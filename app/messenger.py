"""
Facebook Messenger webhook handler and send utilities.
"""
import asyncio
import hashlib
import hmac
from loguru import logger
from app.config import settings
from app.http_client import get_http_client

_SEND_RETRIES = 2


GRAPH_API = "https://graph.facebook.com/v25.0"
_MSG_MAX = 1900  # Messenger hard limit is 2000; stay safely under


def _split_message(text: str) -> list[str]:
    """Split text into chunks ≤ _MSG_MAX chars, breaking at newlines then sentence boundaries."""
    if len(text) <= _MSG_MAX:
        return [text]
    chunks: list[str] = []
    while len(text) > _MSG_MAX:
        split_at = text.rfind("\n", 0, _MSG_MAX)
        if split_at == -1:
            split_at = text.rfind(". ", 0, _MSG_MAX)
        if split_at == -1:
            split_at = _MSG_MAX
        chunks.append(text[:split_at].rstrip())
        text = text[split_at:].lstrip()
    if text:
        chunks.append(text)
    return chunks


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
    """Send a plain text message via Messenger Send API, splitting if over limit."""
    for chunk in _split_message(text):
        await _send_chunk(recipient_id, chunk)


async def _send_chunk(recipient_id: str, text: str) -> None:
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
        "messaging_type": "RESPONSE",
    }
    last_err: Exception | None = None
    for attempt in range(_SEND_RETRIES + 1):
        try:
            r = await get_http_client().post(
                f"{GRAPH_API}/me/messages",
                params={"access_token": settings.messenger_page_token},
                json=payload,
            )
            if r.status_code == 200:
                return
            if 400 <= r.status_code < 500:
                logger.error("Messenger send 4xx psid={} status={} body={}", recipient_id, r.status_code, r.text)
                r.raise_for_status()
            logger.warning("Messenger send 5xx psid={} status={} attempt={}", recipient_id, r.status_code, attempt + 1)
            last_err = Exception(f"status {r.status_code}")
        except Exception as e:
            import httpx
            if not isinstance(e, (httpx.TimeoutException, httpx.NetworkError)):
                raise
            logger.warning("Messenger send network error psid={} attempt={} err={}", recipient_id, attempt + 1, e)
            last_err = e
        if attempt < _SEND_RETRIES:
            await asyncio.sleep(1.0)
    raise RuntimeError(f"Messenger send failed after {_SEND_RETRIES + 1} attempts: {last_err}")


async def send_typing_on(recipient_id: str) -> None:
    """Send typing indicator to show the bot is processing."""
    payload = {
        "recipient": {"id": recipient_id},
        "sender_action": "typing_on",
    }
    try:
        await get_http_client().post(
            f"{GRAPH_API}/me/messages",
            params={"access_token": settings.messenger_page_token},
            json=payload,
        )
    except Exception:
        pass  # non-critical — never fail a message over a missing typing indicator


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
