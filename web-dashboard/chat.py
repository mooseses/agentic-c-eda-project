

import json
import logging
import subprocess
import requests
from typing import Generator
from database import Database

logger = logging.getLogger('chatbot')
logger.setLevel(logging.DEBUG)
if not logger.handlers:
    fh = logging.FileHandler('chatbot.log')
    fh.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
    logger.addHandler(fh)

SYSTEM_PROMPT = """You are Agent, an assistant for a Linux based server.
You help users with security monitoring AND general server maintenance tasks.

AVAILABLE TOOLS:
- get_events: Get recent security events from database
- get_flags: Get pending security flags
- propose_command: Propose ANY shell command for user approval

You CAN help with:
- Security monitoring (checking logs, ports, IPs, processes)
- Network diagnostics (ping, traceroute, netstat, ss)
- System maintenance (apt, systemctl, df, free, uptime)
- File operations (ls, cat, tail, grep)
- ANY command the user requests

Evaluate other requests and determine if a tool call is needed. If the request isn't related to server management, refuse.

TO CALL A TOOL, respond with ONLY this exact format:
TOOL: tool_name
PARAMS: {"param1": "value1"}

EXAMPLES:
User: "ping google"
You: TOOL: propose_command
PARAMS: {"command": "ping -c 5 google.com", "reason": "Test internet connectivity"}

User: "What ports are listening?"
You: TOOL: propose_command
PARAMS: {"command": "ss -tlnp", "reason": "List listening TCP ports"}

For regular conversation, just respond normally without TOOL format.
Keep responses concise. Use markdown for formatting.

CRITICAL RULES:
1. Commands like ping, apt, systemctl are ALLOWED. Propose them immediately.
2. Be direct and take action. Don't explain alternatives - just do it."""

