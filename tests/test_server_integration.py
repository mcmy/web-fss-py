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


if __name__ == "__main__":
    unittest.main()
