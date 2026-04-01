import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from web_fs_server.server import WebFSRequestHandler, WebFSServer  # noqa: E402


class ServerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.server = WebFSServer(
            ("127.0.0.1", 0),
            WebFSRequestHandler,
            base_path=self.temp_dir.name,
            enable_upload=True,
            default_chunk_size=4,
        )
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        self.host, self.port = self.server.server_address[:2]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp_dir.cleanup()

    def _post_json(self, path: str, payload: dict):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        body = json.dumps(payload).encode("utf-8")
        conn.request("POST", path, body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, json.loads(data.decode("utf-8"))

    def _post_chunk(self, path: str, body: bytes):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        conn.request("POST", path, body=body)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, json.loads(data.decode("utf-8"))

    def _get_json(self, path: str):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, json.loads(data.decode("utf-8"))

    def test_upload_resume_and_range_download(self) -> None:
        status_code, check = self._post_json(
            "/.upload/check",
            {"filename": "a.bin", "file_size": 6, "directory": "/"},
        )
        self.assertEqual(status_code, 200)
        self.assertFalse(check["exists"])
        self.assertFalse(check["can_resume"])

        status_code, init = self._post_json(
            "/.upload/init",
            {
                "filename": "a.bin",
                "file_size": 6,
                "directory": "/",
                "mode": "overwrite",
                "chunk_size": 3,
            },
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(init["bytes_received"], 0)

        status_code, chunk = self._post_chunk(
            "/.upload/chunk?filename=a.bin&directory=/&start=0&end=3&file_size=6",
            b"abc",
        )
        self.assertEqual(status_code, 200)
        self.assertEqual(chunk["bytes_received"], 3)

        status_code, chunk = self._post_chunk(
            "/.upload/chunk?filename=a.bin&directory=/&start=3&end=6&file_size=6",
            b"def",
        )
        self.assertEqual(status_code, 200)
        self.assertTrue(chunk["completed"])

        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        conn.request("GET", "/a.bin", headers={"Range": "bytes=1-4"})
        resp = conn.getresponse()
        body = resp.read()
        conn.close()

        self.assertEqual(resp.status, 206)
        self.assertEqual(body, b"bcde")

        file_path = Path(self.temp_dir.name) / "a.bin"
        meta_path = Path(self.temp_dir.name) / "a.bin.upload"
        self.assertTrue(file_path.exists())
        self.assertEqual(file_path.read_bytes(), b"abcdef")
        self.assertFalse(meta_path.exists())

    def test_delete_entry(self) -> None:
        base = Path(self.temp_dir.name)
        nested_dir = base / "to-delete"
        nested_dir.mkdir()
        (nested_dir / "child.txt").write_text("hello", encoding="utf-8")

        status_code, payload = self._post_json(
            "/.upload/delete",
            {"filename": "to-delete", "directory": "/"},
        )
        self.assertEqual(status_code, 200)
        self.assertIn("Deleted directory", payload["message"])
        self.assertFalse(nested_dir.exists())

    def test_delete_entry_with_trailing_slash_endpoint(self) -> None:
        base = Path(self.temp_dir.name)
        target_file = base / "file-to-delete.txt"
        target_file.write_text("x", encoding="utf-8")

        status_code, payload = self._post_json(
            "/.upload/delete/",
            {"filename": "file-to-delete.txt", "directory": "/"},
        )
        self.assertEqual(status_code, 200)
        self.assertIn("Deleted file", payload["message"])
        self.assertFalse(target_file.exists())

    def test_list_entries_api_root(self) -> None:
        base = Path(self.temp_dir.name)
        (base / "folder").mkdir()
        (base / "folder" / "child.txt").write_text("child", encoding="utf-8")
        (base / "root.txt").write_text("hello", encoding="utf-8")
        (base / "hidden.upload").write_text("meta", encoding="utf-8")

        status_code, payload = self._get_json("/.api/list?directory=/")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["path"], "/")

        names = [item["name"] for item in payload["entries"]]
        self.assertIn("folder", names)
        self.assertIn("root.txt", names)
        self.assertNotIn("hidden.upload", names)

        folder_item = next(item for item in payload["entries"] if item["name"] == "folder")
        self.assertEqual(folder_item["display_name"], "folder/")
        self.assertEqual(folder_item["href"], "folder/")
        self.assertTrue(folder_item["can_delete"])
        self.assertFalse(folder_item["is_parent"])

    def test_list_entries_api_subdirectory_has_parent_entry(self) -> None:
        base = Path(self.temp_dir.name)
        (base / "sub").mkdir()
        (base / "sub" / "nested.txt").write_text("x", encoding="utf-8")

        status_code, payload = self._get_json("/.api/list?directory=/sub/")
        self.assertEqual(status_code, 200)
        self.assertEqual(payload["path"], "/sub/")
        self.assertGreaterEqual(len(payload["entries"]), 1)

        parent_item = payload["entries"][0]
        self.assertTrue(parent_item["is_parent"])
        self.assertEqual(parent_item["href"], "../")
        self.assertFalse(parent_item["can_delete"])

    def test_list_entries_api_invalid_directory(self) -> None:
        status_code, payload = self._get_json("/.api/list?directory=/missing")
        self.assertEqual(status_code, 400)
        self.assertIn("invalid directory", payload["message"])

    def test_upload_check_returns_rename_suggestion_for_plain_name(self) -> None:
        base = Path(self.temp_dir.name)
        (base / "movie.mp4").write_text("x", encoding="utf-8")

        status_code, payload = self._post_json(
            "/.upload/check",
            {"filename": "movie.mp4", "file_size": 1, "directory": "/"},
        )
        self.assertEqual(status_code, 200)
        self.assertTrue(payload["exists"])
        self.assertEqual(payload["rename_suggestion"], "movie (1).mp4")

    def test_upload_check_rename_suggestion_uses_largest_suffix(self) -> None:
        base = Path(self.temp_dir.name)
        (base / "movie.mp4").write_text("x", encoding="utf-8")
        (base / "movie (2).mp4").write_text("x", encoding="utf-8")

        status_code, payload = self._post_json(
            "/.upload/check",
            {"filename": "movie.mp4", "file_size": 1, "directory": "/"},
        )
        self.assertEqual(status_code, 200)
        self.assertTrue(payload["exists"])
        self.assertEqual(payload["rename_suggestion"], "movie (3).mp4")

    def test_upload_check_rename_suggestion_increments_suffix(self) -> None:
        base = Path(self.temp_dir.name)
        (base / "movie (1).mp4").write_text("x", encoding="utf-8")
        (base / "movie (2).mp4").write_text("x", encoding="utf-8")
        (base / "movie (3).mp4.upload").write_text("meta", encoding="utf-8")

        status_code, payload = self._post_json(
            "/.upload/check",
            {"filename": "movie (1).mp4", "file_size": 1, "directory": "/"},
        )
        self.assertEqual(status_code, 200)
        self.assertTrue(payload["exists"])
        self.assertEqual(payload["rename_suggestion"], "movie (4).mp4")


if __name__ == "__main__":
    unittest.main()
