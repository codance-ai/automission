"""Structured event stream for mission progress (JSONL-based IPC)."""

from __future__ import annotations

import json
import time
import threading
from pathlib import Path
from typing import Generator


class EventWriter:
    """Append structured events to a JSONL file with immediate flush."""

    def __init__(self, path: Path):
        self.path = path
        self._file = open(path, "a", encoding="utf-8")
        self._lock = threading.Lock()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False

    def emit(self, event_type: str, data: dict | None = None) -> None:
        """Write one event line. Thread-safe."""
        event = {"type": event_type, "ts": time.time()}
        if data:
            event.update(data)
        line = json.dumps(event, separators=(",", ":")) + "\n"
        with self._lock:
            self._file.write(line)
            self._file.flush()

    def close(self) -> None:
        self._file.close()


class EventTailer:
    """Read and tail a JSONL event file."""

    def __init__(self, path: Path):
        self.path = path

    def read_existing(self) -> Generator[dict, None, None]:
        """Yield all existing events from the file."""
        if not self.path.exists():
            return
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def follow(
        self,
        stop_event: threading.Event | None = None,
        poll_interval: float = 0.1,
    ) -> Generator[dict, None, None]:
        """Tail the file, yielding new events as they appear."""
        terminal_types = {"mission_completed", "mission_failed", "executor_shutdown"}

        with open(self.path, encoding="utf-8") as f:
            while True:
                line = f.readline()
                if line:
                    line = line.strip()
                    if line:
                        event = json.loads(line)
                        yield event
                        if event.get("type") in terminal_types:
                            return
                else:
                    if stop_event and stop_event.is_set():
                        return
                    time.sleep(poll_interval)
