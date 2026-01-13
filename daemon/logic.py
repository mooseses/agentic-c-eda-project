import json
import requests
import config

class ReasoningEngine:
    def __init__(self, db=None):
        self.db = db
        
    def _get_config(self, key: str, default: str) -> str:
        if self.db:
            return self.db.get_config(key, default)
        return default
    
    def _get_system_prompt(self) -> str:
        sensitivity = int(self._get_config("sensitivity", "7"))
        
        return f"""You are a security analyst for a Linux server.
Analyze the following security events and determine if they should be flagged for user attention.

Sensitivity level: {sensitivity}/10 (higher = more alerts)

IMPORTANT: You must respond with ONLY valid JSON, no other text.

Response format:
{{
    "flagged": true/false,
    "severity": "info" | "warning" | "critical",
    "summary": "Brief description of what happened",
    "suggested_actions": ["action1", "action2"]
}}

Rules:
- flagged=false for normal traffic, routine operations
- flagged=true with severity="info" for minor anomalies
- flagged=true with severity="warning" for suspicious but not urgent
- flagged=true with severity="critical" for likely attacks or breaches
- Be concise in summaries
- Never auto-block, only flag for user review"""
    
    def analyze_batch(self, events: list[str]) -> dict:
        if not events:
            return {"flagged": False, "severity": "info", "summary": "No events to analyze", "suggested_actions": []}
        
        # Build prompt
        events_text = "\n".join(f"- {e}" for e in events)
        prompt = f"Events to analyze:\n{events_text}"
        
        # Call LLM
        api_url = self._get_config("llm_api_url", config.LLM_API_URL)
        api_key = self._get_config("llm_api_key", "")
        model = self._get_config("llm_model", config.LLM_MODEL)
        timeout = int(self._get_config("llm_timeout", str(config.LLM_TIMEOUT)))
        
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": self._get_system_prompt()},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 500
        }
        
        try:
            response = requests.post(api_url, headers=headers, json=payload, timeout=timeout)
            response.raise_for_status()
            content = response.json()['choices'][0]['message']['content']
            
            if '<think>' in content:
                content = content.split('</think>')[-1].strip()
            
            start = content.find('{')
            end = content.rfind('}') + 1
            if start >= 0 and end > start:
                result = json.loads(content[start:end])
                return {
                    "flagged": result.get("flagged", False),
                    "severity": result.get("severity", "info"),
                    "summary": result.get("summary", "Analysis complete"),
                    "suggested_actions": result.get("suggested_actions", [])
                }
            
        except Exception as e:
            pass
        
        return {
            "flagged": True,
            "severity": "warning",
            "summary": f"Analysis inconclusive for {len(events)} event(s)",
            "suggested_actions": ["Review events manually"]
        }
