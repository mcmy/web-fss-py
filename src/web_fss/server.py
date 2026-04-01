from __future__ import annotations

import datetime
import email.utils
import html
import json
import mimetypes
import os
import posixpath
import re
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, quote, unquote, urlsplit

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__
from .ranges import (
    _add_range,
    _count_covered_bytes,
    _is_upload_complete,
    _normalize_ranges,
    _parse_single_range_header,
    _range_is_covered,
)
from .html_pages import render_directory_page, render_upload_panel
from .startup_urls import _build_startup_urls

DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024
MAX_JSON_BODY = 1024 * 1024
READ_BUFFER_SIZE = 64 * 1024
LIST_API_PATHS = ("/.api/list", "/.api/list/")


def _utc_now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


class UploadLockManager:
    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._locks: Dict[str, threading.Lock] = {}

    def get_lock(self, path: Path) -> threading.Lock:
        key = str(path)
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock


class WebFSServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: Tuple[str, int],
        handler_class: Any,
        base_path: str,
        enable_upload: bool = True,
        default_chunk_size: int = DEFAULT_CHUNK_SIZE,
        show_hidden: bool = False,
        serve_index_html: bool = False,
    ) -> None:
        self.base_path = Path(base_path).resolve()
        self.enable_upload = enable_upload
        self.default_chunk_size = max(64 * 1024, int(default_chunk_size))
        self.show_hidden = bool(show_hidden)
        self.serve_index_html = bool(serve_index_html)
        self.upload_locks = UploadLockManager()
        super().__init__(server_address, handler_class)


