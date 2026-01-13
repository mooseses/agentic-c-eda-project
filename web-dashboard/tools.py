

import json
from database import get_db

class ToolRegistry:

    def __init__(self, db=None):
        self.db = db or get_db()
        self.tools = {

            "get_events": self.get_events,
            "get_flags": self.get_flags,

            "propose_command": self.propose_command,
            "propose_ignore_port": self.propose_ignore_port,
            "propose_ignore_ip": self.propose_ignore_ip,
            "resolve_flag": self.resolve_flag,
        }

    def get_tool_definitions(self) -> list[dict]:

        return [
            {
                "name": "get_events",
                "description": "Get recent security events from the database",
                "parameters": {"limit": "integer (optional, default 10)"}
            },
            {
                "name": "get_flags",
                "description": "Get pending flags that need user attention",
                "parameters": {"status": "string (optional: pending/resolved/dismissed)"}
            },
            {
                "name": "propose_command",
                "description": "Propose a shell command for user to approve and run. Use this for ANY investigation: checking ports, looking up IPs, reading logs, etc.",
                "parameters": {"command": "string", "reason": "string"}
            },
            {
                "name": "propose_ignore_port",
                "description": "Propose adding a port to the ignore list",
                "parameters": {"port": "integer", "reason": "string"}
            },
            {
                "name": "propose_ignore_ip",
                "description": "Propose adding an IP to the ignore list",
                "parameters": {"ip": "string", "reason": "string"}
            },
            {
                "name": "resolve_flag",
                "description": "Mark a flag as resolved or dismissed",
                "parameters": {"flag_id": "integer", "status": "string (resolved/dismissed)"}
            }
        ]

    def get_events(self, limit: int = 10) -> dict:

        events = self.db.get_events(limit=limit)
        return {"type": "tool_result", "data": {"events": events}}

    def get_flags(self, status: str = None) -> dict:

        flags = self.db.get_flags(status=status)
        return {"type": "tool_result", "data": {"flags": flags}}

    def propose_command(self, command: str, reason: str = None, description: str = None) -> dict:

        desc = reason or description or "No reason provided"
        return {
            "type": "proposal",
            "action": "run_command",
            "data": {
                "command": command,
                "reason": desc
            }
        }

    def propose_ignore_port(self, port: int, reason: str) -> dict:

        return {
            "type": "proposal",
            "action": "ignore_port",
            "data": {
                "port": port,
                "reason": reason
            }
        }

    def propose_ignore_ip(self, ip: str, reason: str) -> dict:

        return {
            "type": "proposal",
            "action": "ignore_ip",
            "data": {
                "ip": ip,
                "reason": reason
            }
        }

    def resolve_flag(self, flag_id: int, status: str) -> dict:

        if status not in ("resolved", "dismissed"):
            return {"type": "error", "message": "Status must be 'resolved' or 'dismissed'"}
        self.db.update_flag_status(flag_id, status)
        return {"type": "tool_result", "data": {"flag_id": flag_id, "status": status}}

    def execute_tool(self, tool_name: str, params: dict) -> dict:

        if tool_name not in self.tools:
            return {"type": "error", "message": f"Unknown tool: {tool_name}"}

        try:
            return self.tools[tool_name](**params)
        except Exception as e:
            return {"type": "error", "message": str(e)}

class ProposalExecutor:

    def __init__(self, db=None):
        self.db = db or get_db()

    def execute(self, action: str, data: dict) -> dict:

        if action == "run_command":

            return {"success": False, "error": "Commands should be executed via PTY service"}
        elif action == "ignore_port":
            return self._add_ignore_port(data["port"])
        elif action == "ignore_ip":
            return self._add_ignore_ip(data["ip"])
        else:
            return {"success": False, "error": f"Unknown action: {action}"}

    def _add_ignore_port(self, port: int) -> dict:

        current = self.db.get_config("ignored_ports", "")
        ports = set(current.split('\n')) if current else set()
        ports.discard('')
        ports.add(str(port))
        self.db.set_config("ignored_ports", '\n'.join(sorted(ports)))
        return {"success": True, "message": f"Added port {port} to ignore list"}

    def _add_ignore_ip(self, ip: str) -> dict:

        current = self.db.get_config("ignored_ips", "")
        ips = set(current.split('\n')) if current else set()
        ips.discard('')
        ips.add(ip)
        self.db.set_config("ignored_ips", '\n'.join(sorted(ips)))
        return {"success": True, "message": f"Added IP {ip} to ignore list"}

