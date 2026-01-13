import asyncio
import json
import os
import uuid
import logging
from typing import Optional
import os

from pty_manager import PTYSessionManager

logger = logging.getLogger("pty_service")

SOCKET_PATH = os.environ.get(
    "AGENT_PTY_SOCKET",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".agent", "pty.sock")
)

PASSWORD_PROMPTS = [
    "[sudo] password",
    "password:",
    "password for",
    "enter passphrase",
    "enter password",
    "authentication password",
]

CONFIRM_PROMPTS = [
    "[y/n]",
    "(y/n)",
    "[yes/no]",
    "(yes/no)",
    "continue? [",
    "proceed? [",
    "are you sure",
    "do you want to continue",
]

def detect_prompt_type(output: str) -> Optional[str]:

    output_lower = output.lower()

    for pattern in PASSWORD_PROMPTS:
        if pattern in output_lower:
            return "password"

    for pattern in CONFIRM_PROMPTS:
        if pattern in output_lower:
            return "confirm"

    return None

class PTYService:

    def __init__(self, socket_path: str = SOCKET_PATH):
        self.socket_path = socket_path
        self.manager = PTYSessionManager()
        self._server: Optional[asyncio.AbstractServer] = None
        self._running = False

    async def handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):

        session_id = None
        session = None

        try:

            data = await asyncio.wait_for(reader.readline(), timeout=30)
            if not data:
                return

            request = json.loads(data.decode())
            action = request.get("action")

            if action == "create":

                command = request.get("command", "")
                timeout = request.get("timeout", 300)

                if not command:
                    await self._send(writer, {"status": "error", "message": "No command provided"})
                    return

                session_id = str(uuid.uuid4())[:8]
                session = await self.manager.create_session(session_id, command, timeout)

                if not session:
                    await self._send(writer, {"status": "error", "message": "Failed to create PTY session"})
                    return

                await self._send(writer, {"status": "created", "session_id": session_id})

                await self._stream_session(session, reader, writer)

            elif action == "attach":

                session_id = request.get("session_id")
                session = self.manager.get_session(session_id)

                if not session:
                    await self._send(writer, {"status": "error", "message": "Session not found"})
                    return

                await self._send(writer, {"status": "attached", "session_id": session_id})
                await self._stream_session(session, reader, writer)

            elif action == "list":

                sessions = [
                    {
                        "session_id": s.session_id,
                        "command": s.command[:50],
                        "running": s.is_running(),
                        "created": s.created_at.isoformat()
                    }
                    for s in self.manager.sessions.values()
                ]
                await self._send(writer, {"status": "ok", "sessions": sessions})

            elif action == "close":

                session_id = request.get("session_id")
                await self.manager.close_session(session_id)
                await self._send(writer, {"status": "closed", "session_id": session_id})

            else:
                await self._send(writer, {"status": "error", "message": f"Unknown action: {action}"})

        except asyncio.TimeoutError:
            logger.warning("Client connection timed out")
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON from client: {e}")
        except Exception as e:
            logger.error(f"Error handling connection: {e}")
        finally:
            writer.close()
            await writer.wait_closed()

    async def _stream_session(self, session, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):

        async def read_pty_output():

            try:
                idle_count = 0
                max_idle = 50

                print(f"[PTY-DEBUG] Starting output loop for session {session.session_id}")

                while True:

                    output = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: session.read_output(0.05)
                    )

                    if output:
                        idle_count = 0
                        text = output.decode('utf-8', errors='replace')
                        prompt_type = detect_prompt_type(text)

                        msg = {
                            "event": "output",
                            "data": text,
                        }
                        if prompt_type:
                            msg["prompt_hint"] = prompt_type

                        await self._send(writer, msg)
                    else:
                        idle_count += 1

                    is_running = session.is_running()
                    if not is_running:
                        print(f"[PTY-DEBUG] Process ended, draining output...")

                        for _ in range(10):
                            remaining = await asyncio.get_event_loop().run_in_executor(
                                None, lambda: session.read_output(0.01)
                            )
                            if remaining:
                                text = remaining.decode('utf-8', errors='replace')
                                await self._send(writer, {"event": "output", "data": text})
                            else:
                                break
                        print(f"[PTY-DEBUG] Exiting loop - process ended")
                        break

                    # Safety: if we've been idle too long with no output, check again
                    if idle_count > max_idle:
                        print(f"[PTY-DEBUG] Exiting loop - idle timeout (is_running={is_running})")
                        break

                    await asyncio.sleep(0.01)

                exit_code = session.get_exit_code()
                await self._send(writer, {
                    "event": "done",
                    "session_id": session.session_id,
                    "exit_code": exit_code
                })

            except Exception as e:
                await self._send(writer, {"event": "error", "message": str(e)})

        async def read_client_input():

            try:
                while session.is_running():
                    try:
                        data = await asyncio.wait_for(reader.readline(), timeout=0.1)
                        if data:
                            request = json.loads(data.decode())

                            if request.get("type") == "input":
                                input_data = request.get("data", "")
                                session.write_input(input_data)

                            elif request.get("type") == "signal":
                                sig = request.get("signal", "SIGINT")
                                if sig == "SIGINT":
                                    session.send_signal(2)
                                elif sig == "SIGTERM":
                                    session.send_signal(15)

                            elif request.get("type") == "resize":

                                pass

                    except asyncio.TimeoutError:
                        continue
                    except json.JSONDecodeError:
                        continue

            except Exception as e:
                logger.warning(f"Error reading client input: {e}")

        await asyncio.gather(
            read_pty_output(),
            read_client_input(),
            return_exceptions=True
        )

    async def _send(self, writer: asyncio.StreamWriter, msg: dict):

        try:
            writer.write(json.dumps(msg).encode() + b"\n")
            await writer.drain()
        except Exception as e:
            logger.warning(f"Failed to send message: {e}")

    async def start(self):

        socket_dir = os.path.dirname(self.socket_path)
        os.makedirs(socket_dir, mode=0o777, exist_ok=True)

        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        self._server = await asyncio.start_unix_server(
            self.handle_connection,
            path=self.socket_path
        )

        os.chmod(self.socket_path, 0o666)

        self._running = True
        logger.info(f"PTY service listening on {self.socket_path}")
        print(f"[+] PTY service listening on {self.socket_path}")

        asyncio.create_task(self.manager.start_cleanup_loop())

        async with self._server:
            await self._server.serve_forever()

    async def stop(self):

        self._running = False

        if self._server:
            self._server.close()
            await self._server.wait_closed()

        await self.manager.close_all()

        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)

        logger.info("PTY service stopped")

async def main():

    logging.basicConfig(level=logging.INFO)
    service = PTYService()

    try:
        await service.start()
    except KeyboardInterrupt:
        await service.stop()

if __name__ == "__main__":
    asyncio.run(main())
