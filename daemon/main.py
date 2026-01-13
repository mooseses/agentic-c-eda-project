import signal
import sys
import time
import logging
import re
import threading
import asyncio

import config
from watchdog import LogWatchdog
from logic import ReasoningEngine
from firewall import FirewallController
from service_discovery import discover_services
from database import get_db

SECURITY_LOG = "/var/lib/agentic-c-eda/logs/security_events.log"
AGENT_LOG = "/var/lib/agentic-c-eda/logs/agent_decisions.log"

firewall = None
watchdog = None
db = None
pty_service_thread = None


RE_SOURCE = re.compile(r'Source=([^\s]+)')
RE_PORT = re.compile(r'Port=(\d+)')


def setup_logger(name: str, log_file: str) -> logging.Logger:
    import os
    from pathlib import Path
    
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s | %(message)s')

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


def parse_event_type(event: str) -> str:
    return event.split()[0] if event else "UNKNOWN"


def parse_event_details(event: str) -> tuple[str, int]:
    source_match = RE_SOURCE.search(event)
    port_match = RE_PORT.search(event)
    source_ip = source_match.group(1) if source_match else None
    port = int(port_match.group(1)) if port_match else None
    return source_ip, port


def start_pty_service():
    def run_pty():
        from pty_service import PTYService, SOCKET_PATH
        import traceback
        
        print(f"[*] PTY service starting, socket: {SOCKET_PATH}")
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        service = PTYService()
        try:
            loop.run_until_complete(service.start())
        except Exception as e:
            print(f"[!] PTY service error: {e}")
            traceback.print_exc()
    
    thread = threading.Thread(target=run_pty, daemon=True, name="PTYService")
    thread.start()
    return thread


def shutdown(sig, frame):
    print("\n[!] Shutting down...")
    if watchdog:
        watchdog.stop_stream()
    if firewall:
        firewall.disable_sensor()
    sys.exit(0)


def main():
    global firewall, watchdog, db, pty_service_thread

    print("=" * 50)
    print("  Agentic C-EDA - Cyber-Physical  Edge  Defense  Architecture")
    print("=" * 50)

    print("\n[Phase 0: Database Initialization]")
    db = get_db(config.DATABASE_PATH)
    print(f"[+] Database: {config.DATABASE_PATH}")

    print("\n[Phase 1: Service Discovery]")
    trusted_ports, trusted_services = discover_services()
    config.TRUSTED_INTERNAL_PORTS = trusted_ports
    config.TRUSTED_SERVICES = trusted_services
    print(f"[+] {len(trusted_ports)} ports added to internal trust list")
    
    import json
    db.set_config("trusted_ports_dynamic", json.dumps(list(trusted_ports)))
    print(f"[+] Saved {len(trusted_ports)} ports to database")

    print("\n[Phase 2: Starting PTY Service]")
    pty_service_thread = start_pty_service()
    time.sleep(0.5)
    
    print("\n[Phase 3: Initializing Sensors]")
    
    analysis_interval = int(db.get_config("batch_interval", "5"))
    
    print(f"[*] Security log: {SECURITY_LOG}")
    print(f"[*] Agent log:    {AGENT_LOG}")
    print(f"[*] Analysis interval: {analysis_interval}s")

    sec_log = setup_logger("security", SECURITY_LOG)
    agent_log = setup_logger("agent", AGENT_LOG)

    firewall = FirewallController()
    watchdog = LogWatchdog(db=db)
    reasoning = ReasoningEngine(db=db)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    firewall.enable_sensor()
    watchdog.start_stream()

    sec_log.info("[*] Monitoring active. Awaiting events...")

    event_buffer = []
    event_ids_for_batch = []
    buffer_start_time = None
    batch_id = db.get_latest_decision_id() + 1

    while True:
        event = watchdog.read_stream()
        if event:
            sec_log.info(f"[EVENT] {event}")
            event_buffer.append(event)

            event_type = parse_event_type(event)
            source_ip, port = parse_event_details(event)
            event_id = db.insert_event(
                event_type=event_type,
                raw_event=event,
                source_ip=source_ip,
                port=port,
                batch_id=batch_id
            )
            event_ids_for_batch.append(event_id)

            if buffer_start_time is None:
                buffer_start_time = time.time()

        if buffer_start_time is not None:
            elapsed = time.time() - buffer_start_time
            if elapsed >= analysis_interval:
                agent_log.info(f"[ANALYSIS] Analyzing {len(event_buffer)} event(s)...")

                result = reasoning.analyze_batch(event_buffer)

                flagged = result.get('flagged', False)
                severity = result.get('severity', 'info')
                summary = result.get('summary', 'No summary')
                suggested_actions = result.get('suggested_actions', [])

                db.insert_decision(
                    batch_id=batch_id,
                    event_count=len(event_buffer),
                    verdict="FLAG" if flagged else "ALLOW",
                    confidence=0.0,
                    reason=summary,
                    threat_ips=[]
                )

                if flagged:
                    db.insert_flag(
                        event_ids=event_ids_for_batch,
                        severity=severity,
                        summary=summary,
                        suggested_actions=suggested_actions
                    )
                    
                    if severity == "critical":
                        agent_log.warning(f"    🚨 CRITICAL: {summary}")
                    elif severity == "warning":
                        agent_log.warning(f"    ⚠️  WARNING: {summary}")
                    else:
                        agent_log.info(f"    📋 INFO: {summary}")
                else:
                    agent_log.info(f"    ✅ OK: {summary}")

                event_buffer.clear()
                event_ids_for_batch.clear()
                buffer_start_time = None
                batch_id += 1


if __name__ == "__main__":
    main()