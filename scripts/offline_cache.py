"""Offline cache: JSONL-based temp storage for undelivered envelopes.

When the relay is unreachable and offline_cache is enabled, envelopes are
written to a JSONL file. On reconnection, they are replayed in sequence order.
"""

import json
import os
import time


class OfflineCache:
    """FIFO offline message cache backed by a JSONL file.

    Each line is a JSON object with:
      - seq: microsecond-precision timestamp for ordering
      - envelope: the full envelope dict

    When the cache exceeds max_size, the oldest entries are dropped (FIFO).
    """

    def __init__(self, path: str, max_size: int = 1000):
        self.path = path
        self.max_size = max_size

    def append(self, envelope: dict) -> None:
        """Append one envelope to the cache.

        If the cache exceeds max_size after appending, oldest entries
        are removed (FIFO eviction).
        """
        entry = {
            "seq": int(time.time() * 1_000_000),
            "envelope": envelope,
        }
        entries = self._read_all()
        entries.append(entry)
        if len(entries) > self.max_size:
            entries = entries[-self.max_size:]
        self._write_all(entries)

    def pop_all(self) -> list[dict]:
        """Pop all cached envelopes in chronological order and clear the file.

        Returns:
            List of envelope dicts, ordered by seq (oldest first).
        """
        entries = self._read_all()
        entries.sort(key=lambda e: e["seq"])
        envelopes = [e["envelope"] for e in entries]
        self._write_all([])
        return envelopes

    def size(self) -> int:
        """Return the number of cached entries."""
        return len(self._read_all())

    def _read_all(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        entries = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        return entries

    def _write_all(self, entries: list[dict]) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
