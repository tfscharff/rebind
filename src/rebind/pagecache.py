"""Results worked out from one page of a file, remembered for as long as the file is unchanged.

The app rebuilds the whole document every time a person saves a correction. Almost nothing a
rebuild computes depends on the correction -- the recognised text of a scanned page, its layout
regions, its music, its picture in the editor -- and each of those costs from a fraction of a second
to several seconds a page. Every such result is kept here, keyed by the file's identity (path, size,
modification time) and whatever else it depends on, so a rebuild pays only for what changed.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

# Pages remembered per cache: a long book several times over, and still a bounded amount of memory.
DEFAULT_LIMIT = 5000


def file_key(source: Path | str) -> tuple | None:
    """What identifies a file's contents for caching, or None if it cannot be looked at."""
    try:
        path = Path(source)
        stat = path.stat()
        return (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    except OSError:
        return None


class PageCache:
    """A bounded, thread-safe, least-recently-used map. A key of None is never stored or found."""

    def __init__(self, limit: int = DEFAULT_LIMIT) -> None:
        self._items: OrderedDict[tuple, Any] = OrderedDict()
        self._limit = limit
        self._lock = threading.Lock()

    def get(self, key: tuple | None, default: Any = None) -> Any:
        if key is None:
            return default
        with self._lock:
            if key not in self._items:
                return default
            self._items.move_to_end(key)
            return self._items[key]

    def put(self, key: tuple | None, value: Any) -> Any:
        if key is not None:
            with self._lock:
                self._items[key] = value
                self._items.move_to_end(key)
                while len(self._items) > self._limit:
                    self._items.popitem(last=False)
        return value

    def __contains__(self, key: tuple | None) -> bool:
        with self._lock:
            return key is not None and key in self._items
