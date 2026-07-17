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
from bot_tools import lark_crm, lark_image

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
_NAME_CACHE_MAX = 5000            # trần chống phình vô hạn; vượt -> bỏ 1000 entry cũ nhất (FIFO)


def _cache_name(pid: str, name: str) -> None:
    if len(_NAME_CACHE) >= _NAME_CACHE_MAX and pid not in _NAME_CACHE:
        for k in list(_NAME_CACHE)[:1000]:            # dict giữ thứ tự chèn -> đầu = cũ nhất
            _NAME_CACHE.pop(k, None)
    _NAME_CACHE[pid] = name


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
                            _cache_name(pid, name)
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
    if psid not in _NAME_CACHE:
        _cache_name(psid, "")                 # vẫn không có -> cache rỗng, khỏi gọi lặp
    return _NAME_CACHE[psid]


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


# Tin public + private khi khách comment (giới thiệu + hỏi nhu cầu, mời vào inbox).
_PUBLIC_REPLY = "Dạ em cảm ơn Bác đã quan tâm tới Hồn Đá ạ! 🌸"
_PRIVATE_REPLY = ("Dạ em chào Bác ạ 🌸 Em là Thảo Vân, trợ lý bên Đá mỹ nghệ Hồn Đá. "
                  "Em thấy Bác quan tâm tới sản phẩm bên em. Bác đang tìm hiểu mẫu nào ạ - "
                  "Mộ đá, Long đình, Cổng hay Lan can đá? Em tư vấn chi tiết cho Bác nhé!")

_SEEN_COMMENTS: dict[str, float] = {}     # comment_id -> ts, chống xử lý trùng khi FB gửi lại
_SEEN_MAX = 5000


def _comment_seen(cid: str) -> bool:
    """True nếu comment_id đã xử lý. Dọn entry cũ > 6h khi dict phình."""
    now = time.monotonic()
    if len(_SEEN_COMMENTS) > _SEEN_MAX:
        cutoff = now - 6 * 3600
        for k in [k for k, v in _SEEN_COMMENTS.items() if v < cutoff]:
            _SEEN_COMMENTS.pop(k, None)
    if cid in _SEEN_COMMENTS:
        return True
    _SEEN_COMMENTS[cid] = now
    return False


def parse_comment_events(payload: dict):
    """[(comment_id, from_id)] từ webhook feed. Bỏ comment của chính Page, verb != add, đã xử lý."""
    out = []
    if not isinstance(payload, dict) or payload.get("object") != "page":
        return out
    for entry in payload.get("entry", []) or []:
        page_id = str(entry.get("id", ""))
        for ch in (entry.get("changes") or []):
            v = ch.get("value") or {}
            if v.get("item") != "comment" or v.get("verb") != "add":
                continue
            cid = str(v.get("comment_id") or "")
            from_id = str((v.get("from") or {}).get("id") or "")
            if not cid or not from_id or from_id == page_id:   # bỏ comment của Page (chống loop)
                continue
            if _comment_seen(cid):
                continue
            out.append((cid, from_id))
    return out


async def reply_public(comment_id: str) -> None:
    """Trả lời công khai dưới comment: cảm ơn."""
    if not (config.PAGE_TOKEN and comment_id):
        return
    url = f"https://graph.facebook.com/{config.GRAPH_VER}/{comment_id}/comments"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as c:
            r = await c.post(url, params={"access_token": config.PAGE_TOKEN},
                             json={"message": _PUBLIC_REPLY})
            if r.status_code >= 400:
                print(f"[comment-public] {r.status_code}: {r.text[:300]}", file=sys.stderr)
    except Exception as e:
        print(f"[comment-public] {type(e).__name__}: {e}", file=sys.stderr)


async def reply_private(comment_id: str) -> None:
    """Nhắn RIÊNG vào inbox người comment (private reply - FB chỉ cho 1 lần/comment)."""
    if not (config.PAGE_TOKEN and comment_id):
        return
    url = _SEND_API.format(ver=config.GRAPH_VER)
    body = {"recipient": {"comment_id": comment_id}, "message": {"text": _PRIVATE_REPLY}}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as c:
            r = await c.post(url, params={"access_token": config.PAGE_TOKEN}, json=body)
            if r.status_code >= 400:
                print(f"[comment-private] {r.status_code}: {r.text[:300]}", file=sys.stderr)
    except Exception as e:
        print(f"[comment-private] {type(e).__name__}: {e}", file=sys.stderr)


async def handle_comment(comment_id: str, from_id: str) -> None:
    """1 comment: cảm ơn công khai + nhắn riêng mời vào inbox. Lỗi 1 kênh không chặn kênh kia."""
    await reply_public(comment_id)
    await reply_private(comment_id)
    stats.log_event("comment", from_id)


