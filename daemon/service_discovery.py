import subprocess
import re
import json
import requests
from config import LLM_API_URL, LLM_MODEL, LLM_TIMEOUT

SERVICE_PROMPT = """You are a network security expert analyzing a Linux server.

This is a personal home machine, so common applications like Steam, media servers,
development tools, and desktop sharing are EXPECTED and SAFE.

For each service, determine if it's TRUSTED (safe for a home network).

TRUSTED (safe) examples:
- Gaming: Steam, game servers
- Media: Plex, Squeezebox, Jellyfin, Kodi
- Development: VS Code, LM Studio, Docker, Node.js, Flask
- Desktop: Synergy, KDE Connect, VNC, RDP
- System: SSH, HTTP, databases
- Communication: MQTT, Home Assistant

Only mark as UNKNOWN if it's:
- A service you've never heard of
- Suspicious malware-like process names
- Crypto miners or botnets

Respond with JSON only:
{
    "trusted_ports": [list of port numbers that are safe],
    "services": {"port": "service_name", ...}
}"""

KNOWN_SERVICES = {
    22: "SSH", 53: "DNS", 80: "HTTP", 443: "HTTPS",
    1234: "LM-Studio", 1716: "KDE-Connect", 1883: "MQTT",
    3000: "Node.js", 3306: "MySQL", 3389: "RDP",
    5000: "Flask/Dev", 5432: "PostgreSQL",
    6379: "Redis", 8080: "HTTP-Proxy",
    9000: "PHP-FPM/Squeezebox",
    24800: "Synergy", 27017: "MongoDB",
    27036: "Steam", 27060: "Steam", 32400: "Plex",
}

def get_listening_ports() -> list[dict]:

    services = []
    try:
        result = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True,
            text=True,
            timeout=10
        )

        for line in result.stdout.strip().split('\n')[1:]:
            parts = line.split()
            if len(parts) >= 6:
                local_addr = parts[3]
                port_match = re.search(r':(\d+)$', local_addr)
                if port_match:
                    port = int(port_match.group(1))
                    process = "unknown"
                    for part in parts:
                        if 'users:' in part:
                            proc_match = re.search(r'\("([^"]+)"', part)
                            if proc_match:
                                process = proc_match.group(1)
                    services.append({
                        "port": port,
                        "process": process,
                        "address": local_addr
                    })
    except Exception as e:
        print(f"[!] Port scan error: {e}")

    return services

def identify_service(port: int, process: str) -> str:

    if port in KNOWN_SERVICES:
        return KNOWN_SERVICES[port]

    proc_lower = process.lower()
    if "steam" in proc_lower:
        return "Steam"
    if "lm-studio" in proc_lower or "lmstudio" in proc_lower:
        return "LM-Studio"
    if "code" in proc_lower:
        return "VS-Code"
    if "kde" in proc_lower:
        return "KDE-Service"

    return process if process != "unknown" else f"Unknown:{port}"

def analyze_services_with_llm(services: list[dict]) -> dict:

    if not services:
        return {"trusted_ports": [], "services": {}, "warnings": []}

    service_list = "\n".join(
        f"Port {s['port']}: {identify_service(s['port'], s['process'])} (process: {s['process']})"
        for s in services
    )

    payload = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SERVICE_PROMPT},
            {"role": "user", "content": f"Analyze these {len(services)} open ports:\n{service_list}"}
        ],
        "temperature": 0.1,
        "max_tokens": 500
    }

    try:
        response = requests.post(LLM_API_URL, json=payload, timeout=LLM_TIMEOUT * 2)
        response.raise_for_status()
        content = response.json()['choices'][0]['message']['content']

        content = re.sub(r'```json?\s*', '', content)
        content = content.replace('```', '').strip()
        result = json.loads(content)

        result.setdefault("trusted_ports", [])
        result.setdefault("services", {})
        result.setdefault("warnings", [])
        return result

    except Exception as e:
        print(f"[!] LLM service analysis error: {e}")
        return {
            "trusted_ports": [22, 80, 443, 53],
            "services": {str(s['port']): identify_service(s['port'], s['process']) for s in services},
            "warnings": ["LLM unavailable - using default trust list"]
        }

def discover_services() -> tuple[set, dict]:

    from config import MANUAL_TRUSTED_PORTS

    print("[*] Discovering local services...")
    services = get_listening_ports()
    print(f"[+] Found {len(services)} listening ports")

    for s in services:
        print(f"    Port {s['port']:5d} : {identify_service(s['port'], s['process'])}")

    print("[*] Analyzing services with LLM...")
    analysis = analyze_services_with_llm(services)

    llm_trusted = set(analysis.get("trusted_ports", []))
    service_map = analysis.get("services", {})

    trusted_ports = llm_trusted.union(MANUAL_TRUSTED_PORTS)

    print(f"[+] LLM trusted: {sorted(llm_trusted)}")
    print(f"[+] Manual whitelist: {sorted(MANUAL_TRUSTED_PORTS)}")
    print(f"[+] Combined trusted: {sorted(trusted_ports)}")

    return trusted_ports, service_map

if __name__ == "__main__":
    trusted, services = discover_services()
    print(f"\nTrusted: {trusted}")
    print(f"Services: {services}")
