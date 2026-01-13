

from collections import deque
from datetime import datetime
from threading import Lock
from enum import Enum

class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"

class LogEntry:
    def __init__(self, level: LogLevel, source: str, message: str):
        self.id = None
        self.timestamp = datetime.now().isoformat()
        self.level = level
        self.source = source
        self.message = message

    def to_dict(self):
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "level": self.level.value,
            "source": self.source,
            "message": self.message
        }

class LogBuffer:

    def __init__(self, max_size: int = 500):
        self._buffer = deque(maxlen=max_size)
        self._lock = Lock()
        self._counter = 0

    def add(self, level: LogLevel, source: str, message: str) -> LogEntry:

        entry = LogEntry(level, source, message)
        with self._lock:
            self._counter += 1
            entry.id = self._counter
            self._buffer.append(entry)
        return entry

    def info(self, source: str, message: str) -> LogEntry:
        return self.add(LogLevel.INFO, source, message)

    def warning(self, source: str, message: str) -> LogEntry:
        return self.add(LogLevel.WARNING, source, message)

    def error(self, source: str, message: str) -> LogEntry:
        return self.add(LogLevel.ERROR, source, message)

    def debug(self, source: str, message: str) -> LogEntry:
        return self.add(LogLevel.DEBUG, source, message)

    def get_logs(self, limit: int = 100, level: str = None, since_id: int = 0) -> list[dict]:

        with self._lock:
            logs = list(self._buffer)

        if level:
            logs = [l for l in logs if l.level.value == level]

        if since_id:
            logs = [l for l in logs if l.id > since_id]

        logs = sorted(logs, key=lambda x: x.id, reverse=True)[:limit]

        return [l.to_dict() for l in logs]

    def get_latest_id(self) -> int:

        with self._lock:
            return self._counter

    def clear(self):

        with self._lock:
            self._buffer.clear()

_log_buffer = None

def get_log_buffer() -> LogBuffer:

    global _log_buffer
    if _log_buffer is None:
        _log_buffer = LogBuffer()
    return _log_buffer
