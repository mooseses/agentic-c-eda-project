

import asyncio
import logging
import httpx
from typing import Optional

logger = logging.getLogger(__name__)

class NotificationService:

    def __init__(self, db):
        self.db = db

    def _get_telegram_config(self) -> tuple[str, str]:

        token = self.db.get_config("notification_telegram_token", "")
        chat_id = self.db.get_config("notification_telegram_chat_id", "")
        return token, chat_id

    def _get_bark_config(self) -> str:

        return self.db.get_config("notification_bark_url", "")

    async def send_telegram(self, message: str, title: str = "🚨 Agent Alert") -> bool:

        token, chat_id = self._get_telegram_config()

        if not token or not chat_id:
            logger.warning("Telegram not configured (missing token or chat_id)")
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        full_message = f"*{title}*\n\n{message}"

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url, json={
                    "chat_id": chat_id,
                    "text": full_message,
                    "parse_mode": "Markdown"
                })

                if response.status_code == 200:
                    logger.info(f"Telegram notification sent to {chat_id}")
                    return True
                else:
                    logger.error(f"Telegram API error: {response.status_code} - {response.text}")
                    return False

        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False

    async def send_bark(self, title: str, body: str, group: str = "Agent") -> bool:

        bark_url = self._get_bark_config()

        if not bark_url:
            logger.warning("Bark not configured (missing URL)")
            return False

        base_url = bark_url.rstrip('/')

        import urllib.parse
        encoded_title = urllib.parse.quote(title)
        encoded_body = urllib.parse.quote(body)

        url = f"{base_url}/{encoded_title}/{encoded_body}?group={group}"

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(url)

                if response.status_code == 200:
                    logger.info("Bark notification sent")
                    return True
                else:
                    logger.error(f"Bark API error: {response.status_code} - {response.text}")
                    return False

        except Exception as e:
            logger.error(f"Failed to send Bark notification: {e}")
            return False

    async def send_alert(self, flag: dict) -> dict:

        summary = flag.get("summary", "Unknown alert")
        severity = flag.get("severity", "WARNING").upper()

        title = f"🚨 {severity}: Security Alert"
        message = f"{summary}\n\nSeverity: {severity}"

        results = {}

        telegram_result = await self.send_telegram(message, title)
        results["telegram"] = telegram_result

        bark_result = await self.send_bark(title, summary)
        results["bark"] = bark_result

        return results

    async def test_telegram(self) -> tuple[bool, str]:

        token, chat_id = self._get_telegram_config()

        if not token:
            return False, "Telegram token not configured"
        if not chat_id:
            return False, "Telegram chat ID not configured"

        success = await self.send_telegram("🔔 Test notification from Agentic C-EDA", "Test Alert")

        if success:
            return True, "Telegram notification sent successfully"
        else:
            return False, "Failed to send Telegram notification"

    async def test_bark(self) -> tuple[bool, str]:

        bark_url = self._get_bark_config()

        if not bark_url:
            return False, "Bark URL not configured"

        success = await self.send_bark("Agent Test", "Test notification from Agentic C-EDA")

        if success:
            return True, "Bark notification sent successfully"
        else:
            return False, "Failed to send Bark notification"
