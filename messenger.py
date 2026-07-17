"""
Giao thức Messenger: verify webhook, verify chữ ký, bóc tin, gửi trả, rate-limit + đồng thời.
Port gọn từ Javis OS (server/messenger_bot.py) - bỏ phần dính settings, dùng thẳng config.
"""
import asyncio
import hashlib
import hmac
import re
import sys
import time

import httpx

import brain
import config
import stats
from bot_tools import lark_image

_MAX_IMAGES_PER_MSG = 4   # khớp brain._MAX_NEW_IMAGES

_SEND_API = "https://graph.facebook.com/{ver}/me/messages"
_TEXT_MAX = 2000
_HANDOFF = "<<HANDOFF>>"   # marker persona chèn khi chuyển chuyên gia; code bóc ra, khách không thấy
_IMG_RE = re.compile(r"<<IMG:([^>]+)>>")   # marker ảnh: file_token Lark, bóc ra gửi ảnh riêng
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?84|0)\d{9,10}(?!\d)")   # SĐT VN trong tin khách


def _find_phone(text: str) -> str | None:
    """SĐT VN đầu tiên trong tin (đã nén khoảng trắng/chấm/gạch). None nếu không có."""
    compact = re.sub(r"[\s.\-()]", "", text or "")
    m = _PHONE_RE.search(compact)
    return m.group(0) if m else None


def _extract_handoff(reply: str) -> tuple[str, bool]:
    """Bóc marker handoff khỏi tin gửi khách. Trả (reply_sạch, có_handoff)."""
    if _HANDOFF in reply:
        return (reply.replace(_HANDOFF, "").strip(), True)
    return (reply, False)


def _extract_images(reply: str) -> tuple[str, list[str]]:
    """Bóc marker <<IMG:token>> khỏi text. Trả (text_sạch, [file_token])."""
    tokens = _IMG_RE.findall(reply)
    clean = _IMG_RE.sub("", reply).strip()
    return (clean, tokens)


async def notify_admins(text: str) -> None:
    """Gửi 1 tin tới mọi admin (handoff / báo lỗi). Không có admin thì thôi."""
    for uid in config.ADMIN_UIDS:
        await send_text(uid, text)


# Cache tên FB theo psid (RAM). Dùng cho thông báo admin + trang admin.
_NAME_CACHE: dict[str, str] = {}


async def _names_from_conversations() -> None:
    """Nạp tên MỌI khách từ /me/conversations vào cache.

    API profile /{psid} cần Advanced Access mới trả tên khách lạ; endpoint conversations
    của chính Page thì trả tên đủ ngay ở Standard Access -> dùng làm nguồn chính."""
    url = f"https://graph.facebook.com/{config.GRAPH_VER}/me/conversations"
    params = {"fields": "participants", "limit": 100, "access_token": config.PAGE_TOKEN}
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            for _ in range(5):                        # tối đa 5 trang (500 hội thoại)
                r = await c.get(url, params=params)
                d = r.json()
                for conv in d.get("data", []) or []:
                    for p in (conv.get("participants", {}).get("data") or []):
                        pid, name = str(p.get("id", "")), p.get("name", "")
                        if pid and name:
                            _NAME_CACHE[pid] = name
                nxt = (d.get("paging") or {}).get("next")
                if not nxt:
                    break
                url, params = nxt, {}                 # link next đã kèm đủ query
    except Exception as e:
        print(f"[profile] đọc conversations lỗi: {type(e).__name__}: {e}", file=sys.stderr)


async def profile_name(psid: str) -> str:
    """Tên đầy đủ của khách. Nguồn: /me/conversations (cache); miss thì nạp lại 1 lần."""
    if not config.PAGE_TOKEN:
        return ""
    if psid not in _NAME_CACHE:
        await _names_from_conversations()
    return _NAME_CACHE.setdefault(psid, "")   # vẫn không có -> cache rỗng, khỏi gọi lặp


async def _label(psid: str) -> str:
    """Nhãn khách trong thông báo admin: 'Tên (psid 123)' hoặc 'psid 123' nếu không có tên."""
    name = await profile_name(psid)
    return f"{name} (psid {psid})" if name else f"psid {psid}"


