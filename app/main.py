import asyncio
import time
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from loguru import logger
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.loguru import LoguruIntegration

from fastapi.middleware.cors import CORSMiddleware

from app.admin import router as admin_router

# ---------------------------------------------------------------------------
# Webhook deduplication
# ---------------------------------------------------------------------------

# { (sender_id, timestamp_ms): seen_at } — prevents double-processing Messenger retries
_seen_events: dict[tuple[str, int], float] = {}
_DEDUP_TTL = 60.0  # seconds


def _is_duplicate(sender_id: str, timestamp_ms: int) -> bool:
    key = (sender_id, timestamp_ms)
    now = time.monotonic()
    # Evict stale entries
    stale = [k for k, t in _seen_events.items() if now - t > _DEDUP_TTL]
    for k in stale:
        del _seen_events[k]
    if key in _seen_events:
        return True
    _seen_events[key] = now
    return False


from app.config import settings
from app import orchestrator
from app.http_client import close_http_client
from app.logging_setup import configure_logging
from app.messenger import extract_messages, send_text, verify_signature
from app.middleware import RequestIdMiddleware, is_rate_limited
from app.dev_chat import router as dev_router


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()

    if settings.sentry_dsn:
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            integrations=[FastApiIntegration(), LoguruIntegration()],
            traces_sample_rate=0.1,   # 10% of requests for performance tracing
            send_default_pii=False,
        )
        logger.info("Sentry initialised env={}", settings.environment)

    logger.info("SpiritStone AI starting env={}", settings.environment)

    # Warm up embedding model to avoid cold-start latency on first user message
    try:
        from app.llm import embed
        await embed("warm up")
        logger.info("embedding warm-up done")
    except Exception:
        logger.warning("embedding warm-up failed (non-fatal)")

    yield
    await close_http_client()
    logger.info("SpiritStone AI shutdown")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="SpiritStone AI", version="0.1.0", lifespan=lifespan)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
app.include_router(admin_router)

if settings.environment != "production":
    app.include_router(dev_router)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    from app.db.supabase import get_client
    try:
        db = await get_client()
        await db.table("conversations").select("messenger_user_id").limit(1).execute()
        db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "db": db_ok}


# ---------------------------------------------------------------------------
# Messenger Webhook
# ---------------------------------------------------------------------------

@app.get("/webhook")
async def webhook_verify(
    hub_mode: str | None = None,
    hub_verify_token: str | None = None,
    hub_challenge: str | None = None,
):
    if hub_mode == "subscribe" and hub_verify_token == settings.messenger_verify_token:
        logger.info("webhook verified")
        return int(hub_challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook")
async def webhook_receive(request: Request, background_tasks: BackgroundTasks):
    body_bytes = await request.body()

    sig = request.headers.get("x-hub-signature-256", "")
    if settings.messenger_app_secret and not verify_signature(body_bytes, sig):
        raise HTTPException(status_code=403, detail="Invalid signature")

    body = await request.json()
    if body.get("object") != "page":
        raise HTTPException(status_code=400, detail="Not a page event")

    events = extract_messages(body)
    queued = 0
    for event in events:
        if is_rate_limited(event["sender_id"]):
            logger.warning("rate limited sender={}", event["sender_id"])
            continue
        if _is_duplicate(event["sender_id"], event["timestamp"]):
            logger.warning("duplicate event sender={} ts={}", event["sender_id"], event["timestamp"])
            continue
        background_tasks.add_task(handle_message, event["sender_id"], event["text"])
        queued += 1

    return {"status": "ok", "queued": queued}


_HANDLE_TIMEOUT = 25.0
_TIMEOUT_REPLY = "Em xin lỗi, xử lý quá lâu. Anh/chị vui lòng thử lại sau ạ!"


async def handle_message(sender_id: str, text: str) -> None:
    logger.info("msg sender={} text={!r}", sender_id, text)
    try:
        await asyncio.wait_for(orchestrator.run(sender_id, text), timeout=_HANDLE_TIMEOUT)
    except asyncio.TimeoutError:
        logger.error("handle_message timeout sender={}", sender_id)
        try:
            await send_text(sender_id, _TIMEOUT_REPLY)
        except Exception:
            pass
    except Exception:
        logger.exception("handle_message failed sender={}", sender_id)
        try:
            await send_text(sender_id, "Em xin lỗi, đã có lỗi xảy ra. Anh/chị vui lòng thử lại ạ!")
        except Exception:
            pass
