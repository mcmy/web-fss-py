from __future__ import annotations

import datetime
import email.utils
import html
import json
import mimetypes
import os
import posixpath
import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, quote, unquote, urlsplit

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__

DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024
MAX_JSON_BODY = 1024 * 1024
READ_BUFFER_SIZE = 64 * 1024


def _utc_now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _normalize_ranges(raw_ranges: Any, file_size: int) -> List[List[int]]:
    ranges: List[List[int]] = []
    if not isinstance(raw_ranges, list):
        return ranges

    for item in raw_ranges:
        if not (isinstance(item, list) or isinstance(item, tuple)):
            continue
        if len(item) != 2:
            continue
        try:
            start = int(item[0])
            end = int(item[1])
        except (TypeError, ValueError):
            continue
        if start < 0:
            start = 0
        if end < 0:
            end = 0
        if start >= end:
            continue
        if start >= file_size:
            continue
        if end > file_size:
            end = file_size
        ranges.append([start, end])

    if not ranges:
        return []

    ranges.sort(key=lambda r: (r[0], r[1]))
    merged: List[List[int]] = [ranges[0]]
    for start, end in ranges[1:]:
        last = merged[-1]
        if start <= last[1]:
            if end > last[1]:
                last[1] = end
        else:
            merged.append([start, end])
    return merged


def _add_range(ranges: Sequence[Sequence[int]], start: int, end: int, file_size: int) -> List[List[int]]:
    raw = [list(item) for item in ranges]
    raw.append([start, end])
    return _normalize_ranges(raw, file_size)


def _count_covered_bytes(ranges: Sequence[Sequence[int]]) -> int:
    total = 0
    for item in ranges:
        if len(item) != 2:
            continue
        start = int(item[0])
        end = int(item[1])
        if end > start:
            total += end - start
    return total


def _range_is_covered(ranges: Sequence[Sequence[int]], start: int, end: int) -> bool:
    for item in ranges:
        if len(item) != 2:
            continue
        if int(item[0]) <= start and int(item[1]) >= end:
            return True
    return False


def _is_upload_complete(ranges: Sequence[Sequence[int]], file_size: int) -> bool:
    if file_size == 0:
        return True
    if not ranges:
        return False
    if len(ranges) != 1:
        return False
    return int(ranges[0][0]) == 0 and int(ranges[0][1]) == file_size


def _parse_single_range_header(range_header: str, file_size: int) -> Tuple[int, int]:
    if not range_header:
        raise ValueError("missing range header")

    unit, sep, value = range_header.partition("=")
    if sep != "=" or unit.strip().lower() != "bytes":
        raise ValueError("unsupported range unit")

    value = value.strip()
    if not value or "," in value:
        raise ValueError("only single range is supported")

    start_text, sep, end_text = value.partition("-")
    if sep != "-":
        raise ValueError("invalid range format")

    if start_text == "":
        if end_text == "":
            raise ValueError("invalid suffix range")
        suffix = int(end_text)
        if suffix <= 0:
            raise ValueError("invalid suffix value")
        if suffix > file_size:
            suffix = file_size
        return file_size - suffix, file_size

    start = int(start_text)
    if start < 0:
        raise ValueError("start cannot be negative")
    if start >= file_size:
        raise ValueError("start out of bounds")

    if end_text == "":
        end = file_size
    else:
        end_inclusive = int(end_text)
        if end_inclusive < start:
            raise ValueError("invalid inclusive end")
        end = end_inclusive + 1

    if end > file_size:
        end = file_size
    if end <= start:
        raise ValueError("invalid range size")

    return start, end


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
    ) -> None:
        self.base_path = Path(base_path).resolve()
        self.enable_upload = enable_upload
        self.default_chunk_size = max(64 * 1024, int(default_chunk_size))
        self.upload_locks = UploadLockManager()
        super().__init__(server_address, handler_class)


