"""
Giao thức Messenger: verify webhook, verify chữ ký, bóc tin, gửi trả, rate-limit + đồng thời.
Port gọn từ Javis OS (server/messenger_bot.py) - bỏ phần dính settings, dùng thẳng config.
"""
import asyncio
import hashlib
import hmac
import json
import re
import sys
import time

import httpx

import brain
import config
import stats
from bot_tools import lark_crm, lark_image

_MAX_IMAGES_PER_MSG = 4   # khớp brain._MAX_NEW_IMAGES
_IMG_MAX_DIM = 1600       # cạnh dài tối đa gửi FB; ảnh gốc Lark hay 16MB PNG -> FB nghẹn
_IMG_JPEG_Q = 85


def _shrink_image(data: bytes, ctype: str) -> tuple[bytes, str]:
    """Downscale + nén JPEG để FB nuốt được (ảnh gốc Lark tới ~16MB PNG -> upload treo/timeout).
    Pillow lỗi hoặc ảnh đã nhỏ -> trả nguyên gốc (fallback an toàn)."""
    try:
        import io

        from PIL import Image
        im = Image.open(io.BytesIO(data))
        big = max(im.size) > _IMG_MAX_DIM
        if not big and len(data) < 1_000_000:
            return (data, ctype)                       # đã nhỏ, khỏi đụng
        if big:
            im.thumbnail((_IMG_MAX_DIM, _IMG_MAX_DIM))
        if im.mode in ("RGBA", "P", "LA"):
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=_IMG_JPEG_Q, optimize=True)
        return (buf.getvalue(), "image/jpeg")
    except Exception as e:
        print(f"[img] nén lỗi, gửi gốc: {type(e).__name__}: {e}", file=sys.stderr)
        return (data, ctype)

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
_IMG_EVENT = "\x00IMG"           # khách gửi ẢNH/FILE thật (không chữ)
_STICKER_EVENT = "\x00STK"       # khách thả like/sticker/icon (không phải ảnh thật)
_REFERRAL_EVENT = "\x00REF"      # khách bấm quảng cáo Click-to-Messenger / link m.me / Bắt đầu -> chào chủ động
_IMAGE_REPLY = ("Dạ mẫu này bên em có khá nhiều biến thể về kích thước và loại đá ạ. "
                "Để tư vấn chính xác nhất cho Bác, Bác cho em xin số điện thoại, "
                "chuyên gia bên em sẽ liên hệ tư vấn cụ thể cho Bác ngay ạ! 🌸")
# Like/sticker lần đầu: chào hỏi mời tư vấn như bình thường (KHÔNG xin SĐT dồn).
_STICKER_REPLY = ("Dạ em chào Bác ạ 🌸 Bác đang quan tâm mẫu nào để em tư vấn giúp Bác ạ? "
                  "(Mộ đá, Lăng thờ, Cổng hay Lan can đá...)")
# Khách vừa bấm quảng cáo/link mở chat (chưa gõ gì): chào chủ động mời tư vấn.
_REFERRAL_REPLY = ("Dạ em chào Bác ạ 🌸 Cảm ơn Bác đã quan tâm bên em. "
                   "Bác đang tìm mẫu nào để em tư vấn giúp Bác ạ? "
                   "(Mộ đá, Lăng thờ, Cổng hay Lan can đá...)")

# Đếm like/sticker liên tiếp mỗi khách -> lần 2 thì ngừng trả lời + báo admin. Reset khi có tin thật.
_STICKER_COUNT: dict[str, int] = {}
_STICKER_COUNT_MAX = 5000

# Ảnh khách gửi: stash URL theo psid, _process tải bytes -> Gemini vision đọc. (Ảnh tách khỏi
# pipeline text vì buffer chỉ mang string; sentinel _IMG_EVENT vẫn đi luồng gom/merge như cũ.)
_PENDING_IMAGES: dict[str, list[str]] = {}
_PENDING_IMAGES_MAX = 2000
_MAX_CUSTOMER_IMAGES = 4
# Khách gửi ảnh KHÔNG kèm chữ -> prompt để Gemini chủ động nhìn ảnh, DÒ bảng sản phẩm, gợi ý mẫu.
_IMG_ONLY_PROMPT = (
    "(Khách vừa gửi ảnh, chưa nhắn gì thêm. Hãy NHÌN KỸ ảnh và tư vấn:\n"
    "1. Nhận diện hạng mục (mộ đá, lăng thờ, cổng, lan can...) và đặc điểm (số mái, kiểu dáng, loại đá nếu thấy).\n"
    "2. DÒ trong BẢNG SẢN PHẨM tìm 1-3 mẫu GIỐNG/gần nhất với ảnh, nêu rõ mã + tên "
    "(nhắc mã -> hệ thống tự gửi ảnh mẫu kèm). Nếu không chắc mẫu chính xác, nói rõ đây là mẫu gần giống "
    "cùng dòng, đừng khẳng định chắc nịch.\n"
    "3. Nếu ảnh không phải sản phẩm đá (ảnh khác/không rõ), hỏi khách cần tư vấn gì.)")


