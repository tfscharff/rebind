"""Per-page results are remembered while the file is unchanged, and forgotten once it changes."""

from __future__ import annotations

import os
from pathlib import Path

from rebind.pagecache import PageCache, file_key


def test_a_result_is_found_again_until_the_file_changes(tmp_path: Path):
    source = tmp_path / "a.pdf"
    source.write_bytes(b"one")
    cache = PageCache()
    cache.put(file_key(source) + (1,), "page one")
    assert cache.get(file_key(source) + (1,)) == "page one"

    source.write_bytes(b"two!")
    os.utime(source, ns=(1, 1))
    assert cache.get(file_key(source) + (1,)) is None


def test_the_oldest_result_goes_first_and_a_missing_file_is_never_cached(tmp_path: Path):
    cache = PageCache(limit=2)
    for key in ("a", "b", "c"):
        cache.put((key,), key)
    assert cache.get(("a",)) is None and cache.get(("c",)) == "c"
    assert file_key(tmp_path / "missing.pdf") is None
    cache.put(None, "x")
    assert None not in cache