class WebFSRequestHandler(BaseHTTPRequestHandler):
    server_version = "web-fs-server/%s" % __version__

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        try:
            if parsed.path.startswith("/.upload/"):
                if not getattr(self.server, "enable_upload", False):
                    self._send_json(
                        {"message": "Upload feature is disabled on this server."},
                        HTTPStatus.FORBIDDEN,
                    )
                    return
                if parsed.path == "/.upload/status":
                    self._handle_upload_status(parsed.query)
                    return
                self._send_json({"message": "Endpoint not found."}, HTTPStatus.NOT_FOUND)
                return
            self._serve_path(head_only=False)
        except Exception as exc:
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

            if parsed.path == "/.upload/check":
                self._handle_upload_check()
                return
            if parsed.path == "/.upload/init":
                self._handle_upload_init()
                return
            if parsed.path == "/.upload/chunk":
                self._handle_upload_chunk(parsed.query)
                return
            if parsed.path == "/.upload/delete":
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
        try:
            entries = sorted(local_dir.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "No permission to list directory")
            return

        title = "File Browser"
        rows: List[str] = []
        can_manage = getattr(self.server, "enable_upload", False)

        if request_path != "/":
            rows.append(
                '<tr>'
                '<td class="name-col"><a class="item-link" href="../">../ (Parent Directory)</a></td>'
                '<td class="size-col">-</td>'
                '<td class="time-col">-</td>'
                '<td class="action-col">-</td>'
                "</tr>"
            )

        for entry in entries:
            name = entry.name
            if name.endswith(".upload"):
                continue

            display_name = name
            link_name = quote(name)
            size_text = "-"
            try:
                stat_info = entry.stat()
                mtime = datetime.datetime.fromtimestamp(stat_info.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            except OSError:
                stat_info = None
                mtime = "-"

            if entry.is_dir():
                display_name += "/"
                link_name += "/"
            elif entry.is_symlink():
                display_name += "@"
            if stat_info and entry.is_file():
                size_text = self._human_size(stat_info.st_size)

            action_html = "-"
            if can_manage:
                action_html = (
                    '<button class="btn btn-danger btn-small delete-btn" type="button" data-delete-name="%s">Delete</button>'
                    % html.escape(name, quote=True)
                )

            rows.append(
                "<tr>"
                "<td class=\"name-col\"><a class=\"item-link\" href=\"%s\">%s</a></td>"
                "<td class=\"size-col\">%s</td>"
                "<td class=\"time-col\">%s</td>"
                "<td class=\"action-col\">%s</td>"
                "</tr>"
                % (
                    html.escape(link_name, quote=True),
                    html.escape(display_name),
                    html.escape(size_text),
                    html.escape(mtime),
                    action_html,
                )
            )

        upload_panel = ""
        if getattr(self.server, "enable_upload", False):
            upload_panel = self._upload_panel_html(request_path)

        page = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title}</title>