def verify_webhook(mode, token, challenge) -> str | None:
    """GET verify của FB: echo challenge nếu verify_token khớp (hằng-thời-gian)."""
    if (mode == "subscribe" and token and config.VERIFY_TOKEN
            and hmac.compare_digest(str(token), config.VERIFY_TOKEN)):
        return str(challenge or "")
    return None


def verify_signature(raw_body: bytes, header_sig: str) -> bool:
    """POST thật: X-Hub-Signature-256 = 'sha256=' + HMAC_SHA256(app_secret, body). Fail-closed."""
    if not config.APP_SECRET or not header_sig or not header_sig.startswith("sha256="):
        return False
    expected = hmac.new(config.APP_SECRET.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_sig.split("=", 1)[1].strip())


def parse_events(payload: dict):
    """[(psid, text)] từ webhook body. Bỏ echo của page, sự kiện không có text."""
    out = []
    if not isinstance(payload, dict) or payload.get("object") != "page":
        return out
    for entry in payload.get("entry", []) or []:
        for ev in (entry.get("messaging") or []):
            msg = ev.get("message") or {}
            if msg.get("is_echo"):
                continue
            psid = (ev.get("sender") or {}).get("id")
            text = msg.get("text")
            if psid and text:
                out.append((str(psid), str(text)))
    return out


def _split_text(text: str, n: int = _TEXT_MAX) -> list[str]:
    """Tách text dài > n thành nhiều đoạn, ưu tiên cắt ở xuống dòng/khoảng trắng gần cuối."""
    text = str(text)
    if len(text) <= n:
        return [text]
    out = []
    while len(text) > n:
        cut = text.rfind("\n", 0, n)
        if cut < n // 2:
            cut = text.rfind(" ", 0, n)
        if cut < n // 2:
            cut = n                       # không có ranh giới -> cắt cứng
        out.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        out.append(text)
    return out


async def send_text(psid: str, text: str) -> None:
    if not (config.PAGE_TOKEN and psid and text):
        return
    url = _SEND_API.format(ver=config.GRAPH_VER)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as c:
            for chunk in _split_text(text):
                body = {"recipient": {"id": psid}, "messaging_type": "RESPONSE",
                        "message": {"text": chunk}}
                r = await c.post(url, params={"access_token": config.PAGE_TOKEN}, json=body)
                if r.status_code >= 400:
                    print(f"[send] {r.status_code}: {r.text[:300]}", file=sys.stderr)
                    break
    except Exception as e:
        print(f"[send] {type(e).__name__}: {e}", file=sys.stderr)


async def send_image(psid: str, file_token: str) -> None:
    """Gửi ảnh cho khách qua URL proxy công khai (/img). FB tự tải ảnh từ URL này."""
    if not (config.PAGE_TOKEN and psid and file_token):
        return
    if not config.PUBLIC_URL:
        print("[img] thiếu PUBLIC_URL, không gửi được ảnh", file=sys.stderr)
        return
    url = _SEND_API.format(ver=config.GRAPH_VER)
    img_url = f"{config.PUBLIC_URL}/img/{file_token}"
    body = {"recipient": {"id": psid}, "messaging_type": "RESPONSE",
            "message": {"attachment": {"type": "image",
                                       "payload": {"url": img_url, "is_reusable": True}}}}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as c:
            r = await c.post(url, params={"access_token": config.PAGE_TOKEN}, json=body)
            if r.status_code >= 400:
                print(f"[img] {r.status_code}: {r.text[:300]}", file=sys.stderr)
    except Exception as e:
        print(f"[img] {type(e).__name__}: {e}", file=sys.stderr)


async def send_action(psid: str, action: str = "typing_on") -> None:
    if not (config.PAGE_TOKEN and psid):
        return
    url = _SEND_API.format(ver=config.GRAPH_VER)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as c:
            await c.post(url, params={"access_token": config.PAGE_TOKEN},
                         json={"recipient": {"id": psid}, "sender_action": action})
    except Exception:
        pass


