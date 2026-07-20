"""
Webhook Messenger standalone. Chạy: python app.py   (hoặc uvicorn app:app --port 7900)

GET  /webhook/messenger : FB verify (echo hub.challenge)
POST /webhook/messenger : nhận tin -> verify chữ ký -> trả lời nền qua Claude CLI -> 200 ngay
GET  /healthz           : sống chưa

Trả 200 NHANH, trả lời chạy nền (FB đòi 200 trong ~20s, chậm là retry bão).
"""
import asyncio
import json
import sys

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

import admin
import alerts
import config
import messenger

app = FastAPI(title="Chatbot Messenger (standalone)")
app.include_router(admin.router)
_BG: set = set()   # giữ ref mạnh task nền


async def _followup_loop():
    """Nền: mỗi FOLLOWUP_CHECK_MIN phút quét khách im -> nhắc nhẹ."""
    while True:
        await asyncio.sleep(max(1, config.FOLLOWUP_CHECK_MIN) * 60)
        try:
            await messenger.run_followups()
            await messenger.run_missed_check()   # cùng vòng quét, khỏi đẻ thêm loop
        except Exception as e:
            print(f"[followup] vòng quét lỗi: {type(e).__name__}: {e}", file=sys.stderr)
            await asyncio.to_thread(
                alerts.alert, f"loop:followup:{type(e).__name__}",
                f"⚠️ VÒNG QUÉT FOLLOW-UP LỖI - khách im không được nhắc lại.\n{type(e).__name__}: {e}")


async def _tunnel_watch_loop():
    """Nền: mỗi TUNNEL_CHECK_MIN phút ping PUBLIC_URL từ ngoài -> tunnel đứt thì báo Lark."""
    while True:
        await asyncio.sleep(max(1, config.TUNNEL_CHECK_MIN) * 60)
        try:
            await messenger.run_tunnel_check()
        except Exception as e:
            print(f"[tunnel] vòng check lỗi: {type(e).__name__}: {e}", file=sys.stderr)
            # Vòng canh chết = mất luôn cảnh báo tunnel chết. Im lặng ở đây là nguy hiểm nhất.
            await asyncio.to_thread(
                alerts.alert, f"loop:tunnel:{type(e).__name__}",
                f"⚠️ VÒNG CANH TUNNEL LỖI - KHÔNG còn cảnh báo khi tunnel chết.\n{type(e).__name__}: {e}")


def _spawn(coro):
    t = asyncio.create_task(coro)
    _BG.add(t)
    t.add_done_callback(_BG.discard)


def _missing_config() -> list[str]:
    """Biến .env thiếu mà bot KHÔNG chạy đúng được. Thiếu -> bot im ru, dễ tưởng 'chưa có khách'."""
    need = {"MSGR_PAGE_TOKEN": config.PAGE_TOKEN, "MSGR_VERIFY_TOKEN": config.VERIFY_TOKEN,
            "MSGR_APP_SECRET": config.APP_SECRET, "GEMINI_API_KEY": config.GEMINI_API_KEY}
    return [k for k, v in need.items() if not v]


@app.on_event("startup")
async def _startup_selfcheck():
    """Báo 1 lần lúc khởi động: thiếu cấu hình gì, kênh cảnh báo sống chưa.
    Không có bước này thì cấu hình sai chỉ lộ khi khách nhắn mà không ai trả lời."""
    missing = _missing_config()
    print(f"[app] cấu hình: {'THIẾU ' + ', '.join(missing) if missing else 'đủ'} | "
          f"Firebase {'bật' if config.FIREBASE_CRED and config.FIREBASE_DB_URL else 'TẮT'} | "
          f"CRM {'bật' if config.LARK_CRM_APP_TOKEN and config.LARK_APP_ID else 'TẮT'}", file=sys.stderr)
    if not config.LARK_WEBHOOK_URL:
        print("[app] CHƯA cấu hình LARK_WEBHOOK_URL -> mọi cảnh báo lỗi sẽ BỊ NUỐT.", file=sys.stderr)
        return
    if not missing:
        return          # khởi động sạch thì IM: container crash-loop mà báo mỗi vòng = spam Lark
    await asyncio.to_thread(
        alerts.notify,
        f"🔴 BOT KHỞI ĐỘNG THIẾU CẤU HÌNH: {', '.join(missing)}\n"
        f"Model: {config.MODEL} | Port: {config.PORT}\n"
        f"➡️ Bot sẽ KHÔNG trả lời khách cho tới khi điền đủ.")


@app.on_event("startup")
async def _start_bg():
    if config.FOLLOWUP_ENABLED:
        _spawn(_followup_loop())
        print(f"[app] follow-up bật: nhắc sau {config.FOLLOWUP_AFTER_H}h, quét mỗi {config.FOLLOWUP_CHECK_MIN}p",
              file=sys.stderr)
    if config.TUNNEL_WATCH_ENABLED and config.PUBLIC_URL:
        _spawn(_tunnel_watch_loop())
        print(f"[app] canh tunnel bật: ping {config.PUBLIC_URL} mỗi {config.TUNNEL_CHECK_MIN}p",
              file=sys.stderr)


@app.get("/healthz")
async def healthz():
    """Thêm `alerts`: kênh cảnh báo hỏng thì KHÔNG tự báo được (báo qua chính nó), phải soi ở đây."""
    return {"ok": True, "model": config.MODEL, "configured": bool(config.PAGE_TOKEN),
            "missing_config": _missing_config(), "alerts": alerts.status()}


@app.get("/webhook/messenger")
async def verify(request: Request):
    p = request.query_params
    challenge = messenger.verify_webhook(p.get("hub.mode"), p.get("hub.verify_token"),
                                         p.get("hub.challenge"))
    if challenge is None:
        return PlainTextResponse("forbidden", status_code=403)
    return PlainTextResponse(challenge)


@app.post("/webhook/messenger")
async def events(request: Request):
    raw = await request.body()
    if not messenger.verify_signature(raw, request.headers.get("X-Hub-Signature-256", "")):
        return PlainTextResponse("bad signature", status_code=403)
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return JSONResponse({"ok": True})
    for psid, text in messenger.parse_events(payload):
        t = asyncio.create_task(messenger.handle_event(psid, text))
        _BG.add(t)
        t.add_done_callback(_BG.discard)
    for comment_id, from_id in messenger.parse_comment_events(payload):
        t = asyncio.create_task(messenger.handle_comment(comment_id, from_id))
        _BG.add(t)
        t.add_done_callback(_BG.discard)
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    if not config.PAGE_TOKEN or not config.VERIFY_TOKEN or not config.APP_SECRET:
        print("[app] THIẾU cấu hình. Copy .env.example -> .env rồi điền PAGE_TOKEN/VERIFY_TOKEN/APP_SECRET.",
              file=sys.stderr)
    print(f"[app] Chatbot Messenger chạy port {config.PORT}, model={config.MODEL}", file=sys.stderr)
    uvicorn.run(app, host="0.0.0.0", port=config.PORT, log_level="info")