def _stash_customer_images(psid: str, urls: list[str]) -> None:
    if len(_PENDING_IMAGES) > _PENDING_IMAGES_MAX:     # chặn phình (khách gửi ảnh rồi bỏ đi, không xử)
        _PENDING_IMAGES.clear()
    _PENDING_IMAGES.setdefault(psid, []).extend(urls)


async def _fetch_customer_images(urls: list[str]) -> list[tuple[bytes, str]]:
    """Tải ảnh khách từ URL CDN của FB (public), nén lại cho nhẹ token trước khi đưa Gemini."""
    out: list[tuple[bytes, str]] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as c:
        for u in urls[:_MAX_CUSTOMER_IMAGES]:
            try:
                r = await c.get(u)
                r.raise_for_status()
                ct = (r.headers.get("content-type", "image/jpeg") or "").split(";")[0].strip()
                if not ct.startswith("image/"):        # video/file: không đọc được bằng vision
                    continue
                out.append(_shrink_image(r.content, ct))
            except Exception as e:
                print(f"[cust-img] tải lỗi: {type(e).__name__}: {e}", file=sys.stderr)
    return out


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
            elif msg.get("attachments"):          # không chữ: tách sticker/like khỏi ảnh thật
                atts = msg["attachments"] or []
                is_sticker = any((a.get("payload") or {}).get("sticker_id") for a in atts)
                if not is_sticker:
                    urls = [(a.get("payload") or {}).get("url") for a in atts
                            if a.get("type") == "image" and (a.get("payload") or {}).get("url")]
                    if urls:
                        _stash_customer_images(str(psid), urls)   # _process tải + đưa Gemini đọc
                out.append((str(psid), _STICKER_EVENT if is_sticker else _IMG_EVENT))
            elif ev.get("referral") or ev.get("postback"):
                # Bấm quảng cáo Click-to-Messenger / link m.me có ref / nút Bắt đầu -> chào chủ động.
                out.append((str(psid), _REFERRAL_EVENT))
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


# Tách sau . ! ? … + khoảng trắng. (?=\D) tránh cắt giữa số (2.5 triệu, SĐT). Xuống dòng cũng tách.
_SENT_RE = re.compile(r"(?<=[.!?…])\s+(?=\D)")


def _split_sentences(text: str, per: int = 2) -> list[str]:
    """Reply -> nhiều tin ngắn ~per câu (chat tự nhiên). Gom per câu/tin, cắt theo độ dài nếu quá dài.

    ponytail: viết tắt hiếm ('vd.') có thể bị tách thừa - chấp nhận, reply bot không mấy khi có."""
    text = (text or "").strip()
    if not text:
        return []
    sents: list[str] = []
    for para in text.split("\n"):                     # tôn trọng xuống dòng persona đặt
        for s in _SENT_RE.split(para):
            s = s.strip()
            if s:
                sents.append(s)
    out: list[str] = []
    for i in range(0, len(sents), per):
        chunk = " ".join(sents[i:i + per])
        out.extend(_split_text(chunk) if len(chunk) > _TEXT_MAX else [chunk])
    return out or [text]


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


async def send_image_bytes(psid: str, data: bytes, ctype: str = "image/jpeg") -> None:
    """Gửi ảnh bằng CÁCH UPLOAD bytes thẳng (multipart). FB không phải tự fetch URL nữa
    -> ảnh tới ngay sau text, không trickle. Bytes đã warm sẵn nên gửi luôn."""
    if not (config.PAGE_TOKEN and psid and data):
        return
    url = _SEND_API.format(ver=config.GRAPH_VER)
    ext = "png" if "png" in (ctype or "") else "jpg"
    form = {
        "recipient": json.dumps({"id": psid}),
        "messaging_type": "RESPONSE",
        "message": json.dumps({"attachment": {"type": "image", "payload": {"is_reusable": True}}}),
    }
    files = {"filedata": (f"image.{ext}", data, ctype or "image/jpeg")}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as c:
            r = await c.post(url, params={"access_token": config.PAGE_TOKEN}, data=form, files=files)
            if r.status_code >= 400:
                print(f"[img] upload {r.status_code}: {r.text[:300]}", file=sys.stderr)
    except Exception as e:
        print(f"[img] upload {type(e).__name__}: {e}", file=sys.stderr)


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
    """Gom các tin thành 1. Ưu tiên: tin chữ > ảnh thật > sticker/like > referral (chào)."""
    _META = (_IMG_EVENT, _STICKER_EVENT, _REFERRAL_EVENT)
    real = [t for t in texts if t not in _META]
    if real:                                   # khách gõ thật -> bỏ chào referral, trả lời thẳng
        return "\n".join(real)
    if _IMG_EVENT in texts:                    # ảnh thật ưu tiên hơn sticker
        return _IMG_EVENT
    if _STICKER_EVENT in texts:
        return _STICKER_EVENT
    return _REFERRAL_EVENT if texts else ""


_FOLLOWUP_TEXT = ("Dạ Bác ơi 🌸 Không biết Bác còn đang phân vân mẫu nào không ạ? "
                  "Bác cứ nhắn em, em tư vấn thêm và gửi mẫu phù hợp cho Bác nhé!")


