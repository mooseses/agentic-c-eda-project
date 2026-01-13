

import asyncio
import json
import os
import sys
import secrets
from pathlib import Path

from fastapi import FastAPI, Depends, Query, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, str(Path(__file__).parent.parent))

from database import get_db, Database
from models import (
    EventResponse, DecisionResponse, ConfigResponse,
    ConfigUpdate, StatsResponse, HealthResponse, TestConnectionResponse,
    LogResponse
)
from auth import verify_api_key, get_api_key, API_KEY
from log_buffer import get_log_buffer, LogBuffer
from chat import ChatEngine
from tools import ProposalExecutor
from pty_client import PTYClient

VERSION = "1.0.0"
DB_PATH = os.environ.get("AGENT_DB_PATH", "/var/lib/agentic-c-eda/agentic-c-eda.db")

app = FastAPI(
    title="Agentic C-EDA Dashboard",
    description="Cognitive Network Defense System - Web Dashboard",
    version=VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

def get_database() -> Database:

    return get_db(DB_PATH)

@app.get("/", response_class=HTMLResponse)
async def root():

    index_path = static_path / "index.html"
    if index_path.exists():
        return index_path.read_text(encoding='utf-8')
    return HTMLResponse("<h1>Agentic C-EDA Dashboard</h1><p>Static files not found.</p>")

@app.get("/api/health", response_model=HealthResponse)
async def health_check(db: Database = Depends(get_database)):

    try:
        db.get_stats()
        db_status = "connected"
    except Exception:
        db_status = "error"

    return HealthResponse(
        status="healthy" if db_status == "connected" else "degraded",
        version=VERSION,
        database=db_status
    )

@app.get("/api/events", response_model=list[EventResponse])
async def get_events(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    return db.get_events(limit=limit, offset=offset)

@app.delete("/api/events/purge")
async def purge_events(
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    logs = get_log_buffer()
    events_count = db.purge_all_events()
    decisions_count = db.purge_all_decisions()
    logs.warning("CONFIG", f"Purged {events_count} events and {decisions_count} decisions")
    return {"events_deleted": events_count, "decisions_deleted": decisions_count}

@app.get("/api/events/stream")
async def stream_events(
    db: Database = Depends(get_database),
    api_key: str = Depends(verify_api_key)
):

    async def event_generator():
        last_id = db.get_latest_event_id()
        while True:
            await asyncio.sleep(1)
            events = db.get_events(limit=50)
            new_events = [e for e in events if e['id'] > last_id]
            if new_events:
                last_id = max(e['id'] for e in new_events)
                for event in reversed(new_events):
                    yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.get("/api/decisions", response_model=list[DecisionResponse])
async def get_decisions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    return db.get_decisions(limit=limit, offset=offset)

@app.get("/api/decisions/stream")
async def stream_decisions(
    db: Database = Depends(get_database),
    api_key: str = Depends(verify_api_key)
):

    async def decision_generator():
        last_id = db.get_latest_decision_id()
        while True:
            await asyncio.sleep(1)
            decisions = db.get_decisions(limit=10)
            new_decisions = [d for d in decisions if d['id'] > last_id]
            if new_decisions:
                last_id = max(d['id'] for d in new_decisions)
                for decision in reversed(new_decisions):
                    yield f"data: {json.dumps(decision)}\n\n"

    return StreamingResponse(
        decision_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.get("/api/config", response_model=ConfigResponse)
async def get_config(
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    sensitivity = int(db.get_config("sensitivity", "5"))
    trusted_manual = json.loads(db.get_config("trusted_ports_manual", "[]"))
    trusted_dynamic = json.loads(db.get_config("trusted_ports_dynamic", "[]"))
    ignored_ports = db.get_config("ignored_ports", "")
    ignored_ips = db.get_config("ignored_ips", "")
    custom_prompt = db.get_config("custom_prompt", "")
    llm_api_url = db.get_config("llm_api_url", "http://localhost:1234/v1/chat/completions")
    llm_api_key = db.get_config("llm_api_key", "")
    llm_model = db.get_config("llm_model", "qwen/qwen3-4b-2507")
    llm_timeout = int(db.get_config("llm_timeout", "10"))
    event_buffer = int(db.get_config("event_buffer", "5"))
    dry_run = db.get_config("dry_run", "false").lower() == "true"

    return ConfigResponse(
        sensitivity=sensitivity,
        trusted_ports_manual=trusted_manual,
        trusted_ports_dynamic=trusted_dynamic,
        ignored_ports=ignored_ports,
        ignored_ips=ignored_ips,
        custom_prompt=custom_prompt,
        llm_api_url=llm_api_url,
        llm_api_key=llm_api_key,
        llm_model=llm_model,
        llm_timeout=llm_timeout,
        event_buffer=event_buffer,
        dry_run=dry_run
    )

@app.put("/api/config", response_model=ConfigResponse)
async def update_config(
    update: ConfigUpdate,
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    logs = get_log_buffer()
    changes = []

    if update.sensitivity is not None:
        db.set_config("sensitivity", str(update.sensitivity))
        changes.append(f"sensitivity={update.sensitivity}")
    if update.trusted_ports_manual is not None:
        db.set_config("trusted_ports_manual", json.dumps(update.trusted_ports_manual))
        changes.append(f"trusted_ports={len(update.trusted_ports_manual)}")
    if update.ignored_ports is not None:
        db.set_config("ignored_ports", update.ignored_ports)
        changes.append("ignored_ports")
    if update.ignored_ips is not None:
        db.set_config("ignored_ips", update.ignored_ips)
        changes.append("ignored_ips")
    if update.custom_prompt is not None:
        db.set_config("custom_prompt", update.custom_prompt)
        changes.append("custom_prompt")
    if update.llm_api_url is not None:
        db.set_config("llm_api_url", update.llm_api_url)
        changes.append("llm_api_url")
    if update.llm_api_key is not None:
        db.set_config("llm_api_key", update.llm_api_key)
        changes.append("llm_api_key")
    if update.llm_model is not None:
        db.set_config("llm_model", update.llm_model)
        changes.append(f"llm_model={update.llm_model}")
    if update.llm_timeout is not None:
        db.set_config("llm_timeout", str(update.llm_timeout))
        changes.append(f"llm_timeout={update.llm_timeout}s")
    if update.event_buffer is not None:
        db.set_config("event_buffer", str(update.event_buffer))
        changes.append(f"event_buffer={update.event_buffer}s")
    if update.dry_run is not None:
        db.set_config("dry_run", "true" if update.dry_run else "false")
        changes.append(f"dry_run={'enabled' if update.dry_run else 'disabled'}")

    if changes:
        logs.info("CONFIG", f"Updated: {', '.join(changes)}")

    return await get_config(db=db, _=_)

@app.post("/api/test-connection", response_model=TestConnectionResponse)
async def test_connection(
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    import requests
    logs = get_log_buffer()

    api_url = db.get_config("llm_api_url", "http://localhost:1234/v1/chat/completions")
    api_key = db.get_config("llm_api_key", "")
    llm_model = db.get_config("llm_model", "qwen/qwen3-4b-2507")
    timeout = int(db.get_config("llm_timeout", "10"))

    logs.info("TEST", f"Testing connection to {api_url}")

    try:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        response = requests.post(
            api_url,
            headers=headers,
            json={
                "model": llm_model,
                "messages": [{"role": "user", "content": "test"}],
                "max_tokens": 5
            },
            timeout=timeout
        )

        if response.status_code == 200:
            logs.info("TEST", "Connection successful")
            return TestConnectionResponse(success=True, message="Connection successful")
        else:
            logs.warning("TEST", f"API returned status {response.status_code}")
            return TestConnectionResponse(success=False, message=f"API returned status {response.status_code}")
    except requests.exceptions.Timeout:
        logs.error("TEST", "Connection timed out")
        return TestConnectionResponse(success=False, message="Connection timed out")
    except requests.exceptions.ConnectionError:
        logs.error("TEST", "Could not connect to API")
        return TestConnectionResponse(success=False, message="Could not connect to API")
    except Exception as e:
        logs.error("TEST", str(e))
        return TestConnectionResponse(success=False, message=str(e))

@app.post("/api/notifications/test/telegram")
async def test_telegram_notification(
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    try:
        from notifications import NotificationService
        notifier = NotificationService(db)
        success, message = await notifier.test_telegram()
        return {"success": success, "message": message}
    except Exception as e:
        return {"success": False, "message": f"Error: {str(e)}"}

@app.post("/api/notifications/test/bark")
async def test_bark_notification(
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    try:
        from notifications import NotificationService
        notifier = NotificationService(db)
        success, message = await notifier.test_bark()
        return {"success": success, "message": message}
    except Exception as e:
        return {"success": False, "message": f"Error: {str(e)}"}

@app.get("/api/stats", response_model=StatsResponse)
async def get_stats(
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    return db.get_stats()

def get_logs() -> LogBuffer:

    return get_log_buffer()

@app.get("/api/logs", response_model=list[LogResponse])
async def get_debug_logs(
    limit: int = Query(100, ge=1, le=500),
    level: str = Query(None, description="Filter by level: INFO, WARNING, ERROR"),
    logs: LogBuffer = Depends(get_logs),
    _: str = Depends(verify_api_key)
):

    return logs.get_logs(limit=limit, level=level)

@app.get("/api/logs/stream")
async def stream_logs(
    api_key: str = Query(..., description="API key for SSE auth"),
    level: str = Query(None, description="Filter by level"),
    logs: LogBuffer = Depends(get_logs)
):

    if not secrets.compare_digest(api_key, API_KEY):
        raise HTTPException(status_code=403, detail="Invalid API key")

    async def log_generator():
        last_id = logs.get_latest_id()
        while True:
            await asyncio.sleep(0.5)
            new_logs = logs.get_logs(limit=20, level=level, since_id=last_id)
            if new_logs:
                last_id = max(l['id'] for l in new_logs)
                for log in reversed(new_logs):
                    yield f"data: {json.dumps(log)}\n\n"

    return StreamingResponse(
        log_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )

@app.delete("/api/logs")
async def clear_logs(
    logs: LogBuffer = Depends(get_logs),
    _: str = Depends(verify_api_key)
):

    logs.clear()
    logs.info("WEB", "Logs cleared by user")
    return {"status": "cleared"}

async def watch_daemon_logs():

    import aiofiles
    import os

    logs = get_log_buffer()
    log_files = {
        "security_events.log": "DAEMON",
        "agent_decisions.log": "AGENT"
    }

    positions = {}
    base_path = Path(__file__).parent.parent

    for filename in log_files:
        filepath = base_path / filename
        if filepath.exists():
            positions[filename] = filepath.stat().st_size

    while True:
        await asyncio.sleep(1)
        for filename, source in log_files.items():
            filepath = base_path / filename
            if not filepath.exists():
                continue

            current_size = filepath.stat().st_size
            last_pos = positions.get(filename, 0)

            if current_size > last_pos:
                try:
                    async with aiofiles.open(filepath, 'r') as f:
                        await f.seek(last_pos)
                        content = await f.read()
                        for line in content.strip().split('\n'):
                            if line:

                                if 'ERROR' in line.upper():
                                    logs.error(source, line)
                                elif 'WARNING' in line.upper() or 'BLOCK' in line:
                                    logs.warning(source, line)
                                else:
                                    logs.info(source, line)
                    positions[filename] = current_size
                except Exception:
                    pass

@app.post("/api/chat")
async def chat(
    request: dict,
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    message = request.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="Message required")

    engine = ChatEngine(db)

    async def stream():
        for chunk in engine.stream_chat(message):

            event = chunk.get("event", "message")
            yield f"event: {event}\ndata: {json.dumps(chunk)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )

@app.get("/api/chat/history")
async def get_chat_history(
    limit: int = Query(50, ge=1, le=100),
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    return db.get_chat_messages(limit=limit)

@app.delete("/api/chat/history")
async def clear_chat_history(
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    db.clear_chat_messages()
    return {"status": "cleared"}

@app.post("/api/execute")
async def execute_command(
    request: dict,
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    command = request.get("command", "")
    if not command:
        raise HTTPException(status_code=400, detail="Command required")

    engine = ChatEngine(db)

    async def stream():
        for chunk in engine.execute_command(command):
            event = chunk.get("event", "message")
            yield f"event: {event}\ndata: {json.dumps(chunk)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )

@app.post("/api/execute/retry")
async def execute_with_password(
    request: dict,
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    command = request.get("command", "")
    password = request.get("password", "")

    if not command:
        raise HTTPException(status_code=400, detail="Command required")
    if not password:
        raise HTTPException(status_code=400, detail="Password required")

    engine = ChatEngine(db)

    async def stream():
        full_output = ""
        for chunk in engine.execute_with_password(command, password):
            event = chunk.get("event", "message")
            yield f"event: {event}\ndata: {json.dumps(chunk)}\n\n"
            if chunk.get("event") == "terminal_done":
                full_output = chunk.get("output", "")

        if full_output:
            yield f"event: status\ndata: {json.dumps({'event': 'status', 'text': 'Analyzing output...'})}\n\n"
            analysis_msg = f"Command output:\n```\n{full_output[:3000]}\n```\n\nProvide a brief analysis of this output."
            for chunk in engine.stream_chat(analysis_msg):
                if chunk["event"] in ("text", "proposal"):
                    yield f"event: {chunk['event']}\ndata: {json.dumps(chunk)}\n\n"
                elif chunk["event"] == "done":
                    break

        yield f"event: done\ndata: {json.dumps({'event': 'done'})}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )

@app.get("/api/flags")
async def get_flags(
    status: str = Query(None),
    limit: int = Query(50, ge=1, le=100),
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    return db.get_flags(status=status, limit=limit)

@app.post("/api/flags/{flag_id}/resolve")
async def resolve_flag(
    flag_id: int,
    request: dict,
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    status = request.get("status", "resolved")
    if status not in ("resolved", "dismissed"):
        raise HTTPException(status_code=400, detail="Status must be 'resolved' or 'dismissed'")
    db.update_flag_status(flag_id, status)
    return {"flag_id": flag_id, "status": status}

@app.post("/api/flags/{flag_id}/dismiss")
async def dismiss_flag(
    flag_id: int,
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    db.update_flag_status(flag_id, "dismissed")
    return {"flag_id": flag_id, "status": "dismissed"}

@app.get("/api/flags/stream")
async def stream_flags(
    db: Database = Depends(get_database),
    api_key: str = Depends(verify_api_key)
):

    async def flag_generator():
        last_count = -1
        while True:
            flags = db.get_flags(status="pending", limit=50)
            current_count = len(flags)

            if current_count != last_count:
                yield f"data: {json.dumps({'event': 'flags', 'flags': flags})}\n\n"
                last_count = current_count

            await asyncio.sleep(2)

    return StreamingResponse(
        flag_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )

@app.on_event("startup")
async def startup():

    logs = get_log_buffer()
    print("=" * 50)
    print("  Agentic C-EDA Web Dashboard")
    print("=" * 50)
    print(f"  API Key: {get_api_key()}")
    print(f"  Database: {DB_PATH}")
    print("=" * 50)

    logs.info("WEB", "Dashboard started")
    logs.info("WEB", f"API Key: {get_api_key()[:8]}...")
    logs.info("WEB", f"Database: {DB_PATH}")

    asyncio.create_task(watch_daemon_logs())

_pending_commands: dict[str, str] = {}

@app.post("/api/terminal/prepare")
async def prepare_terminal_command(
    request: dict,
    db: Database = Depends(get_database),
    _: str = Depends(verify_api_key)
):

    import uuid

    command = request.get("command", "")
    if not command:
        raise HTTPException(status_code=400, detail="Command required")

    command_id = str(uuid.uuid4())[:8]
    _pending_commands[command_id] = command

    return {"command_id": command_id, "command": command}

@app.websocket("/ws/terminal/{command_id}")
async def terminal_websocket(
    websocket: WebSocket,
    command_id: str
):

    await websocket.accept()

    command = _pending_commands.pop(command_id, None)
    if not command:
        await websocket.send_json({"event": "error", "message": "Command not found or expired"})
        await websocket.close()
        return

    pty_client = PTYClient()
    connected = await pty_client.connect()

    if not connected:
        await websocket.send_json({"event": "error", "message": "Failed to connect to PTY service"})
        await websocket.close()
        return

    try:

        result = await pty_client.create_session(command)

        if result.get("status") != "created":
            await websocket.send_json({
                "event": "error",
                "message": result.get("message", "Failed to create session")
            })
            return

        session_id = result.get("session_id")
        await websocket.send_json({"event": "session_created", "session_id": session_id})

        async def read_from_pty():

            try:
                async for msg in pty_client.stream_output():
                    await websocket.send_json(msg)
                    if msg.get("event") in ("done", "error"):
                        break
            except Exception as e:
                await websocket.send_json({"event": "error", "message": str(e)})

        async def read_from_browser():

            try:
                while True:
                    data = await websocket.receive_json()
                    msg_type = data.get("type")

                    if msg_type == "input":
                        await pty_client.send_input(data.get("data", ""))
                    elif msg_type == "signal":
                        await pty_client.send_signal(data.get("signal", "SIGINT"))
                    elif msg_type == "close":
                        break
            except WebSocketDisconnect:
                pass
            except Exception:
                pass

        await asyncio.gather(
            read_from_pty(),
            read_from_browser(),
            return_exceptions=True
        )

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"event": "error", "message": str(e)})
        except:
            pass
    finally:
        await pty_client.close()
        try:
            await websocket.close()
        except:
            pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
