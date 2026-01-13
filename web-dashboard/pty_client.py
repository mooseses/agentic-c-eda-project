

import asyncio
import json
import os
from typing import Optional, AsyncGenerator

SOCKET_PATH = os.environ.get(
    "AGENT_PTY_SOCKET",
    "/app/.agent/pty.sock"
)

class PTYClient:

    def __init__(self, socket_path: str = SOCKET_PATH):
        self.socket_path = socket_path
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self._connected = False

    async def connect(self) -> bool:

        try:
            self.reader, self.writer = await asyncio.open_unix_connection(
                self.socket_path
            )
            self._connected = True
            return True
        except Exception as e:
            self._connected = False
            return False

    async def create_session(self, command: str, timeout: int = 300) -> dict:

        if not self._connected:
            return {"status": "error", "message": "Not connected to PTY service"}

        request = {
            "action": "create",
            "command": command,
            "timeout": timeout
        }

        await self._send(request)
        response = await self._receive()
        return response or {"status": "error", "message": "No response"}

    async def send_input(self, data: str):

        if not self._connected:
            return

        request = {
            "type": "input",
            "data": data
        }
        await self._send(request)

    async def send_signal(self, signal_name: str = "SIGINT"):

        if not self._connected:
            return

        request = {
            "type": "signal",
            "signal": signal_name
        }
        await self._send(request)

    async def stream_output(self) -> AsyncGenerator[dict, None]:

        if not self._connected or not self.reader:
            return

        try:
            while True:
                line = await self.reader.readline()
                if not line:
                    break

                try:
                    msg = json.loads(line.decode())
                    yield msg

                    if msg.get("event") in ("done", "error"):
                        break
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass

    async def _send(self, data: dict):

        if self.writer:
            self.writer.write(json.dumps(data).encode() + b"\n")
            await self.writer.drain()

    async def _receive(self) -> Optional[dict]:

        if self.reader:
            try:
                line = await asyncio.wait_for(self.reader.readline(), timeout=5.0)
                if line:
                    return json.loads(line.decode())
            except (asyncio.TimeoutError, json.JSONDecodeError):
                pass
        return None

    async def close(self):

        self._connected = False
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass

    def is_connected(self) -> bool:

        return self._connected
