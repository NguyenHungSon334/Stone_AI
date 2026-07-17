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
from fastapi.responses import JSONResponse, PlainTextResponse, Response

import admin
import config
import messenger
from bot_tools import lark_image

app = FastAPI(title="Chatbot Messenger (standalone)")
app.include_router(admin.router)
_BG: set = set()   # giữ ref mạnh task nền


@app.get("/healthz")
async def healthz():
    return {"ok": True, "model": config.MODEL, "configured": bool(config.PAGE_TOKEN)}


@app.get("/img/{file_token}")
async def product_image(file_token: str):
    """Proxy ảnh Lark -> FB tải được (Lark cần auth, không public). Cache 1 ngày."""
    try:
        data, ctype = await asyncio.to_thread(lark_image.download_media, file_token)
    except Exception as e:
        print(f"[img] tải Lark lỗi: {type(e).__name__}: {e}", file=sys.stderr)
        return PlainTextResponse("not found", status_code=404)
    return Response(content=data, media_type=ctype, headers={"Cache-Control": "public, max-age=86400"})


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
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    if not config.PAGE_TOKEN or not config.VERIFY_TOKEN or not config.APP_SECRET:
        print("[app] THIẾU cấu hình. Copy .env.example -> .env rồi điền PAGE_TOKEN/VERIFY_TOKEN/APP_SECRET.",
              file=sys.stderr)
    print(f"[app] Chatbot Messenger chạy port {config.PORT}, model={config.MODEL}", file=sys.stderr)
    uvicorn.run(app, host="0.0.0.0", port=config.PORT, log_level="info")
