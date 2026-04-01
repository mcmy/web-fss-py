from typing import Any, List, Sequence, Tuple


def normalize_ranges(raw_ranges: Any, file_size: int) -> List[List[int]]:
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


def add_range(ranges: Sequence[Sequence[int]], start: int, end: int, file_size: int) -> List[List[int]]:
    raw = [list(item) for item in ranges]
    raw.append([start, end])
    return normalize_ranges(raw, file_size)


def count_covered_bytes(ranges: Sequence[Sequence[int]]) -> int:
    total = 0
    for item in ranges:
        if len(item) != 2:
            continue
        start = int(item[0])
        end = int(item[1])
        if end > start:
            total += end - start
    return total


def range_is_covered(ranges: Sequence[Sequence[int]], start: int, end: int) -> bool:
    for item in ranges:
        if len(item) != 2:
            continue
        if int(item[0]) <= start and int(item[1]) >= end:
            return True
    return False


def is_upload_complete(ranges: Sequence[Sequence[int]], file_size: int) -> bool:
    if file_size == 0:
        return True
    if not ranges:
        return False
    if len(ranges) != 1:
        return False
    return int(ranges[0][0]) == 0 and int(ranges[0][1]) == file_size


def parse_single_range_header(range_header: str, file_size: int) -> Tuple[int, int]:
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


# Backward-compatible aliases used by existing tests/import paths.
_normalize_ranges = normalize_ranges
_add_range = add_range
_count_covered_bytes = count_covered_bytes
_range_is_covered = range_is_covered
_is_upload_complete = is_upload_complete
_parse_single_range_header = parse_single_range_header