class ChatEngine:

    def __init__(self, db: Database):
        self.db = db

    def _get_llm_config(self) -> dict:

        return {
            "url": self.db.get_config("llm_api_url", "http://localhost:1234/v1/chat/completions"),
            "model": self.db.get_config("llm_model", "qwen/qwen3-4b-2507"),
            "timeout": int(self.db.get_config("llm_timeout", "30")),
            "api_key": self.db.get_config("llm_api_key", "")
        }

    def _call_llm(self, messages: list) -> str:

        config = self._get_llm_config()

        headers = {"Content-Type": "application/json"}
        if config["api_key"]:
            headers["Authorization"] = f"Bearer {config['api_key']}"

        payload = {
            "model": config["model"],
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 2000
        }

        try:
            resp = requests.post(config["url"], headers=headers, json=payload, timeout=config["timeout"])
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"LLM error: {e}")
            return f"Error calling LLM: {e}"

    def _parse_tool_call(self, text: str) -> tuple[str, dict] | None:

        if "TOOL:" in text:
            try:
                lines = text.strip().split("\n")
                tool_name = None
                params = {}
                for line in lines:
                    line = line.strip()
                    if line.startswith("TOOL:"):
                        tool_name = line.split(":", 1)[1].strip()
                    elif line.startswith("PARAMS:"):
                        params_str = line.split(":", 1)[1].strip()
                        params = json.loads(params_str)
                if tool_name:
                    return (tool_name, params)
            except Exception as e:
                logger.warning(f"Standard tool parse error: {e}")

        if "<|message|>" in text:
            try:
                json_part = text.split("<|message|>")[-1].split("<|")[0].strip()
                data = json.loads(json_part)

                tool_name = None
                if "to=" in text:
                    import re
                    to_match = re.search(r'to=(?:tool[:\.])?(\w+)', text)
                    if to_match:
                        tool_name = to_match.group(1)

                if "command" in data:
                    return ("propose_command", data)
                if tool_name:
                    return (tool_name, data)
                if "tool" in data:
                    return (data["tool"], data.get("params", {}))
            except Exception as e:
                logger.warning(f"Channel message parse error: {e}")

        if "{" in text and "}" in text:
            try:

                start = text.find("{")
                end = text.rfind("}") + 1
                json_part = text[start:end]
                data = json.loads(json_part)
                if "command" in data:
                    return ("propose_command", data)
                if "tool" in data:
                    return (data["tool"], data.get("params", {}))
            except:
                pass

        import re
        proposing_match = re.search(r'Proposing:\s*(.+?)(?:\n|$)', text)
        if proposing_match:
            command = proposing_match.group(1).strip().strip('`')
            if command:
                logger.info(f"Extracted command from 'Proposing:' text: {command}")
                return ("propose_command", {"command": command, "reason": "Proposed by assistant"})

        code_block_match = re.search(r'```(?:bash|sh)?\s*\n(.+?)\n```', text, re.DOTALL)
        if code_block_match:
            command = code_block_match.group(1).strip()

            if command and '\n' not in command and len(command) < 200:
                logger.info(f"Extracted command from code block: {command}")
                return ("propose_command", {"command": command, "reason": "Command suggested by assistant"})

        return None

    def _execute_tool(self, name: str, params: dict) -> dict:

        logger.info(f"Executing tool: {name} with params: {params}")

        if name == "get_events":
            limit = int(params.get("limit", 10))
            events = self.db.get_events(limit=limit)
            return {"type": "result", "data": events}

        elif name == "get_flags":
            flags = self.db.get_flags(status=params.get("status"))
            return {"type": "result", "data": flags}

        elif name == "propose_command":
            return {
                "type": "proposal",
                "action": "run_command",
                "command": params.get("command", ""),
                "reason": params.get("reason", params.get("description", ""))
            }

        return {"type": "error", "message": f"Unknown tool: {name}"}

    def _detect_password_prompt(self, line: str) -> bool:

        prompts = ['[sudo]', 'Password:', 'password:', 'Password for', 'Enter passphrase']
        return any(p in line for p in prompts)

    def _is_sudo_command(self, command: str) -> bool:

        cmd = command.strip()
        return cmd.startswith('sudo ') and 'sudo -S' not in cmd

    def _run_command(self, command: str) -> Generator[dict, None, None]:

        if self._is_sudo_command(command):
            yield {
                "event": "terminal_input_needed",
                "prompt": "[sudo] password required",
                "command": command,
                "input_type": "password"
            }
            yield {"event": "terminal_done", "output": "", "needs_input": True}
            return

        try:
            process = subprocess.Popen(
                command, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True, bufsize=1
            )

            output_lines = []

            for line in process.stdout:
                clean_line = line.rstrip('\n')
                output_lines.append(clean_line)
                yield {"event": "terminal", "line": clean_line}

            process.wait()

            if not output_lines:
                yield {"event": "terminal", "line": "(no output)"}
                yield {"event": "terminal_done", "output": "(no output)"}
            else:
                yield {"event": "terminal_done", "output": "\n".join(output_lines)}

        except Exception as e:
            yield {"event": "error", "message": str(e)}

    def execute_with_password(self, command: str, password: str) -> Generator[dict, None, None]:

        import shlex

        yield {"event": "status", "text": "Running with authentication..."}

        if command.strip().startswith('sudo '):

            if 'sudo -S' not in command:
                command = command.replace('sudo ', 'sudo -S ', 1)

        try:
            process = subprocess.Popen(
                command, shell=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True, bufsize=1
            )

            process.stdin.write(password + '\n')
            process.stdin.flush()
            process.stdin.close()

            output_lines = []
            for line in process.stdout:
                clean_line = line.rstrip('\n')

                if password not in clean_line:
                    output_lines.append(clean_line)
                    yield {"event": "terminal", "line": clean_line}

            process.wait()
            yield {"event": "terminal_done", "output": "\n".join(output_lines)}

        except Exception as e:
            yield {"event": "error", "message": str(e)}

    def stream_chat(self, message: str) -> Generator[dict, None, None]:

        logger.info(f"USER: {message}")
        self.db.insert_chat_message("user", message)

        yield {"event": "status", "text": "Thinking..."}

        history = self.db.get_chat_messages(limit=20)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})

        for i in range(5):
            response = self._call_llm(messages)
            logger.debug(f"LLM [{i}]: {response}")

            tool = self._parse_tool_call(response)

            if tool:
                name, params = tool
                yield {"event": "status", "text": f"Calling {name}..."}
                logger.info(f"TOOL: {name} - {params}")

                result = self._execute_tool(name, params)

                if result["type"] == "proposal":

                    self.db.insert_chat_message("assistant", f"Proposing: {result['command']}")
                    yield {
                        "event": "proposal",
                        "action": result["action"],
                        "command": result["command"],
                        "reason": result["reason"]
                    }
                    yield {"event": "done"}
                    return
                else:

                    messages.append({"role": "assistant", "content": response})
                    messages.append({"role": "user", "content": f"Tool result: {json.dumps(result['data'])}"})
            else:

                clean = self._clean_response(response)
                self.db.insert_chat_message("assistant", clean)
                yield {"event": "text", "content": clean}
                yield {"event": "done"}
                return

        yield {"event": "text", "content": "Reached maximum tool calls."}
        yield {"event": "done"}

    def _clean_response(self, text: str) -> str:

        if "<think>" in text:
            text = text.split("</think>")[-1]

        import re

        text = re.sub(r'<\|[^|]+\|>', ' ', text)

        if text.strip().startswith("{") and text.strip().endswith("}"):
            try:
                data = json.loads(text.strip())
                if "command" in data:
                    return f"I propose running: `{data['command']}`\n\nReason: {data.get('reason', 'Investigate activity')}"
            except:
                pass

        return text.strip()

    def execute_command(self, command: str) -> Generator[dict, None, None]:

        yield {"event": "status", "text": "Running command..."}

        full_output = ""
        for chunk in self._run_command(command):
            yield chunk
            if chunk["event"] == "terminal_done":
                full_output = chunk["output"]

        if full_output:
            yield {"event": "status", "text": "Analyzing output..."}

            analysis_msg = f"Command output:\n```\n{full_output[:3000]}\n```\n\nProvide a brief analysis of this output."

            for chunk in self.stream_chat(analysis_msg):
                if chunk["event"] in ("text", "proposal"):
                    yield chunk
                elif chunk["event"] == "done":
                    break

        yield {"event": "done"}