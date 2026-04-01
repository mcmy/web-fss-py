import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from web_fss.server import _build_startup_urls  # noqa: E402


class StartupUrlTests(unittest.TestCase):
    def test_specific_bind_only_one_url(self) -> None:
        urls = _build_startup_urls("127.0.0.1", 8000)
        self.assertEqual(urls, ["http://127.0.0.1:8000/"])

    def test_wildcard_bind_has_urls(self) -> None:
        urls = _build_startup_urls("0.0.0.0", 8000)
        self.assertTrue(len(urls) >= 1)
        self.assertTrue(all(url.startswith("http://") for url in urls))


if __name__ == "__main__":
    unittest.main()
