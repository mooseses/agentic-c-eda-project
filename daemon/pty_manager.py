import os
import pty
import select
import signal
import asyncio
import fcntl
import struct
import termios
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Callable

logger = logging.getLogger("pty_manager")

@dataclass
class PTYSession:

    session_id: str
    command: str
    master_fd: int = -1
    pid: int = -1
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    timeout: int = 300
    _closed: bool = False
    _exit_code: Optional[int] = None

    def start(self) -> bool:

        try:

            self.master_fd, slave_fd = pty.openpty()

            flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
            fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            self.pid = os.fork()

            if self.pid == 0:

                os.close(self.master_fd)

                os.setsid()

                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

                os.dup2(slave_fd, 0)
                os.dup2(slave_fd, 1)
                os.dup2(slave_fd, 2)

                if slave_fd > 2:
                    os.close(slave_fd)

                winsize = struct.pack('HHHH', 24, 80, 0, 0)
                fcntl.ioctl(0, termios.TIOCSWINSZ, winsize)

                os.environ['TERM'] = 'xterm-256color'
                os.environ['COLUMNS'] = '80'
                os.environ['LINES'] = '24'

                os.execvp("/bin/bash", ["/bin/bash", "-c", self.command])

            else:

                os.close(slave_fd)
                logger.info(f"PTY session {self.session_id} started: pid={self.pid}, command={self.command[:50]}")
                return True

        except Exception as e:
            logger.error(f"Failed to start PTY session: {e}")
            self._cleanup()
            return False

    def read_output(self, timeout: float = 0.1) -> Optional[bytes]:

        if self._closed or self.master_fd < 0:
            return None

        try:
            r, _, _ = select.select([self.master_fd], [], [], timeout)
            if self.master_fd in r:
                data = os.read(self.master_fd, 4096)
                if data:
                    self.last_activity = datetime.now()
                    return data
        except (OSError, ValueError):
            pass

        return None

    def write_input(self, data: str) -> bool:

        if self._closed or self.master_fd < 0:
            return False

        try:
            os.write(self.master_fd, data.encode('utf-8'))
            self.last_activity = datetime.now()
            return True
        except OSError as e:
            logger.warning(f"Failed to write to PTY {self.session_id}: {e}")
            return False

    def send_signal(self, sig: int):

        if self.pid > 0:
            try:
                os.kill(self.pid, sig)
            except OSError:
                pass

    def is_running(self) -> bool:

        if self.pid <= 0 or self._closed:
            return False

        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid == self.pid:

                if os.WIFEXITED(status):
                    self._exit_code = os.WEXITSTATUS(status)
                elif os.WIFSIGNALED(status):
                    self._exit_code = -os.WTERMSIG(status)
                self._closed = True
                return False
            return True
        except ChildProcessError:
            self._closed = True
            return False

    def get_exit_code(self) -> Optional[int]:

        if not self._closed:
            self.is_running()
        return self._exit_code

    def is_timed_out(self) -> bool:

        return datetime.now() - self.last_activity > timedelta(seconds=self.timeout)

    def close(self):

        if self._closed:
            return

        self._closed = True

        if self.master_fd >= 0:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = -1

        if self.pid > 0:
            try:
                os.kill(self.pid, signal.SIGTERM)

                for _ in range(10):
                    pid, _ = os.waitpid(self.pid, os.WNOHANG)
                    if pid == self.pid:
                        break
                    import time
                    time.sleep(0.1)
                else:

                    os.kill(self.pid, signal.SIGKILL)
                    os.waitpid(self.pid, 0)
            except (OSError, ChildProcessError):
                pass

        logger.info(f"PTY session {self.session_id} closed")

    def _cleanup(self):

        self.close()

    def __del__(self):
        self.close()

class PTYSessionManager:

    def __init__(self):
        self.sessions: dict[str, PTYSession] = {}
        self._lock = asyncio.Lock()

    async def create_session(self, session_id: str, command: str, timeout: int = 300) -> Optional[PTYSession]:

        async with self._lock:

            if session_id in self.sessions:
                self.sessions[session_id].close()
                del self.sessions[session_id]

            session = PTYSession(
                session_id=session_id,
                command=command,
                timeout=timeout
            )

            if session.start():
                self.sessions[session_id] = session
                return session
            else:
                return None

    def get_session(self, session_id: str) -> Optional[PTYSession]:

        return self.sessions.get(session_id)

    async def close_session(self, session_id: str):

        async with self._lock:
            if session_id in self.sessions:
                self.sessions[session_id].close()
                del self.sessions[session_id]

    async def cleanup_stale_sessions(self):

        async with self._lock:
            to_remove = []

            for sid, session in self.sessions.items():
                if session.is_timed_out():
                    logger.info(f"Session {sid} timed out, closing")
                    session.close()
                    to_remove.append(sid)
                elif not session.is_running() and session._closed:
                    to_remove.append(sid)

            for sid in to_remove:
                del self.sessions[sid]

    async def start_cleanup_loop(self, interval: int = 30):

        while True:
            await asyncio.sleep(interval)
            await self.cleanup_stale_sessions()

    def get_active_count(self) -> int:

        return len(self.sessions)

    async def close_all(self):

        async with self._lock:
            for session in self.sessions.values():
                session.close()
            self.sessions.clear()