async def run_followups() -> None:
    """Quét khách im sau trả lời (chưa chốt) -> gửi 1 tin nhắc nhẹ. Chạy định kỳ từ app."""
    for psid, last_user_at in brain.followup_candidates(config.FOLLOWUP_AFTER_H):
        if psid in config.ADMIN_UIDS:
            continue
        await send_text(psid, _FOLLOWUP_TEXT)
        brain.mark_followed(psid, last_user_at)
        stats.log_event("followup", psid)


async def _save_lead_to_crm(psid: str) -> None:
    """Handoff: trích lead từ hội thoại -> ghi Lark CRM. Best-effort, lỗi chỉ báo admin."""
    lead = await brain.extract_lead(psid)
    if not lead:
        return
    name = await profile_name(psid)          # tên tài khoản FB - không xin tên khách nữa
    if name:
        lead["ten"] = name
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
    pending_urls = _PENDING_IMAGES.pop(psid, None)     # ảnh khách kèm lượt này (nếu có)
    images: list[tuple[bytes, str]] = []
    if pending_urls:
        images = await _fetch_customer_images(pending_urls)
    if text == _IMG_EVENT:
        _STICKER_COUNT.pop(psid, None)                 # ảnh thật = tương tác thật -> reset đếm
        stats.log_event("image", psid)
        if not images:
            # Tải ảnh hỏng hoặc attachment không phải ảnh (file/video) -> câu cố định cũ.
            await send_text(psid, _IMAGE_REPLY)
            return
        text = _IMG_ONLY_PROMPT                         # ảnh không kèm chữ -> Gemini tự nhìn ảnh tư vấn
    if text == _REFERRAL_EVENT:
        # Khách bấm quảng cáo/link mở chat, chưa gõ gì: chào chủ động 1 lần, không gọi AI.
        _STICKER_COUNT.pop(psid, None)
        stats.log_event("referral", psid)
        await send_text(psid, _REFERRAL_REPLY)
        return
    if text == _STICKER_EVENT:
        # Like/sticker/icon: lần đầu chào hỏi mời tư vấn; lần 2 liên tiếp -> im + báo admin.
        n = _STICKER_COUNT.get(psid, 0) + 1
        if len(_STICKER_COUNT) > _STICKER_COUNT_MAX:   # ponytail: chặn phình, xoá sạch (thô nhưng đủ)
            _STICKER_COUNT.clear()
        _STICKER_COUNT[psid] = n
        if n == 1:
            await send_text(psid, _STICKER_REPLY)
        elif n == 2:
            stats.log_event("sticker_stop", psid)
            await notify_admins(f"🔕 Ngừng trả lời {await _label(psid)}: gửi like/sticker/icon "
                                "lần 2 liên tiếp, không có nội dung thật. Cần người xem tay.")
        # n >= 3: im lặng, không báo admin lại (tránh spam)
        return
    async with _SEM:
        _STICKER_COUNT.pop(psid, None)                 # có tin chữ thật -> reset đếm sticker
        await send_action(psid, "typing_on")
        # to_thread: is_new_customer có thể chạm Firebase (cache miss) - offload để
        # Firebase chậm/chết không block event loop (mọi khách khác đứng).
        is_new = await asyncio.to_thread(brain.is_new_customer, psid)  # TRƯỚC khi brain ghi lịch sử
        t0 = time.monotonic()
        try:
            reply = await brain.answer(psid, text, images or None)
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
        imgs: list[tuple[bytes, str]] = []
        if img_tokens:
            # Tải HẾT ảnh về xong (đợi tất cả) rồi mới gửi. Giữ bytes để upload thẳng lên FB
            # -> ảnh tới ngay sau text, không để FB tự fetch URL rồi trickle lẻ tẻ.
            warmed = await asyncio.gather(
                *(asyncio.to_thread(lark_image.download_media, t) for t in img_tokens),
                return_exceptions=True)
            for tok, w in zip(img_tokens, warmed):
                if isinstance(w, Exception):
                    print(f"[img] tải lỗi token {tok[:12]}...: {type(w).__name__}: {w}", file=sys.stderr)
                else:
                    imgs.append(_shrink_image(*w))      # nén trước; (bytes, ctype) - ảnh hỏng thì bỏ
        if reply:
            for chunk in _split_sentences(reply):      # tách 1-2 câu/tin -> chat tự nhiên
                await send_text(psid, chunk)
        for data, ctype in imgs:
            await send_image_bytes(psid, data, ctype)
        # Thông báo admin SAU khi đã trả lời khách (không bắt khách chờ). Admin tự nhắn thì bỏ qua.
        if psid not in config.ADMIN_UIDS:
            if is_new:
                await notify_admins(f"👋 KHÁCH MỚI: {await _label(psid)}\nTin đầu: {text[:200]}\n"
                                    f"Bot trả lời: {reply[:200]}")
            phone = _find_phone(text)
            if phone:
                await notify_admins(f"📞 KHÁCH ĐỂ LẠI SĐT: {phone} - {await _label(psid)}\n"
                                    f"Tin khách: {text[:200]}")