<style>
:root {{
  --bg-top: #f7fafc;
  --bg-bottom: #eef3f8;
  --card-bg: #ffffff;
  --card-border: #d8e2ee;
  --text-main: #1a2433;
  --text-muted: #617185;
  --primary: #2f6fed;
  --primary-strong: #2458c4;
  --accent-soft: #e9f0ff;
  --danger: #c12f3a;
  --line: #e4ebf3;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  min-height: 100vh;
  color: var(--text-main);
  font-family: "Segoe UI", "Helvetica Neue", "Noto Sans", sans-serif;
  background: linear-gradient(180deg, var(--bg-top), var(--bg-bottom));
}}
.app-shell {{
  width: min(1040px, calc(100vw - 32px));
  margin: 20px auto 28px;
}}
.app-header {{
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 14px;
  padding: 18px 20px;
  box-shadow: 0 8px 24px rgba(20, 48, 90, 0.06);
}}
.app-header h1 {{
  margin: 0;
  font-size: 26px;
  letter-spacing: 0.2px;
}}
.path-note {{
  margin: 8px 0 0;
  color: var(--text-muted);
  font-size: 14px;
  word-break: break-all;
}}
.path-note code {{
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: var(--accent-soft);
  border-radius: 8px;
  padding: 2px 8px;
  color: #21488a;
}}
.section-gap {{ height: 12px; }}
.list-card {{
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 14px;
  overflow: hidden;
  box-shadow: 0 8px 24px rgba(20, 48, 90, 0.05);
}}
.list-head {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  font-size: 13px;
  color: var(--text-muted);
  border-bottom: 1px solid var(--line);
}}
.table-scroll {{
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}}
table {{
  border-collapse: collapse;
  width: 100%;
  min-width: 660px;
}}
th, td {{
  text-align: left;
  padding: 10px 14px;
  border-bottom: 1px solid var(--line);
}}
th {{
  background: #f6f9fd;
  color: #5a6c82;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.4px;
}}
tbody tr:hover {{
  background: #f8fbff;
}}
.name-col {{ min-width: 220px; width: auto; }}
.size-col {{ width: 130px; color: #42556b; white-space: nowrap; }}
.time-col {{ width: 190px; color: #5f7186; white-space: nowrap; }}
.action-col {{ width: 120px; white-space: nowrap; text-align: right; }}
.item-link {{
  display: inline-block;
  white-space: nowrap;
  color: var(--text-main);
  text-decoration: none;
}}
.item-link:hover {{
  color: #1d4fae;
  text-decoration: underline;
}}
.upload-card {{
  background: var(--card-bg);
  border: 1px solid var(--card-border);
  border-radius: 14px;
  padding: 14px;
  box-shadow: 0 8px 24px rgba(20, 48, 90, 0.05);
}}
.upload-top {{
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}}
.visually-hidden-input {{
  position: absolute;
  width: 1px;
  height: 1px;
  margin: -1px;
  padding: 0;
  overflow: hidden;
  clip: rect(0 0 0 0);
  border: 0;
}}
.picked-file-name {{
  min-width: 220px;
  max-width: min(52vw, 520px);
  padding: 8px 10px;
  border: 1px solid #d3deeb;
  border-radius: 10px;
  background: #f8fbff;
  color: #4a5e75;
  font-size: 13px;
  white-space: nowrap;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}}
.upload-meta {{
  margin-top: 10px;
  display: flex;
  justify-content: space-between;
  gap: 10px;
  color: var(--text-muted);
  font-size: 13px;
  flex-wrap: wrap;
}}
.upload-status {{
  min-height: 20px;
  margin-top: 8px;
  font-size: 14px;
}}
.btn {{
  appearance: none;
  border: 1px solid transparent;
  border-radius: 10px;
  padding: 8px 14px;
  font-size: 14px;
  line-height: 1.2;
  cursor: pointer;
  transition: background .2s ease, color .2s ease, border-color .2s ease, transform .06s ease;
}}
.btn:active {{
  transform: translateY(1px);
}}
.btn:disabled {{
  cursor: not-allowed;
  opacity: 0.6;
  transform: none;
}}
.btn-primary {{
  background: var(--primary);
  color: #fff;
  border-color: var(--primary);
}}
.btn-primary:hover {{
  background: var(--primary-strong);
  border-color: var(--primary-strong);
}}
.btn-secondary {{
  background: #fff;
  color: #315689;
  border-color: #b9cbea;
}}
.btn-secondary:hover {{
  background: #f3f7ff;
}}
.btn-danger {{
  background: #fff;
  color: #b3343f;
  border-color: #e0b5ba;
}}
.btn-danger:hover {{
  background: #fff3f4;
  border-color: #cf8990;
}}
.btn-ghost {{
  background: #fff;
  color: #5a6880;
  border-color: #ccd7e8;
}}
.btn-ghost:hover {{
  background: #f7faff;
}}
.progress-wrap {{
  margin-top: 9px;
  height: 10px;
  border-radius: 999px;
  background: #edf2fa;
  border: 1px solid #d6e0ee;
  overflow: hidden;
}}
.progress-bar {{
  height: 100%;
  width: 0%;
  border-radius: inherit;
  background: linear-gradient(90deg, #2f6fed, #4787ff);
  transition: width .18s ease;
}}
.progress-line {{
  margin-top: 6px;
  color: #5e7188;
  font-size: 12px;
}}
.btn-small {{
  padding: 6px 10px;
  font-size: 12px;
  border-radius: 8px;
}}
.hidden {{
  display: none !important;
}}
.modal {{
  position: fixed;
  inset: 0;
  z-index: 9999;
}}
.modal-backdrop {{
  position: absolute;
  inset: 0;
  background: rgba(16, 31, 56, 0.45);
}}
.modal-panel {{
  position: relative;
  margin: min(12vh, 100px) auto 0;
  width: min(420px, calc(100vw - 26px));
  background: #fff;
  border: 1px solid #d7e1ef;
  border-radius: 14px;
  padding: 16px;
  box-shadow: 0 24px 48px rgba(13, 31, 59, 0.25);
}}
.modal-panel h3 {{
  margin: 0 0 8px;
  font-size: 19px;
}}
.modal-panel p {{
  margin: 0;
  white-space: pre-line;
  color: #4c6078;
  font-size: 14px;
  line-height: 1.5;
}}
.modal-actions {{
  margin-top: 16px;
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  flex-wrap: wrap;
}}
@media (max-width: 760px) {{
  .app-shell {{ width: calc(100vw - 18px); margin: 10px auto 18px; }}
  .app-header {{ padding: 14px; border-radius: 12px; }}
  .app-header h1 {{ font-size: 22px; }}
  th, td {{ padding: 9px 8px; font-size: 13px; }}
  .list-head {{ padding: 10px 10px; }}
  .upload-card {{ padding: 12px; border-radius: 12px; }}
  .upload-top > .btn {{ width: 100%; }}
  .picked-file-name {{ min-width: 100%; max-width: 100%; }}
  .upload-top {{ align-items: stretch; }}
  .modal-actions .btn {{ width: auto; }}
}}
</style>
</head>
<body>
<main class="app-shell">
<section class="app-header">
  <h1>{title}</h1>
  <p class="path-note">Current path: <code>{path_label}</code></p>
</section>
<div class="section-gap"></div>
{upload_panel}
<div class="section-gap"></div>
<section class="list-card">
  <div class="list-head"><span>Items</span><span>{item_count} entries</span></div>
  <div class="table-scroll">
  <table>
  <thead><tr><th class="name-col">Name</th><th class="size-col">Size</th><th class="time-col">Modified</th><th class="action-col">Action</th></tr></thead>
  <tbody>
  {rows}
  </tbody>
  </table>
  </div>
</section>
</main>
</body>
</html>
""".format(
            title=html.escape(title),
            path_label=html.escape(request_path),
            item_count=len(rows),
            rows="\n".join(rows),
            upload_panel=upload_panel,
        )

        payload = page.encode("utf-8", "surrogateescape")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()

        if not head_only:
            self.wfile.write(payload)

    def _upload_panel_html(self, request_path: str) -> str:
        chunk_size = int(getattr(self.server, "default_chunk_size", DEFAULT_CHUNK_SIZE))
        template = """
<section class="upload-card">
  <div class="upload-top">
    <input id="upload-file" class="visually-hidden-input" type="file" />
    <button id="pick-file-btn" class="btn btn-secondary" type="button">Choose File</button>
    <div id="picked-file-name" class="picked-file-name">No file selected</div>
    <button id="upload-btn" class="btn btn-primary" type="button">Upload File</button>
  </div>
  <div class="upload-meta">
    <span>Chunk size: __CHUNK_SIZE__ bytes</span>
    <span id="upload-detail">Ready</span>
  </div>
  <div id="upload-status" class="upload-status">Idle.</div>
  <div class="progress-wrap"><div id="upload-progress-bar" class="progress-bar"></div></div>
  <div class="progress-line"><span id="upload-progress-text">0%</span></div>
</section>

<div id="ui-modal" class="modal hidden" aria-hidden="true">
  <div id="modal-backdrop" class="modal-backdrop"></div>
  <div class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="modal-title">
    <h3 id="modal-title">Confirm Action</h3>
    <p id="modal-message"></p>
    <div class="modal-actions">
      <button id="modal-dismiss" class="btn btn-ghost" type="button">Cancel</button>
      <button id="modal-secondary" class="btn btn-secondary hidden" type="button">Secondary</button>
      <button id="modal-primary" class="btn btn-primary" type="button">OK</button>
    </div>
  </div>
</div>

<script>
(function() {
  const currentDir = __CURRENT_DIR__;
  const chunkSize = __CHUNK_SIZE__;
  const fileInput = document.getElementById('upload-file');
  const pickFileBtn = document.getElementById('pick-file-btn');
  const pickedFileNameEl = document.getElementById('picked-file-name');
  const uploadBtn = document.getElementById('upload-btn');
  const detailEl = document.getElementById('upload-detail');
  const statusEl = document.getElementById('upload-status');
  const progressBarEl = document.getElementById('upload-progress-bar');
  const progressTextEl = document.getElementById('upload-progress-text');
  const deleteButtons = Array.prototype.slice.call(document.querySelectorAll('.delete-btn'));

  const modalEl = document.getElementById('ui-modal');
  const modalBackdropEl = document.getElementById('modal-backdrop');
  const modalTitleEl = document.getElementById('modal-title');
  const modalMessageEl = document.getElementById('modal-message');
  const modalPrimaryEl = document.getElementById('modal-primary');
  const modalSecondaryEl = document.getElementById('modal-secondary');
  const modalDismissEl = document.getElementById('modal-dismiss');
  let modalResolver = null;

  function setStatus(text, isError) {
    statusEl.textContent = text;
    statusEl.style.color = isError ? '#c12f3a' : '#233549';
  }

  function setDetail(text) {
    detailEl.textContent = text;
  }

  function setWriteControlsDisabled(disabled) {
    uploadBtn.disabled = disabled;
    pickFileBtn.disabled = disabled;
    deleteButtons.forEach(function(btn) {
      btn.disabled = disabled;
    });
  }

  function setPickedFileLabel(file) {
    if (!file) {
      pickedFileNameEl.textContent = 'No file selected';
      return;
    }
    pickedFileNameEl.textContent = file.name + ' (' + formatBytes(file.size) + ')';
  }

  function formatBytes(value) {
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let size = Number(value) || 0;
    let index = 0;
    while (size >= 1024 && index < units.length - 1) {
      size = size / 1024;
      index += 1;
    }
    if (index === 0) {
      return String(Math.floor(size)) + ' ' + units[index];
    }
    return size.toFixed(1) + ' ' + units[index];
  }

  function setProgress(done, total) {
    if (total <= 0) {
      progressBarEl.style.width = '0%';
      progressTextEl.textContent = '0%';
      return;
    }
    const percent = Math.max(0, Math.min(100, Math.floor(done / total * 100)));
    progressBarEl.style.width = String(percent) + '%';
    progressTextEl.textContent = String(percent) + '%';
  }

  function rangeCovered(ranges, start, end) {
    for (const item of ranges || []) {
      if (!Array.isArray(item) || item.length !== 2) continue;
      if (item[0] <= start && item[1] >= end) return true;
    }
    return false;
  }

  function closeModal(result) {
    if (!modalResolver) return;
    const resolver = modalResolver;
    modalResolver = null;
    modalEl.classList.add('hidden');
    modalEl.setAttribute('aria-hidden', 'true');
    resolver(result);
  }

  function showChoiceModal(options) {
    if (modalResolver) {
      closeModal('dismiss');
    }
    modalTitleEl.textContent = options.title || 'Confirm Action';
    modalMessageEl.textContent = options.message || '';
    modalPrimaryEl.textContent = options.primaryText || 'OK';
    modalDismissEl.textContent = options.dismissText || 'Cancel';

    if (options.secondaryText) {
      modalSecondaryEl.textContent = options.secondaryText;
      modalSecondaryEl.classList.remove('hidden');
    } else {
      modalSecondaryEl.classList.add('hidden');
    }

    modalEl.classList.remove('hidden');
    modalEl.setAttribute('aria-hidden', 'false');
    return new Promise(function(resolve) {
      modalResolver = resolve;
    });
  }

  modalPrimaryEl.addEventListener('click', function() {
    closeModal('primary');
  });
  modalSecondaryEl.addEventListener('click', function() {
    closeModal('secondary');
  });
  modalDismissEl.addEventListener('click', function() {
    closeModal('dismiss');
  });
  modalBackdropEl.addEventListener('click', function() {
    closeModal('dismiss');
  });
  document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape') {
      closeModal('dismiss');
    }
  });

  async function postJson(url, payload) {
    const response = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    let data = {};
    try {
      data = await response.json();
    } catch (e) {
      data = { message: response.statusText || 'Request failed' };
    }
    if (!response.ok) {
      throw new Error(data.message || ('HTTP ' + response.status));
    }
    return data;
  }

  async function uploadFile() {
    const file = fileInput.files && fileInput.files[0];
    if (!file) {
      setStatus('Please choose a file first.', true);
      setDetail('No file selected');
      setPickedFileLabel(null);
      return;
    }

    setWriteControlsDisabled(true);
    setStatus('Checking existing file...', false);
    setDetail('Target: ' + file.name + ' (' + formatBytes(file.size) + ')');
    setProgress(0, 100);

    try {
      const check = await postJson('/.upload/check', {
        filename: file.name,
        file_size: file.size,
        directory: currentDir,
      });

      let mode = 'overwrite';
      if (check.exists || check.meta_exists) {
        if (check.can_resume) {
          const action = await showChoiceModal({
            title: 'Existing Upload Found',
            message:
              'A resumable upload was found for this file.\\n' +
              'Received: ' + (check.bytes_received || 0) + ' / ' + file.size + ' bytes.\\n\\n' +
              'Choose Resume to continue or Overwrite to restart from zero.',
            primaryText: 'Resume',
            secondaryText: 'Overwrite',
            dismissText: 'Cancel'
          });
          if (action === 'primary') {
            mode = 'resume';
          } else if (action === 'secondary') {
            mode = 'overwrite';
          } else {
            setStatus('Upload canceled.', false);
            setDetail('Canceled by user');
            return;
          }
        } else {
          const action = await showChoiceModal({
            title: 'File Conflict',
            message:
              (check.message || 'A file with the same name already exists.') +
              '\\n\\nOverwrite the file to continue.',
            primaryText: 'Overwrite',
            dismissText: 'Cancel'
          });
          if (action !== 'primary') {
            setStatus('Upload canceled.', false);
            setDetail('Canceled by user');
            return;
          }
          mode = 'overwrite';
        }
      }

      const init = await postJson('/.upload/init', {
        filename: file.name,
        file_size: file.size,
        directory: currentDir,
        mode: mode,
        chunk_size: chunkSize,
      });

      let ranges = init.uploaded_ranges || [];
      let bytesReceived = init.bytes_received || 0;
      setProgress(bytesReceived, file.size);
      setDetail('Mode: ' + mode);

      const totalChunks = Math.ceil(file.size / chunkSize) || 1;
      for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex += 1) {
        const start = chunkIndex * chunkSize;
        const end = Math.min(file.size, start + chunkSize);

        if (mode === 'resume' && rangeCovered(ranges, start, end)) {
          continue;
        }

        const blob = file.slice(start, end);
        const query = new URLSearchParams({
          filename: file.name,
          directory: currentDir,
          start: String(start),
          end: String(end),
          file_size: String(file.size),
        });

        setStatus('Uploading chunk ' + (chunkIndex + 1) + ' / ' + totalChunks + ' ...', false);
        const response = await fetch('/.upload/chunk?' + query.toString(), {
          method: 'POST',
          body: blob,
        });

        let chunkData = {};
        try {
          chunkData = await response.json();
        } catch (e) {
          chunkData = { message: response.statusText || 'Chunk upload failed' };
        }

        if (!response.ok) {
          throw new Error(chunkData.message || ('Chunk failed: HTTP ' + response.status));
        }

        ranges = chunkData.uploaded_ranges || ranges;
        bytesReceived = chunkData.bytes_received || bytesReceived;
        setProgress(bytesReceived, file.size);
      }

      setStatus('Upload complete. Refreshing file list...', false);
      setDetail('Done');
      setProgress(file.size, file.size);
      setTimeout(function() { window.location.reload(); }, 550);
    } catch (err) {
      setStatus(err && err.message ? err.message : String(err), true);
      setDetail('Upload failed');
    } finally {
      setWriteControlsDisabled(false);
    }
  }

  async function deleteEntry(entryName) {
    const action = await showChoiceModal({
      title: 'Delete Entry',
      message:
        'You are about to delete: ' + entryName + '\\n\\n' +
        'Folders will be removed recursively and cannot be restored.',
      primaryText: 'Delete',
      dismissText: 'Cancel'
    });

    if (action !== 'primary') {
      return;
    }

    setWriteControlsDisabled(true);
    setStatus('Deleting ' + entryName + ' ...', false);
    setDetail('Delete in progress');

    try {
      const data = await postJson('/.upload/delete', {
        directory: currentDir,
        filename: entryName
      });
      setStatus(data.message || ('Deleted ' + entryName), false);
      setDetail('Delete completed');
      setTimeout(function() { window.location.reload(); }, 350);
    } catch (err) {
      setStatus(err && err.message ? err.message : String(err), true);
      setDetail('Delete failed');
      setWriteControlsDisabled(false);
    }
  }

  pickFileBtn.addEventListener('click', function() {
    fileInput.click();
  });

  fileInput.addEventListener('change', function() {
    const file = fileInput.files && fileInput.files[0];
    setPickedFileLabel(file || null);
    if (file) {
      setDetail('Selected: ' + formatBytes(file.size));
      setStatus('Ready to upload.', false);
    }
  });

  uploadBtn.addEventListener('click', function() {
    uploadFile();
  });

  deleteButtons.forEach(function(button) {
    button.addEventListener('click', function() {
      const entryName = button.getAttribute('data-delete-name');
      if (!entryName) {
        return;
      }
      deleteEntry(entryName);
    });
  });
})();
</script>
"""
        return template.replace("__CURRENT_DIR__", json.dumps(request_path)).replace("__CHUNK_SIZE__", str(chunk_size))

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
        }

        if not exists and not meta_exists:
            return info

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
) -> None:
    server = WebFSServer(
        (bind, port),
        WebFSRequestHandler,
        base_path=directory,
        enable_upload=enable_upload,
        default_chunk_size=chunk_size,
    )

    host, actual_port = server.server_address[:2]
    root = server.base_path
    print("Serving HTTP on %s port %s (root: %s)" % (host, actual_port, root))
    if enable_upload:
        print("Upload API enabled: /.upload/check, /.upload/init, /.upload/chunk")
    else:
        print("Upload API disabled")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()