# Khách gửi ẢNH/file (không kèm chữ) -> trả câu cố định, không gọi AI, không đọc ảnh.
_IMG_EVENT = "\x00IMG"
_IMAGE_REPLY = ("Dạ mẫu này bên em có khá nhiều biến thể về kích thước và loại đá ạ. "
                "Để tư vấn chính xác nhất cho Bác, Bác cho em xin tên và số điện thoại, "
                "chuyên gia bên em sẽ liên hệ tư vấn cụ thể cho Bác ngay ạ! 🌸")


def parse_events(payload: dict):
    """[(psid, text)] từ webhook body. Bỏ echo của page.

    Tin CHỈ có ảnh/file (không chữ) -> text = _IMG_EVENT (handle_event trả câu cố định)."""
    out = []
    if not isinstance(payload, dict) or payload.get("object") != "page":
        return out
    for entry in payload.get("entry", []) or []:
        for ev in (entry.get("messaging") or []):
            msg = ev.get("message") or {}
            if msg.get("is_echo"):
                continue
            psid = (ev.get("sender") or {}).get("id")
            if not psid:
                continue
            text = msg.get("text")
            if text:
                out.append((str(psid), str(text)))
            elif msg.get("attachments"):          # ảnh/file/sticker, không kèm chữ
                out.append((str(psid), _IMG_EVENT))
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

# Gom tin (debounce) mỗi khách: đợi khách gõ xong rồi trả 1 lần thay vì rep rời rạc/bỏ tin.
_BUFFERS: dict[str, dict] = {}     # psid -> {"texts": [...], "task": Task, "first": ts}
_DEBOUNCE_S = 4.0                  # im bao lâu thì chốt gom
_MAX_WAIT_S = 20.0                 # trần chờ từ tin đầu (khách gõ liên tục không ngừng vẫn phải trả)
_MAX_BUFFER = 15                   # trần số tin gom 1 lượt


def _merge_texts(texts: list[str]) -> str:
    """Gom các tin thành 1. Bỏ sentinel ảnh nếu có tin chữ; chỉ toàn ảnh -> giữ sentinel."""
    real = [t for t in texts if t != _IMG_EVENT]
    if real:
        return "\n".join(real)
    return _IMG_EVENT if texts else ""


async def _save_lead_to_crm(psid: str) -> None:
    """Handoff: trích lead từ hội thoại -> ghi Lark CRM. Best-effort, lỗi chỉ báo admin."""
    lead = await brain.extract_lead(psid)
    if not lead:
        return
    result = await asyncio.to_thread(lark_crm.upsert_lead, psid, lead)
    tag = {"created": "✅ Đã tạo lead CRM", "updated": "♻️ Đã cập nhật lead CRM"}.get(result)
    if tag:
        code = lark_crm.lead_code(psid)               # mã Lead/Chance vừa lưu
        head = f"{tag}: {lead.get('ten') or '?'} - {lead.get('sdt') or '?'}"
        if code:
            head += f" [{code}]"
        await notify_admins(f"{head}\n{lead.get('tinh') or ''} | {lead.get('tom_tat') or ''}")
    elif result == "error":
        await notify_admins(f"⚠️ Ghi lead CRM LỖI cho khách {psid} (xem log server)")


async def handle_event(psid: str, text: str) -> None:
    """Nhận 1 tin: gom vào buffer khách + hẹn giờ chốt. Nhiều tin dồn -> gom, trả 1 lần."""
    buf = _BUFFERS.setdefault(psid, {"texts": [], "task": None, "first": time.monotonic()})
    buf["texts"].append(text)
    if buf["task"] and not buf["task"].done():
        buf["task"].cancel()                        # có tin mới -> dời giờ chốt
    buf["task"] = asyncio.create_task(_debounced_flush(psid))


async def _debounced_flush(psid: str) -> None:
    """Đợi khách im _DEBOUNCE_S (trần _MAX_WAIT_S / _MAX_BUFFER tin) rồi gom xử 1 lượt."""
    buf = _BUFFERS.get(psid)
    if buf is None:
        return
    # gõ liên tục -> vẫn chốt khi chạm trần chờ hoặc trần số tin
    over = (time.monotonic() - buf["first"] >= _MAX_WAIT_S) or (len(buf["texts"]) >= _MAX_BUFFER)
    try:
        await asyncio.sleep(0 if over else _DEBOUNCE_S)
    except asyncio.CancelledError:
        return
    buf = _BUFFERS.pop(psid, None)
    if not buf or not buf["texts"]:
        return
    await _process(psid, _merge_texts(buf["texts"]))


async def _process(psid: str, text: str) -> None:
    """Xử 1 lượt đã gom: semaphore → brain → gửi lại. Lỗi = báo admin, không rep khách."""
    if text == _IMG_EVENT:
        # Khách gửi ảnh/file không chữ: câu cố định xin tên+SĐT, không gọi AI, không đọc ảnh.
        stats.log_event("image", psid)
        await send_text(psid, _IMAGE_REPLY)
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
            await _save_lead_to_crm(psid)
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