_SEM = asyncio.Semaphore(config.MAX_CONCURRENT)   # trần đồng thời riêng của bot
_LAST_SEEN: dict[str, float] = {}                  # psid -> ts tin gần nhất
_RATE_MAX = 5000                                   # trần size dict; vượt thì dọn entry cũ


def _rate_ok(psid: str) -> bool:
    now = time.monotonic()
    if len(_LAST_SEEN) > _RATE_MAX:                 # dọn psid im > 1h -> dict không phình vô hạn
        cutoff = now - 3600
        for k in [k for k, v in _LAST_SEEN.items() if v < cutoff]:
            _LAST_SEEN.pop(k, None)
    if now - _LAST_SEEN.get(psid, 0.0) < config.PER_PSID_RATE_S:
        return False
    _LAST_SEEN[psid] = now
    return True


async def handle_event(psid: str, text: str) -> None:
    """1 tin (chạy nền): rate-limit → semaphore → Claude trả lời → gửi lại. Lỗi = xin lỗi lịch sự."""
    if not _rate_ok(psid):
        stats.log_event("rate_limited", psid)
        return
    async with _SEM:
        await send_action(psid, "typing_on")
        is_new = brain.is_new_customer(psid)           # check TRƯỚC khi brain ghi lịch sử
        t0 = time.monotonic()
        try:
            reply = await brain.answer(psid, text)
        except Exception as e:
            # Lỗi: KHÔNG trả lời khách gì cả, chỉ báo admin.
            print(f"[handle] {type(e).__name__}: {e}", file=sys.stderr)
            stats.log_event("error", psid, note=f"{type(e).__name__}: {e}")
            await notify_admins(f"⚠️ LỖI BOT khi trả lời khách {await _label(psid)}\n"
                                f"{type(e).__name__}: {e}\nTin khách: {text[:200]}")
            return
        reply, handoff = _extract_handoff(reply)
        reply, img_tokens = _extract_images(reply)
        if handoff:
            # Handoff: gửi phiếu xác nhận cho KHÁCH trước, rồi báo admin để người thật tiếp quản.
            stats.log_event("handoff", psid, duration_s=time.monotonic() - t0)
            if reply:
                await send_text(psid, reply)
            await notify_admins(f"🔔 KHÁCH CẦN CHUYÊN GIA: {await _label(psid)}\n\n{reply}")
            return
        stats.log_event("ok", psid, duration_s=time.monotonic() - t0)
        img_tokens = img_tokens[:_MAX_IMAGES_PER_MSG]
        if img_tokens:
            # WARM ảnh trước khi gửi bất kỳ tin nào: tải Lark về cache xong hết mới nhắn.
            # -> FB fetch /img trúng cache, text + ảnh tới khách sát nhau, không "nhắn xong chờ ảnh".
            warmed = await asyncio.gather(
                *(asyncio.to_thread(lark_image.download_media, t) for t in img_tokens),
                return_exceptions=True)
            ok_tokens = []
            for tok, w in zip(img_tokens, warmed):
                if isinstance(w, Exception):
                    print(f"[img] warm lỗi token {tok[:12]}...: {type(w).__name__}: {w}", file=sys.stderr)
                else:
                    ok_tokens.append(tok)
            img_tokens = ok_tokens                     # ảnh tải hỏng thì khỏi gửi, không bắt khách chờ
        if reply:
            await send_text(psid, reply)
        for tok in img_tokens:
            await send_image(psid, tok)
        # Thông báo admin SAU khi đã trả lời khách (không bắt khách chờ). Admin tự nhắn thì bỏ qua.
        if psid not in config.ADMIN_UIDS:
            if is_new:
                await notify_admins(f"👋 KHÁCH MỚI: {await _label(psid)}\nTin đầu: {text[:200]}\n"
                                    f"Bot trả lời: {reply[:200]}")
            phone = _find_phone(text)
            if phone:
                await notify_admins(f"📞 KHÁCH ĐỂ LẠI SĐT: {phone} - {await _label(psid)}\n"
                                    f"Tin khách: {text[:200]}")
