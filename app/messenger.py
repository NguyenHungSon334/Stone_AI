"""
Facebook Messenger webhook handler and send utilities.
"""
import hashlib
import hmac
import httpx
from loguru import logger
from app.config import settings


GRAPH_API = "https://graph.facebook.com/v19.0"


def verify_signature(body: bytes, x_hub_signature: str) -> bool:
    """Verify X-Hub-Signature-256 from Facebook."""
    if not x_hub_signature.startswith("sha256="):
        return False
    expected = hmac.new(
        settings.messenger_app_secret.encode(),
        body,
        hashlib.sha256,
    ).hexdigest()
    received = x_hub_signature[len("sha256="):]
    return hmac.compare_digest(expected, received)


async def send_text(recipient_id: str, text: str) -> None:
    """Send a plain text message via Messenger Send API."""
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
        "messaging_type": "RESPONSE",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{GRAPH_API}/me/messages",
            params={"access_token": settings.messenger_page_token},
            json=payload,
        )
        if r.status_code != 200:
            logger.error(
                "Messenger send failed psid={} status={} body={}",
                recipient_id, r.status_code, r.text,
            )
            r.raise_for_status()


async def send_quick_replies(recipient_id: str, text: str, options: list[str]) -> None:
    """Send text with quick reply buttons (max 13)."""
    quick_replies = [
        {"content_type": "text", "title": opt, "payload": opt}
        for opt in options[:13]
    ]
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text, "quick_replies": quick_replies},
        "messaging_type": "RESPONSE",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{GRAPH_API}/me/messages",
            params={"access_token": settings.messenger_page_token},
            json=payload,
        )
        r.raise_for_status()


def extract_messages(body: dict) -> list[dict]:
    """
    Extract messaging events from a Messenger webhook payload.
    Returns list of dicts: {sender_id, text, timestamp}.
    Skips non-text events (attachments, echoes, reads, etc.).
    """
    events = []
    for entry in body.get("entry", []):
        for msg in entry.get("messaging", []):
            if msg.get("message", {}).get("is_echo"):
                continue
            text = msg.get("message", {}).get("text")
            if text:
                events.append({
                    "sender_id": msg["sender"]["id"],
                    "text": text,
                    "timestamp": msg.get("timestamp", 0),
                })
    return events
