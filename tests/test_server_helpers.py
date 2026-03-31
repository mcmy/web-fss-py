import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from web_fs_server.server import (  # noqa: E402
    _add_range,
    _count_covered_bytes,
    _normalize_ranges,
    _parse_single_range_header,
    _range_is_covered,
)


class RangeHeaderTests(unittest.TestCase):
    def test_range_prefix(self) -> None:
        self.assertEqual(_parse_single_range_header("bytes=0-9", 100), (0, 10))

    def test_range_start_to_end(self) -> None:
        self.assertEqual(_parse_single_range_header("bytes=10-", 100), (10, 100))

    def test_suffix_range(self) -> None:
        self.assertEqual(_parse_single_range_header("bytes=-20", 100), (80, 100))

    def test_invalid_range(self) -> None:
        with self.assertRaises(ValueError):
            _parse_single_range_header("bytes=200-300", 100)


class UploadRangeTests(unittest.TestCase):
    def test_normalize_and_merge(self) -> None:
        merged = _normalize_ranges([[0, 10], [10, 20], [25, 30], [28, 40]], 100)
        self.assertEqual(merged, [[0, 20], [25, 40]])

    def test_add_range(self) -> None:
        ranges = _add_range([[0, 10], [20, 30]], 10, 20, 100)
        self.assertEqual(ranges, [[0, 30]])

    def test_covered_bytes(self) -> None:
        self.assertEqual(_count_covered_bytes([[0, 10], [20, 25]]), 15)

    def test_range_is_covered(self) -> None:
        ranges = [[0, 10], [20, 30]]
        self.assertTrue(_range_is_covered(ranges, 2, 8))
        self.assertFalse(_range_is_covered(ranges, 8, 22))


if __name__ == "__main__":
    unittest.main()
