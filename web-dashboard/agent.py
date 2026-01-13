

import json
import re
import logging
import requests
from typing import Generator
from database import get_db, Database
from tools import ToolRegistry, ProposalExecutor

chat_logger = logging.getLogger('chatbot')
chat_logger.setLevel(logging.DEBUG)
_fh = logging.FileHandler('chatbot.log')
_fh.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
chat_logger.addHandler(_fh)

SYSTEM_PROMPT = """You are Agent, an assistant for a Linux based server.
You help users with security monitoring AND general server maintenance tasks.

CRITICAL WORKFLOW:
1. For ANY command the user wants to run, use propose_command
2. The user will see your proposed command and click [Run] to approve
3. After they run it, you'll see the output and can analyze it
4. NEVER make up data - if you need info, propose a command to get it

You CAN help with:
- Security monitoring (checking logs, ports, IPs, processes)
- Network diagnostics (ping, traceroute, netstat, ss)
- System maintenance (apt, systemctl, df, free, uptime)
- File operations (ls, cat, tail, grep)
- ANY command the user requests

Available tools:
{tool_list}

EXAMPLES:
- Network test: propose_command("ping -c 5 google.com", "Test internet connectivity")
- Check ports: propose_command("ss -tlnp", "List all listening TCP ports")
- Check an IP: propose_command("host 192.168.1.1", "Reverse DNS lookup")
- Read logs: propose_command("tail -20 /var/log/auth.log", "Recent auth events")
- Check processes: propose_command("ps aux | grep python", "Find Python processes")
- Update packages: propose_command("sudo apt update", "Update package lists")

Keep responses concise. Do not use markdown tables - use simple lists instead."""

class ChatAgent:

    def __init__(self, db: Database = None):
        self.db = db or get_db()
        self.tools = ToolRegistry(self.db)
        self.executor = ProposalExecutor(self.db)

    def _get_system_prompt(self) -> str:

        tool_list = "\n".join(
            f"- {t['name']}: {t['description']}"
            for t in self.tools.get_tool_definitions()
        )
        return SYSTEM_PROMPT.format(tool_list=tool_list)

    def _call_llm(self, messages: list[dict]) -> str:

        import config
        api_url = self.db.get_config("llm_api_url", config.LLM_API_URL)
        api_key = self.db.get_config("llm_api_key", "")
        model = self.db.get_config("llm_model", config.LLM_MODEL)
        timeout = int(self.db.get_config("llm_timeout", str(config.LLM_TIMEOUT)))

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 1000
        }

        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=timeout)
            response.raise_for_status()
            return response.json()['choices'][0]['message']['content']
        except Exception as e:
            return f"Error calling LLM: {e}"

    def _parse_tool_call(self, content: str) -> tuple[str, dict] | None:

        qwen_match = re.search(r'to=tool\.(\w+).*?<\|message\|>({.*?})', content, re.DOTALL)
        if qwen_match:
            try:
                tool_name = qwen_match.group(1)
                params = json.loads(qwen_match.group(2))
                return (tool_name, params)
            except json.JSONDecodeError:
                pass

        # Format 1b: Qwen alternate: to=NAME (without 'tool.' prefix)
        qwen_alt = re.search(r'to=(\w+).*?<\|message\|>({.*?})', content, re.DOTALL)
        if qwen_alt:
            try:
                tool_name = qwen_alt.group(1)
                if tool_name in self.tools.tools:
                    params = json.loads(qwen_alt.group(2))
                    return (tool_name, params)
            except json.JSONDecodeError:
                pass

        tool_match = re.search(r'<tool>(\w+)</tool>', content)
        params_match = re.search(r'<params>({.*?})</params>', content, re.DOTALL)

        if tool_match and params_match:
            try:
                tool_name = tool_match.group(1)
                params = json.loads(params_match.group(1))
                return (tool_name, params)
            except json.JSONDecodeError:
                pass

        # Format 3: Simple function call syntax: tool_name({"key": "value"})
        func_match = re.search(r'(\w+)\(({.*?})\)', content, re.DOTALL)
        if func_match:
            try:
                tool_name = func_match.group(1)
                if tool_name in self.tools.tools:
                    params = json.loads(func_match.group(2))
                    return (tool_name, params)
            except json.JSONDecodeError:
                pass

        return None

    def _clean_response(self, content: str) -> str:

        content = re.sub(r'<\|channel\|>.*?<\|message\|>\{.*?\}', '', content, flags=re.DOTALL)
        content = re.sub(r'<\|channel\|>.*$', '', content, flags=re.DOTALL)

        content = re.sub(r'<\|[^|]+\|>[^<{]*', '', content)

        content = re.sub(r'\{["\'].*?["\']:\s*["\'].*?["\']\s*\}', '', content, flags=re.DOTALL)
        content = re.sub(r'["\'],\s*["\'][^"\']+["\']\s*:\s*["\'][^"\']*["\'].*?\}', '', content)

        content = re.sub(r'<tool>.*?</tool>', '', content)
        content = re.sub(r'<params>.*?</params>', '', content, flags=re.DOTALL)

        content = re.sub(r'\s+', ' ', content)
        return content.strip()

    def chat(self, user_message: str) -> Generator[dict, None, None]:

        self.db.insert_chat_message("user", user_message)
        chat_logger.info(f"USER: {user_message}")

        yield {"type": "status", "text": "Thinking..."}

        history = self.db.get_chat_messages(limit=20)
        messages = [{"role": "system", "content": self._get_system_prompt()}]
        for msg in history:
            messages.append({"role": msg["role"], "content": msg["content"]})

        for iteration in range(5):
            llm_response = self._call_llm(messages)
            chat_logger.debug(f"LLM RAW [{iteration}]: {llm_response}")

            tool_call = self._parse_tool_call(llm_response)

            if tool_call:
                tool_name, params = tool_call
                yield {"type": "status", "text": f"Calling {tool_name}..."}
                yield {"type": "tool_call", "tool": tool_name, "params": params}

                result = self.tools.execute_tool(tool_name, params)

                if result["type"] == "proposal":

                    clean_msg = self._clean_response(llm_response)
                    if clean_msg:
                        self.db.insert_chat_message("assistant", clean_msg, metadata=result)
                    chat_logger.info(f"PROPOSAL: {json.dumps(result)}")

                    yield {"type": "proposal", "action": result["action"], "data": result["data"]}
                    return
                else:

                    yield {"type": "tool_result", "data": result}
                    yield {"type": "status", "text": "Analyzing results..."}
                    messages.append({"role": "assistant", "content": llm_response})
                    messages.append({"role": "user", "content": f"Tool result: {json.dumps(result)}"})
            else:

                clean_msg = self._clean_response(llm_response)
                if not clean_msg:
                    clean_msg = llm_response
                self.db.insert_chat_message("assistant", clean_msg)
                yield {"type": "status", "text": ""}
                yield {"type": "text", "content": clean_msg}
                return

        yield {"type": "text", "content": "I've reached the maximum number of tool calls. Please continue the conversation."}

    def execute_proposal(self, action: str, data: dict) -> dict:

        result = self.executor.execute(action, data)

        self.db.insert_chat_message(
            "system",
            f"Executed {action}: {json.dumps(result)}",
            metadata={"action": action, "result": result}
        )
        return result