class WebFSRequestHandler(BaseHTTPRequestHandler):
    server_version = "web-fss/%s" % __version__

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        try:
            if parsed.path in LIST_API_PATHS:
                self._handle_list_entries(parsed.query)
                return
            if parsed.path.startswith("/.upload/"):
                if not getattr(self.server, "enable_upload", False):
                    self._send_json(
                        {"message": "Upload feature is disabled on this server."},
                        HTTPStatus.FORBIDDEN,
                    )
                    return
                if parsed.path in ("/.upload/status", "/.upload/status/"):
                    self._handle_upload_status(parsed.query)
                    return
                if parsed.path in ("/.upload/delete", "/.upload/delete/"):
                    self._send_json(
                        {"message": "Use POST or DELETE method for this endpoint."},
                        HTTPStatus.METHOD_NOT_ALLOWED,
                    )
                    return
                self._send_json({"message": "Endpoint not found."}, HTTPStatus.NOT_FOUND)
                return
            self._serve_path(head_only=False)
        except Exception as exc:
            if parsed.path in LIST_API_PATHS:
                self._send_json(
                    {
                        "message": self._format_exception_message(
                            "List request failed", exc
                        )
                    },
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            if parsed.path.startswith("/.upload/"):
                self._send_json(
                    {
                        "message": self._format_exception_message(
                            "Upload request failed", exc
                        )
                    },
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return
            self._send_friendly_html_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "Request Failed",
                self._format_exception_message("Unable to render this page", exc),
            )

    def do_HEAD(self) -> None:
        try:
            self._serve_path(head_only=True)
        except Exception:
            self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
            self.end_headers()

    def do_POST(self) -> None:
        parsed = urlsplit(self.path)
        try:
            if not getattr(self.server, "enable_upload", False):
                self._send_json(
                    {"message": "Upload feature is disabled on this server."},
                    HTTPStatus.FORBIDDEN,
                )
                return

            if parsed.path in ("/.upload/check", "/.upload/check/"):
                self._handle_upload_check()
                return
            if parsed.path in ("/.upload/init", "/.upload/init/"):
                self._handle_upload_init()
                return
            if parsed.path in ("/.upload/chunk", "/.upload/chunk/"):
                self._handle_upload_chunk(parsed.query)
                return
            if parsed.path in ("/.upload/delete", "/.upload/delete/"):
                self._handle_delete_entry()
                return

            self._send_json({"message": "Endpoint not found."}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json(
                {
                    "message": self._format_exception_message(
                        "Upload request failed", exc
                    )
                },
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def do_DELETE(self) -> None:
        parsed = urlsplit(self.path)
        try:
            if not getattr(self.server, "enable_upload", False):
                self._send_json(
                    {"message": "Upload feature is disabled on this server."},
                    HTTPStatus.FORBIDDEN,
                )
                return
            if parsed.path in ("/.upload/delete", "/.upload/delete/"):
                self._handle_delete_entry()
                return
            self._send_json({"message": "Endpoint not found."}, HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json(
                {
                    "message": self._format_exception_message(
                        "Delete request failed", exc
                    )
                },
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _serve_path(self, head_only: bool) -> None:
        parsed = urlsplit(self.path)
        request_path = parsed.path or "/"

        try:
            local_path = self._resolve_url_path(request_path)
        except PermissionError:
            self.send_error(HTTPStatus.FORBIDDEN, "Forbidden path")
            return

        if local_path.is_dir():
            if not request_path.endswith("/"):
                redirect_target = request_path + "/"
                if parsed.query:
                    redirect_target += "?" + parsed.query
                self.send_response(HTTPStatus.MOVED_PERMANENTLY)
                self.send_header("Location", redirect_target)
                self.end_headers()
                return

            if getattr(self.server, "serve_index_html", False):
                for index_name in ("index.html", "index.htm"):
                    index_path = local_path / index_name
                    if index_path.is_file():
                        self._send_file(index_path, head_only)
                        return

            self._send_directory_listing(local_path, request_path, head_only)
            return

        if not local_path.exists() or not local_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return

        self._send_file(local_path, head_only)

    def _send_file(self, file_path: Path, head_only: bool) -> None:
        stat_info = file_path.stat()
        file_size = stat_info.st_size

        range_header = self.headers.get("Range")
        range_start = 0
        range_end = file_size
        status = HTTPStatus.OK

        if range_header:
            try:
                range_start, range_end = _parse_single_range_header(range_header, file_size)
                status = HTTPStatus.PARTIAL_CONTENT
            except Exception:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", "bytes */%d" % file_size)
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                return

        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        content_length = range_end - range_start

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Last-Modified", email.utils.formatdate(stat_info.st_mtime, usegmt=True))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header(
                "Content-Range",
                "bytes %d-%d/%d" % (range_start, range_end - 1, file_size),
            )
        self.end_headers()

        if head_only:
            return

        with file_path.open("rb") as fp:
            fp.seek(range_start)
            remaining = content_length
            while remaining > 0:
                chunk = fp.read(min(READ_BUFFER_SIZE, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


    def _send_directory_listing(self, local_dir: Path, request_path: str, head_only: bool) -> None:
        # Keep behavior consistent with previous implementation: reject unreadable directories.
        try:
            iterator = local_dir.iterdir()
            next(iterator, None)
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "No permission to list directory")
            return

        title = "File Browser"
        upload_panel = ""
        if getattr(self.server, "enable_upload", False):
            upload_panel = self._upload_panel_html(request_path)

        page = render_directory_page(title=title, request_path=request_path, upload_panel=upload_panel)
        payload = page.encode("utf-8", "surrogateescape")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()

        if not head_only:
            self.wfile.write(payload)

    def _handle_list_entries(self, query: str) -> None:
        params = parse_qs(query)
        directory = self._first_query_value(params, "directory")
        if directory is None:
            directory = self._first_query_value(params, "path")
        if directory is None:
            directory = "/"

        try:
            target_dir = self._resolve_upload_directory(directory)
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            self._send_json({"message": "invalid directory"}, HTTPStatus.BAD_REQUEST)
            return

        request_path = self._request_path_for_local_dir(target_dir)
        try:
            entries = self._build_directory_entries(target_dir, request_path)
        except OSError:
            self._send_json(
                {"message": "No permission to list directory"},
                HTTPStatus.NOT_FOUND,
            )
            return

        self._send_json(
            {
                "path": request_path,
                "item_count": len(entries),
                "entries": entries,
            },
            HTTPStatus.OK,
        )

    def _build_directory_entries(self, local_dir: Path, request_path: str) -> List[Dict[str, Any]]:
        entries = sorted(local_dir.iterdir(), key=lambda p: p.name.lower())
        out: List[Dict[str, Any]] = []
        can_manage = getattr(self.server, "enable_upload", False)
        show_hidden = getattr(self.server, "show_hidden", False)

        if request_path != "/":
            out.append(
                {
                    "name": "..",
                    "display_name": "../ (Parent Directory)",
                    "href": "../",
                    "size": "-",
                    "modified": "-",
                    "is_parent": True,
                    "can_delete": False,
                }
            )

        for entry in entries:
            name = entry.name
            if (not show_hidden) and name.startswith("."):
                continue

            display_name = name
            link_name = quote(name)
            size_text = "-"

            try:
                stat_info = entry.stat()
                mtime = datetime.datetime.fromtimestamp(stat_info.st_mtime).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            except OSError:
                stat_info = None
                mtime = "-"

            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False

            try:
                is_symlink = entry.is_symlink()
            except OSError:
                is_symlink = False

            try:
                is_file = entry.is_file()
            except OSError:
                is_file = False

            if is_dir:
                display_name += "/"
                link_name += "/"
            elif is_symlink:
                display_name += "@"

            if stat_info and is_file:
                size_text = self._human_size(stat_info.st_size)

            out.append(
                {
                    "name": name,
                    "display_name": display_name,
                    "href": link_name,
                    "size": size_text,
                    "modified": mtime,
                    "is_parent": False,
                    "can_delete": can_manage,
                }
            )

        return out

    def _request_path_for_local_dir(self, local_dir: Path) -> str:
        base_path = getattr(self.server, "base_path")
        resolved = local_dir.resolve(strict=False)
        if not _is_relative_to(resolved, base_path):
            raise PermissionError("path escapes root")

        relative = resolved.relative_to(base_path)
        if not relative.parts:
            return "/"
        return "/" + "/".join(quote(part) for part in relative.parts) + "/"


    def _upload_panel_html(self, request_path: str) -> str:
        chunk_size = int(getattr(self.server, "default_chunk_size", DEFAULT_CHUNK_SIZE))
        return render_upload_panel(request_path=request_path, chunk_size=chunk_size)

    def _handle_upload_status(self, query: str) -> None:
        params = parse_qs(query)
        filename = self._first_query_value(params, "filename")
        file_size_text = self._first_query_value(params, "file_size")
        directory = self._first_query_value(params, "directory") or "/"

        try:
            file_size = int(file_size_text)
        except (TypeError, ValueError):
            self._send_json({"message": "invalid file_size"}, HTTPStatus.BAD_REQUEST)
            return

        if not self._valid_upload_filename(filename):
            self._send_json({"message": "invalid filename"}, HTTPStatus.BAD_REQUEST)
            return

        try:
            target_dir = self._resolve_upload_directory(directory)
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            self._send_json({"message": "invalid directory"}, HTTPStatus.BAD_REQUEST)
            return

        info = self._build_upload_status(target_dir, filename, file_size)
        self._send_json(info, HTTPStatus.OK)

    def _handle_upload_check(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        filename = str(payload.get("filename") or "")
        directory = str(payload.get("directory") or "/")
        file_size = payload.get("file_size")

        try:
            file_size_int = int(file_size)
        except (TypeError, ValueError):
            self._send_json({"message": "invalid file_size"}, HTTPStatus.BAD_REQUEST)
            return

        if file_size_int < 0:
            self._send_json({"message": "file_size cannot be negative"}, HTTPStatus.BAD_REQUEST)
            return

        if not self._valid_upload_filename(filename):
            self._send_json({"message": "invalid filename"}, HTTPStatus.BAD_REQUEST)
            return

        try:
            target_dir = self._resolve_upload_directory(directory)
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            self._send_json({"message": "invalid directory"}, HTTPStatus.BAD_REQUEST)
            return

        info = self._build_upload_status(target_dir, filename, file_size_int)
        self._send_json(info, HTTPStatus.OK)

    def _handle_upload_init(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        filename = str(payload.get("filename") or "")
        directory = str(payload.get("directory") or "/")
        mode = str(payload.get("mode") or "overwrite").lower()

        try:
            file_size = int(payload.get("file_size"))
        except (TypeError, ValueError):
            self._send_json({"message": "invalid file_size"}, HTTPStatus.BAD_REQUEST)
            return

        try:
            chunk_size = int(payload.get("chunk_size") or getattr(self.server, "default_chunk_size", DEFAULT_CHUNK_SIZE))
        except (TypeError, ValueError):
            chunk_size = getattr(self.server, "default_chunk_size", DEFAULT_CHUNK_SIZE)

        if chunk_size < 64 * 1024:
            chunk_size = 64 * 1024

        if file_size < 0:
            self._send_json({"message": "file_size cannot be negative"}, HTTPStatus.BAD_REQUEST)
            return

        if mode not in ("overwrite", "resume"):
            self._send_json({"message": "mode must be overwrite or resume"}, HTTPStatus.BAD_REQUEST)
            return

        if not self._valid_upload_filename(filename):
            self._send_json({"message": "invalid filename"}, HTTPStatus.BAD_REQUEST)
            return

        try:
            target_dir = self._resolve_upload_directory(directory)
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            self._send_json({"message": "invalid directory"}, HTTPStatus.BAD_REQUEST)
            return

        target_file = target_dir / filename
        meta_file = self._meta_path_for(target_file)

        lock = getattr(self.server, "upload_locks").get_lock(target_file)
        with lock:
            if mode == "overwrite":
                with target_file.open("wb") as fp:
                    fp.truncate(file_size)

                meta = {
                    "version": 1,
                    "filename": filename,
                    "file_size": file_size,
                    "chunk_size": chunk_size,
                    "uploaded_ranges": [],
                    "bytes_received": 0,
                    "completed": file_size == 0,
                    "created_at": _utc_now_iso(),
                    "updated_at": _utc_now_iso(),
                }
                self._write_meta(meta_file, meta)
                if meta["completed"]:
                    self._remove_meta_file(meta_file)
            else:
                if (not target_file.exists()) or (not meta_file.exists()):
                    self._send_json(
                        {
                            "message": "cannot resume: data file or .upload metadata is missing; choose overwrite"
                        },
                        HTTPStatus.CONFLICT,
                    )
                    return

                meta = self._read_meta(meta_file)
                if not meta:
                    self._send_json({"message": "cannot resume: invalid .upload metadata"}, HTTPStatus.CONFLICT)
                    return

                if int(meta.get("file_size", -1)) != file_size:
                    self._send_json(
                        {
                            "message": "cannot resume: file_size mismatch (possible file changed), choose overwrite"
                        },
                        HTTPStatus.CONFLICT,
                    )
                    return

                on_disk = target_file.stat().st_size
                if on_disk != file_size:
                    self._send_json(
                        {
                            "message": "cannot resume: existing file size mismatch (possible file changed), choose overwrite"
                        },
                        HTTPStatus.CONFLICT,
                    )
                    return

                ranges = _normalize_ranges(meta.get("uploaded_ranges"), file_size)
                bytes_received = _count_covered_bytes(ranges)
                meta["uploaded_ranges"] = ranges
                meta["bytes_received"] = bytes_received
                meta["completed"] = _is_upload_complete(ranges, file_size)
                meta["updated_at"] = _utc_now_iso()
                self._write_meta(meta_file, meta)
                if meta["completed"]:
                    self._remove_meta_file(meta_file)

        out_ranges = _normalize_ranges(meta.get("uploaded_ranges"), file_size)
        out_bytes = _count_covered_bytes(out_ranges)
        self._send_json(
            {
                "message": "upload initialized",
                "mode": mode,
                "uploaded_ranges": out_ranges,
                "bytes_received": out_bytes,
                "completed": _is_upload_complete(out_ranges, file_size),
            },
            HTTPStatus.OK,
        )

    def _handle_upload_chunk(self, query: str) -> None:
        params = parse_qs(query)
        filename = self._first_query_value(params, "filename")
        directory = self._first_query_value(params, "directory") or "/"
        start_text = self._first_query_value(params, "start")
        end_text = self._first_query_value(params, "end")
        file_size_text = self._first_query_value(params, "file_size")

        if not self._valid_upload_filename(filename):
            self._send_json({"message": "invalid filename"}, HTTPStatus.BAD_REQUEST)
            return

        try:
            start = int(start_text)
            end = int(end_text)
            file_size = int(file_size_text)
        except (TypeError, ValueError):
            self._send_json({"message": "invalid chunk params"}, HTTPStatus.BAD_REQUEST)
            return

        if file_size < 0 or start < 0 or end < 0 or end < start or end > file_size:
            self._send_json({"message": "invalid chunk bounds"}, HTTPStatus.BAD_REQUEST)
            return

        length_header = self.headers.get("Content-Length")
        if length_header is None:
            self._send_json({"message": "missing Content-Length"}, HTTPStatus.BAD_REQUEST)
            return

        try:
            body_size = int(length_header)
        except ValueError:
            self._send_json({"message": "invalid Content-Length"}, HTTPStatus.BAD_REQUEST)
            return

        if body_size < 0:
            self._send_json({"message": "invalid chunk size"}, HTTPStatus.BAD_REQUEST)
            return

        body = self.rfile.read(body_size)
        if len(body) != body_size:
            self._send_json({"message": "incomplete request body"}, HTTPStatus.BAD_REQUEST)
            return

        expected_size = end - start
        if expected_size != body_size:
            self._send_json(
                {"message": "chunk size mismatch: expected %d got %d" % (expected_size, body_size)},
                HTTPStatus.BAD_REQUEST,
            )
            return

        try:
            target_dir = self._resolve_upload_directory(directory)
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            self._send_json({"message": "invalid directory"}, HTTPStatus.BAD_REQUEST)
            return

        target_file = target_dir / filename
        meta_file = self._meta_path_for(target_file)

        lock = getattr(self.server, "upload_locks").get_lock(target_file)
        with lock:
            if (not target_file.exists()) or (not meta_file.exists()):
                self._send_json(
                    {
                        "message": "upload is not initialized; call /.upload/init first"
                    },
                    HTTPStatus.CONFLICT,
                )
                return

            meta = self._read_meta(meta_file)
            if not meta:
                self._send_json({"message": "invalid .upload metadata"}, HTTPStatus.CONFLICT)
                return

            meta_file_size = int(meta.get("file_size", -1))
            if meta_file_size != file_size:
                self._send_json(
                    {
                        "message": "file_size mismatch with .upload metadata; choose overwrite"
                    },
                    HTTPStatus.CONFLICT,
                )
                return

            on_disk = target_file.stat().st_size
            if on_disk != file_size:
                self._send_json(
                    {
                        "message": "existing file size mismatch (possible external modification); choose overwrite"
                    },
                    HTTPStatus.CONFLICT,
                )
                return

            ranges = _normalize_ranges(meta.get("uploaded_ranges"), file_size)

            if not _range_is_covered(ranges, start, end):
                with target_file.open("r+b") as fp:
                    fp.seek(start)
                    fp.write(body)

                ranges = _add_range(ranges, start, end, file_size)

            bytes_received = _count_covered_bytes(ranges)
            completed = _is_upload_complete(ranges, file_size)

            meta["uploaded_ranges"] = ranges
            meta["bytes_received"] = bytes_received
            meta["completed"] = completed
            meta["updated_at"] = _utc_now_iso()
            self._write_meta(meta_file, meta)
            if completed:
                self._remove_meta_file(meta_file)

        self._send_json(
            {
                "message": "chunk stored",
                "uploaded_ranges": ranges,
                "bytes_received": bytes_received,
                "completed": completed,
            },
            HTTPStatus.OK,
        )

    def _handle_delete_entry(self) -> None:
        payload = self._read_json_body()
        if payload is None:
            return

        filename = str(payload.get("filename") or "")
        directory = str(payload.get("directory") or "/")

        if not self._valid_upload_filename(filename):
            self._send_json({"message": "Invalid filename."}, HTTPStatus.BAD_REQUEST)
            return

        try:
            target_dir = self._resolve_upload_directory(directory)
        except (PermissionError, FileNotFoundError, NotADirectoryError):
            self._send_json({"message": "Invalid directory."}, HTTPStatus.BAD_REQUEST)
            return

        target_path = target_dir / filename
        if not target_path.exists():
            self._send_json({"message": "Target does not exist."}, HTTPStatus.NOT_FOUND)
            return

        lock = getattr(self.server, "upload_locks").get_lock(target_path)
        with lock:
            try:
                if target_path.is_dir() and not target_path.is_symlink():
                    shutil.rmtree(str(target_path))
                    deleted_type = "directory"
                else:
                    target_path.unlink()
                    deleted_type = "file"
                    self._remove_meta_file(self._meta_path_for(target_path))
            except Exception as exc:
                self._send_json(
                    {
                        "message": self._format_exception_message(
                            "Delete failed", exc
                        )
                    },
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
                return

        self._send_json(
            {
                "message": "Deleted %s: %s" % (deleted_type, filename),
                "deleted_type": deleted_type,
                "name": filename,
            },
            HTTPStatus.OK,
        )

    def _build_upload_status(self, target_dir: Path, filename: str, file_size: int) -> Dict[str, Any]:
        target_file = target_dir / filename
        meta_file = self._meta_path_for(target_file)

        exists = target_file.exists()
        meta_exists = meta_file.exists()

        info: Dict[str, Any] = {
            "exists": exists,
            "meta_exists": meta_exists,
            "can_resume": False,
            "bytes_received": 0,
            "uploaded_ranges": [],
            "message": "",
            "rename_suggestion": "",
        }

        if not exists and not meta_exists:
            return info

        info["rename_suggestion"] = self._next_renamed_filename(target_dir, filename)

        if exists and not meta_exists:
            info["message"] = "same name file exists but no .upload metadata; resume unavailable"
            return info

        meta = self._read_meta(meta_file)
        if not meta:
            info["message"] = "invalid .upload metadata; resume unavailable"
            return info

        meta_size = int(meta.get("file_size", -1))
        if meta_size != file_size:
            info["message"] = "file_size mismatch with .upload metadata; resume unavailable"
            return info

        if not exists:
            info["message"] = "data file missing but .upload exists; resume unavailable"
            return info

        on_disk = target_file.stat().st_size
        if on_disk != file_size:
            info["message"] = "existing file size mismatch; possible external modification; resume unavailable"
            return info

        ranges = _normalize_ranges(meta.get("uploaded_ranges"), file_size)
        bytes_received = _count_covered_bytes(ranges)

        info["uploaded_ranges"] = ranges
        info["bytes_received"] = bytes_received

        completed = _is_upload_complete(ranges, file_size)
        if completed:
            info["message"] = "same file appears fully uploaded already; use overwrite to re-upload"
            return info

        info["can_resume"] = True
        return info

    @staticmethod
    def _split_filename_suffix(filename: str) -> Tuple[str, str]:
        dot_index = filename.rfind(".")
        if dot_index <= 0:
            return filename, ""
        return filename[:dot_index], filename[dot_index:]

    @staticmethod
    def _split_rename_index(stem: str) -> Tuple[str, int]:
        matched = re.match(r"^(.*) \((\d+)\)$", stem)
        if not matched:
            return stem, 1
        base_name = matched.group(1)
        index = int(matched.group(2)) + 1
        return base_name, index

    def _next_renamed_filename(self, target_dir: Path, filename: str) -> str:
        stem, ext = self._split_filename_suffix(filename)
        base_name, next_index = self._split_rename_index(stem)
        max_index = max(0, next_index - 1)

        # Follow the largest existing suffix, instead of filling gaps like (1).
        pattern = re.compile(
            r"^%s \((\d+)\)%s$" % (re.escape(base_name), re.escape(ext))
        )
        try:
            entries = target_dir.iterdir()
        except OSError:
            entries = ()

        for entry in entries:
            entry_name = entry.name
            logical_name = (
                entry_name[:-7] if entry_name.endswith(".upload") else entry_name
            )
            matched = pattern.match(logical_name)
            if not matched:
                continue
            index = int(matched.group(1))
            if index > max_index:
                max_index = index

        next_index = max_index + 1

        max_attempts = 100000
        for _ in range(max_attempts):
            candidate = "%s (%d)%s" % (base_name, next_index, ext)
            candidate_path = target_dir / candidate
            candidate_meta = self._meta_path_for(candidate_path)
            if (not candidate_path.exists()) and (not candidate_meta.exists()):
                return candidate
            next_index += 1

        raise RuntimeError("unable to generate renamed filename")

    def _resolve_upload_directory(self, directory: str) -> Path:
        path = directory.strip() or "/"
        if not path.startswith("/"):
            path = "/" + path

        resolved = self._resolve_url_path(path)
        if not resolved.exists():
            raise FileNotFoundError(path)
        if not resolved.is_dir():
            raise NotADirectoryError(path)
        return resolved

    def _resolve_url_path(self, request_path: str) -> Path:
        cleaned = unquote(request_path)
        normalized = posixpath.normpath(cleaned)

        parts = [part for part in normalized.split("/") if part and part not in (".", "..")]
        candidate = getattr(self.server, "base_path")
        for part in parts:
            candidate = candidate / part

        resolved = candidate.resolve(strict=False)
        base_path = getattr(self.server, "base_path")
        if not _is_relative_to(resolved, base_path):
            raise PermissionError("path escapes root")
        return resolved

    def _read_json_body(self) -> Optional[Dict[str, Any]]:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            self._send_json({"message": "missing Content-Length"}, HTTPStatus.BAD_REQUEST)
            return None

        try:
            length = int(length_header)
        except ValueError:
            self._send_json({"message": "invalid Content-Length"}, HTTPStatus.BAD_REQUEST)
            return None

        if length < 0 or length > MAX_JSON_BODY:
            self._send_json({"message": "request body too large"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return None

        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_json({"message": "invalid json payload"}, HTTPStatus.BAD_REQUEST)
            return None

        if not isinstance(data, dict):
            self._send_json({"message": "json payload must be an object"}, HTTPStatus.BAD_REQUEST)
            return None

        return data

    def _send_json(self, payload: Dict[str, Any], status: HTTPStatus) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _format_exception_message(prefix: str, exc: Exception) -> str:
        raw_message = str(exc).strip().replace("\n", " ").replace("\r", " ")
        if not raw_message:
            raw_message = exc.__class__.__name__
        return "%s: %s" % (prefix, raw_message)

    def _send_friendly_html_error(self, status: HTTPStatus, title: str, message: str) -> None:
        safe_title = html.escape(title)
        safe_message = html.escape(message)
        content = (
            "<!DOCTYPE html>"
            "<html><head><meta charset='utf-8' /><meta name='viewport' content='width=device-width, initial-scale=1' />"
            "<title>%s</title>"
            "<style>"
            "body{margin:0;font-family:'Segoe UI','Helvetica Neue','Noto Sans',sans-serif;background:#f5f8fc;color:#1e2a3d;}"
            ".wrap{max-width:760px;margin:48px auto;padding:0 14px;}"
            ".card{background:#fff;border:1px solid #d8e1ed;border-radius:14px;padding:18px 20px;box-shadow:0 10px 25px rgba(25,46,80,.08);}"
            "h1{margin:0 0 10px;font-size:22px;}p{margin:0;color:#52657d;line-height:1.5;word-break:break-word;}"
            "</style></head><body><div class='wrap'><div class='card'><h1>%s</h1><p>%s</p></div></div></body></html>"
        ) % (safe_title, safe_title, safe_message)
        body = content.encode("utf-8", "replace")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _meta_path_for(self, target_file: Path) -> Path:
        return target_file.with_name(target_file.name + ".upload")

    @staticmethod
    def _remove_meta_file(meta_path: Path) -> None:
        try:
            meta_path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            return

    def _read_meta(self, meta_path: Path) -> Optional[Dict[str, Any]]:
        try:
            with meta_path.open("r", encoding="utf-8") as fp:
                data = json.load(fp)
            if not isinstance(data, dict):
                return None
            return data
        except Exception:
            return None

    def _write_meta(self, meta_path: Path, payload: Dict[str, Any]) -> None:
        temp_path = meta_path.with_name(meta_path.name + ".tmp")
        with temp_path.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(str(temp_path), str(meta_path))

    def _valid_upload_filename(self, filename: str) -> bool:
        if not filename:
            return False
        if filename in (".", ".."):
            return False
        if "/" in filename or "\\" in filename:
            return False
        if filename.endswith(".upload"):
            return False
        return True

    @staticmethod
    def _first_query_value(query: Dict[str, List[str]], key: str) -> Optional[str]:
        values = query.get(key)
        if not values:
            return None
        return values[0]

    @staticmethod
    def _human_size(size: int) -> str:
        if size < 1024:
            return "%d B" % size
        units = ["KB", "MB", "GB", "TB"]
        value = float(size)
        for unit in units:
            value /= 1024.0
            if value < 1024.0:
                return "%.1f %s" % (value, unit)
        return "%.1f PB" % (value / 1024.0)


def serve(
    bind: str = "0.0.0.0",
    port: int = 8000,
    directory: str = ".",
    enable_upload: bool = True,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    show_hidden: bool = False,
    serve_index_html: bool = False,
) -> None:
    server = WebFSServer(
        (bind, port),
        WebFSRequestHandler,
        base_path=directory,
        enable_upload=enable_upload,
        default_chunk_size=chunk_size,
        show_hidden=show_hidden,
        serve_index_html=serve_index_html,
    )

    host, actual_port = server.server_address[:2]
    root = server.base_path
    print("Serving HTTP on %s port %s (root: %s)" % (host, actual_port, root))
    print("Available URLs:")
    for url in _build_startup_urls(bind, int(actual_port)):
        print("  - %s" % url)
    print("List API enabled: /.api/list")
    if show_hidden:
        print("Hidden files: visible")
    else:
        print("Hidden files: hidden (use --show-hidden to display)")
    if serve_index_html:
        print("Directory index file: auto-serve enabled (index.html/index.htm)")
    else:
        print("Directory index file: auto-serve disabled (always show file manager)")
    if enable_upload:
        print("Upload API enabled: /.upload/check, /.upload/init, /.upload/chunk, /.upload/delete")
    else:
        print("Upload API disabled")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()
