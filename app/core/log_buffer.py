# app/core/log_buffer.py
"""
In-memory ring buffer that captures Python log records.
Exposes recent logs via the /__debug/logs endpoint.
"""
import logging
from collections import deque
from datetime import datetime, timezone
from typing import List, Dict

MAX_ENTRIES = 500

_buffer: deque = deque(maxlen=MAX_ENTRIES)


class BufferHandler(logging.Handler):
    """Logging handler that appends formatted records to an in-memory deque."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _buffer.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": self.format(record),
            })
        except Exception:
            pass


def install(level: int = logging.DEBUG) -> None:
    """Attach the buffer handler to the root logger."""
    handler = BufferHandler()
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.getLogger().addHandler(handler)
    # Also capture uvicorn logs
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.addHandler(handler)


def get_logs(limit: int = 200, level: str | None = None) -> List[Dict]:
    """Return recent log entries, newest first."""
    entries = list(_buffer)
    if level:
        entries = [e for e in entries if e["level"] == level.upper()]
    return list(reversed(entries[-limit:]))


def clear() -> None:
    _buffer.clear()
