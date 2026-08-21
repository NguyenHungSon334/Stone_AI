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
import unicodedata
from datetime import datetime, timezone

import httpx

import alerts
import brain
import config
import returning
import state
import stats
import util
from bot_tools import lark_crm, lark_image

_MAX_IMAGES_PER_MSG = 4   # khớp brain._MAX_NEW_IMAGES
_IMG_MAX_DIM = 1600       # cạnh dài tối đa gửi FB; ảnh gốc Lark hay 16MB PNG -> FB nghẹn
_IMG_JPEG_Q = 85
_IMG_RETRY_GAP_S = 1.0    # chờ trước khi gửi lại ảnh hỏng (dội lại ngay là hỏng tiếp)


_IMG_CANH_BAO_MB = 2.0    # trên ngưỡng này FB hay trả '#100 Upload attachment failure'


def _bat_heic() -> None:
    """Dạy Pillow đọc .HEIC (ảnh chụp iPhone dán thẳng vào Lark Base).

    Thiếu gói này thì Pillow ném UnidentifiedImageError -> ảnh đi nguyên bản và FB từ chối.
    Best-effort: không cài được thì bot vẫn chạy, chỉ mất ảnh HEIC (đã có cảnh báo riêng)."""
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except Exception as e:
        print(f"[img] không bật được HEIC: {type(e).__name__}: {e}", file=sys.stderr)


_bat_heic()


def _shrink_image(data: bytes, ctype: str) -> tuple[bytes, str]:
    """Downscale + nén JPEG để FB nuốt được (ảnh gốc Lark tới ~20MB PNG -> upload treo/timeout).
    Pillow lỗi hoặc ảnh đã nhỏ -> trả nguyên gốc (fallback an toàn)."""
    goc_mb = len(data) / 1e6
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
        out = buf.getvalue()
        print(f"[img] nén {goc_mb:.1f}MB {ctype} -> {len(out) / 1e6:.1f}MB jpeg", file=sys.stderr)
        if len(out) > _IMG_CANH_BAO_MB * 1e6:
            # Nén rồi vẫn nặng = FB nhiều khả năng từ chối. Báo để biết ĐÂY mới là nguyên nhân,
            # thay vì đoán mò quanh token/quyền mỗi lần thấy #100.
            alerts.alert("img:qua-nang",
                         f"⚠️ ẢNH VẪN NẶNG SAU KHI NÉN ({len(out) / 1e6:.1f}MB) - FB dễ từ chối "
                         f"'#100 Upload attachment failure', khách nhận chữ mà thiếu ảnh.\n"
                         f"➡️ Giảm _IMG_MAX_DIM/_IMG_JPEG_Q, hoặc thay ảnh gốc nhẹ hơn trong Lark Base.")
        return (out, "image/jpeg")
    except Exception as e:
        print(f"[img] nén lỗi, gửi gốc {goc_mb:.1f}MB: {type(e).__name__}: {e}", file=sys.stderr)
        # Hai nguyên nhân KHÁC HẲN nhau, gợi ý sai là admin đi soi nhầm chỗ (đã dính: báo
        # "kiểm tra Pillow" trong khi Pillow vẫn tốt, thủ phạm là file .HEIC/.MOV trong Base).
        if type(e).__name__ == "UnidentifiedImageError":
            goi_y = ("➡️ Tệp trong cột Ảnh KHÔNG phải ảnh đọc được (hay gặp: .HEIC của iPhone, "
                     "hoặc video .MOV dán nhầm). Mở record trong Lark Base, thay bằng JPG/PNG.")
        else:
            goi_y = "➡️ Kiểm tra Pillow trên server: python -c \"import PIL\""
        alerts.alert(f"img:nen:{type(e).__name__}",
                     f"🔴 KHÔNG NÉN ĐƯỢC ẢNH ({goc_mb:.1f}MB, gửi nguyên bản) - FB nhiều khả năng "
                     f"từ chối, khách KHÔNG nhận được ảnh.\n{type(e).__name__}: {e}\n{goi_y}")
        return (data, ctype)

_SEND_API = "https://graph.facebook.com/{ver}/me/messages"
_TEXT_MAX = 2000
# Marker persona chèn khi chuyển chuyên gia (khách KHÔNG thấy). Kèm lý do: <<HANDOFF:lý do ngắn>>.
_HANDOFF_RE = re.compile(r"<<HANDOFF(?::([^>]*))?>>")
_IMG_RE = re.compile(r"<<IMG:([^>]+)>>")   # marker ảnh: file_token Lark, bóc ra gửi ảnh riêng
# Marker persona chèn khi khách KHÓ CHỊU: gửi nốt tin xoa dịu rồi bot im, người thật vào.
_PAUSE_RE = re.compile(r"<<TAMDUNG(?::([^>]*))?>>")
_PAUSE_H = 24.0        # im bấy nhiêu giờ; đủ để chuyên gia gọi, không khoá khách vĩnh viễn
# SĐT khách: dùng chung util.tim_sdt (đã chuẩn hoá 0xxxxxxxxx, khớp hồ sơ + CRM).

# Handoff CƯỠNG BỨC ở code (chặn trước AI, tin cậy 100%): khách gõ tín hiệu tường minh.
_HUMAN_KEYWORDS = ("gặp nhân viên", "gặp người", "người thật", "tư vấn viên", "tổng đài",
                   "talk to human", "gặp admin", "chuyển máy", "gặp chuyên gia", "gặp sale",
                   "nói chuyện với người", "cho gặp người", "gặp ai đó", "gặp quản lý")
# Từ ngữ khiếu nại/pháp lý/tiêu cực mạnh -> người thật vào xoa dịu.
_COMPLAINT_KEYWORDS = ("lừa đảo", "cắt cổ", "chặt chém", "báo công an", "khiếu nại", "hoàn tiền",
                       "trả lại tiền", "đòi tiền", "kiện", "bóc phốt", "phốt", "quá tệ", "tệ hại",
                       "thất vọng", "bức xúc", "vô trách nhiệm")
# KHÔNG có người trực fanpage: chuyên gia GỌI ĐIỆN chứ không vào chat. Câu cũ hứa "kết nối
# hỗ trợ trực tiếp" -> khách ngồi đợi người vào chat, không ai vào, khách bỏ.
_HUMAN_HANDOFF_REPLY = ("Dạ nội dung này em nhờ chuyên gia bên em trao đổi với Bác cho chính xác ạ. "
                        "Bác cho em xin SĐT hoặc Zalo, chuyên gia sẽ gọi cho Bác ngay nhé!")


def _cau_chuyen_nguoi_that(sdt: str) -> str:
    """Câu chuyển người thật. Hồ sơ đã có SĐT -> XÁC NHẬN số, không xin lại.

    Câu cố định không đi qua model nên không đọc hồ sơ: khách vừa cho số ở tin trước rồi gõ
    'cho gặp nhân viên' là bị hỏi lại số ngay."""
    if sdt:
        return ("Dạ nội dung này em nhờ chuyên gia bên em trao đổi với Bác cho chính xác ạ. "
                f"Chuyên gia sẽ gọi cho Bác theo số {sdt} ngay nhé!")
    return _HUMAN_HANDOFF_REPLY
# Khách đang gắt: KHÔNG xin số, KHÔNG hỏi thêm, KHÔNG chào mời - chỉ nhận lỗi rồi nhường người thật.
_XOA_DIU_REPLY = ("Dạ em xin ghi nhận phản ánh của Bác ạ. Em rất tiếc vì đã làm Bác không hài lòng. "
                  "Em báo ngay quản lý bên em liên hệ trực tiếp với Bác để xử lý ạ.")
# Khách đòi ngừng: KHÔNG hứa "quản lý sẽ liên hệ" - đang bảo đừng làm phiền mà còn hẹn gọi
# tiếp là phản tác dụng. Nhận lỗi, dừng, để cửa mở cho khách chủ động.
_NGUNG_REPLY = ("Dạ em xin lỗi vì đã làm phiền Bác ạ. Em dừng nhắn tin cho Bác từ đây. "
                "Khi nào Bác cần đến đá mỹ nghệ thì nhắn em bất cứ lúc nào ạ.")


_LY_DO_KHIEU_NAI = "Khách bức xúc/khiếu nại (từ ngữ tiêu cực mạnh)"
_LY_DO_VIET_HOA = "Khách viết HOA toàn bộ câu (dấu hiệu bức xúc)"
_LY_DO_DOI_NGUNG = "Khách yêu cầu ngừng nhắn"
# Ba lý do này là khách ĐANG KHÓ CHỊU (khác với chỉ xin gặp người thật) -> bot phải im luôn.
_LY_DO_KHO_CHIU = (_LY_DO_KHIEU_NAI, _LY_DO_VIET_HOA, _LY_DO_DOI_NGUNG)

# Khách đòi NGỪNG NHẮN. Bắt bằng code, KHÔNG chờ AI chấm ý định: đây là câu mà đoán sai một
# lần là khách chặn Fanpage. Ghép cụm đủ dài để không dính câu bình thường ("phiền Bác cho em
# xin kích thước" không được khớp "làm phiền").
_NGUNG_KEYWORDS = ("đừng nhắn", "dừng nhắn", "ngừng nhắn", "không nhắn nữa", "đừng làm phiền",
                   "làm phiền quá", "phiền quá", "nhắn hoài", "nhắn mãi", "hỏi hoài", "hỏi mãi",
                   "spam", "đừng gửi nữa", "block", "chặn trang", "bỏ theo dõi")
# Khách TỪ CHỐI (không phải gắt): vẫn trả lời tử tế, chỉ CẤM nhắc lại về sau.
_TU_CHOI_KEYWORDS = ("không có nhu cầu", "ko có nhu cầu", "k có nhu cầu", "không nhu cầu",
                     "nhắn nhầm", "nhắn lộn", "không mua", "ko mua", "không cần nữa",
                     "không quan tâm", "ko quan tâm")


def _y_dinh_tu_khoa(text: str) -> str:
    """Ý định đọc bằng LUẬT CỨNG từ chính lời khách: 'tu choi' | '' (để AI chấm tiếp).

    Có lớp này vì y_dinh do AI chấm có thể lỗi/chấm nhầm -> khách đã nói thẳng 'không có nhu
    cầu' mà vẫn bị follow-up nhắc. Luật cứng không phụ thuộc AI."""
    t = (text or "").lower()
    return "tu choi" if any(k in t for k in _TU_CHOI_KEYWORDS) else ""


def _forced_handoff_reason(text: str) -> str | None:
    """Tín hiệu tường minh cần người thật NGAY (chặn trước AI). None = để AI xử bình thường."""
    t = (text or "").lower()
    if any(k in t for k in _NGUNG_KEYWORDS):      # khách đòi ngừng: xử TRƯỚC mọi nhánh khác
        return _LY_DO_DOI_NGUNG
    if any(k in t for k in _HUMAN_KEYWORDS):
        return "Khách chủ động xin gặp người thật"
    if any(k in t for k in _COMPLAINT_KEYWORDS):
        return _LY_DO_KHIEU_NAI
    raw = (text or "").strip()
    letters = [c for c in raw if c.isalpha()]
    if len(letters) >= 12 and raw == raw.upper() and " " in raw:   # viết HOA cả câu -> đang gắt
        return _LY_DO_VIET_HOA
    return None


_NOISE_TERMS = {
    "gia", "mau", "mo", "lang", "da", "tu", "van", "bao", "xem", "gui", "can", "muon",
    "lam", "xay", "ngoi", "met", "cong", "hang", "rao", "long", "dinh", "zalo",
}
_SHORT_VALID_REPLIES = {"co", "khong", "ok", "uh", "um", "roi", "duoc", "dongy", "vang"}
_NOISE_REPLY = "Dạ Bác vui lòng nói rõ mình cần xem mẫu, hỏi giá hay làm hạng mục nào để em hỗ trợ đúng ạ."


def _plain_text(text: str) -> str:
    folded = unicodedata.normalize("NFD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


def _is_meaningful_text(text: str) -> bool:
    plain = _plain_text(text)
    if not plain:
        return False
    if util.tim_sdt(text) or any(char.isdigit() for char in plain):
        return True
    compact = plain.replace(" ", "")
    if compact in _SHORT_VALID_REPLIES or len(compact) >= 6:
        return True
    return bool(set(plain.split()) & _NOISE_TERMS)


def _noise_state(psid: str) -> dict:
    """Cờ nhiễu/khoá của khách. Nay nằm trong state (Firebase + cache local), không còn file rời."""
    return state.get(psid)


def _reset_noise(psid: str) -> None:
    """Khách nhắn tin có nghĩa -> xoá bộ đếm nhiễu. KHÔNG đụng cờ khoá nhắn (no_contact):
    khách gắt xong nhắn tiếp câu tử tế cũng không được tự mở khoá."""
    st = state.get(psid)
    if st.get("count") or st.get("recent") or (st.get("stopped") and not st.get("no_contact")):
        state.patch(psid, count=0, recent=[], stopped=bool(st.get("no_contact")))


def _noise_decision(psid: str, text: str) -> tuple[str, dict]:
    st = state.get(psid)
    # Khoá vì khách khó chịu KHÔNG được tin có nghĩa mở lại: khách gắt tiếp vẫn là tin
    # có nghĩa, mở ra thì bot lại nhảy vào đúng lúc không nên. Chỉ hết hạn giờ mới mở.
    khoa, _ = state.bi_khoa(psid)
    if khoa:
        return "blocked", st
    if _is_meaningful_text(text):
        _reset_noise(psid)
        return "allow", st
    recent = list(st.get("recent") or [])[-2:]
    recent.append((text or "").strip()[:80])
    if st.get("stopped"):
        return "blocked", state.patch(psid, recent=recent)
    count = int(st.get("count") or 0) + 1
    if count == 1:
        return "clarify", state.patch(psid, recent=recent, count=count)
    return "stop", state.patch(psid, recent=recent, count=count, stopped=True)


def _sticker_decision(psid: str) -> tuple[str, dict]:
    st = state.get(psid)
    if st.get("stopped"):
        return "blocked", st
    recent = list(st.get("recent") or [])[-2:]
    recent.append("[sticker]")
    count = int(st.get("count") or 0) + 1
    if count == 1:
        return "welcome", state.patch(psid, recent=recent, count=count)
    return "stop", state.patch(psid, recent=recent, count=count, stopped=True, reason="sticker")

def _extract_handoff(reply: str) -> tuple[str, str | None]:
    """Bóc marker handoff khỏi tin gửi khách. Trả (reply_sạch, lý_do | None)."""
    m = _HANDOFF_RE.search(reply)
    if not m:
        return (reply, None)
    reason = (m.group(1) or "").strip() or "Khách cần chuyên gia (persona không ghi rõ lý do)"
    return (_HANDOFF_RE.sub("", reply).strip(), reason)


def _extract_pause(reply: str) -> tuple[str, str | None]:
    """Bóc marker tạm dừng khỏi tin gửi khách. Trả (reply_sạch, lý_do | None)."""
    m = _PAUSE_RE.search(reply)
    if not m:
        return (reply, None)
    reason = (m.group(1) or "").strip() or "Khách khó chịu (persona không ghi rõ lý do)"
    return (_PAUSE_RE.sub("", reply).strip(), reason)


def _pause_bot(psid: str, reason: str) -> None:
    """Khách khó chịu -> ĐÁNH DẤU KHOÁ NHẮN vào database (Firebase), bot im _PAUSE_H giờ.

    Cờ nằm ở khach_state/<psid> nên sống sót redeploy, và mọi đường gửi ra đều check nó
    (xem _bo_qua_vi_khoa) - không phải nhớ vá từng luồng nhắn chủ động."""
    state.khoa_nhan(psid, ly_do=reason, nguon="TAMDUNG", gio=_PAUSE_H)


def _extract_images(reply: str) -> tuple[str, list[str]]:
    """Bóc marker <<IMG:token>> khỏi text. Trả (text_sạch, [file_token])."""
    tokens = _IMG_RE.findall(reply)
    clean = _IMG_RE.sub("", reply).strip()
    return (clean, tokens)


# Lưới chặn CUỐI: mọi marker nội bộ đều dạng <<...>>, model viết lệch một chút là các hàm bóc
# ở trên trượt và KHÁCH ĐỌC ĐƯỢC marker (đã xảy ra với <<ANH>>: model tự đóng thẻ thành
# "<<ANH></anh>>"). Chặn ở đây thì mọi biến thể lệch của MỌI marker đều không lọt ra ngoài.
# Giới hạn 60 ký tự và cấm '<' '>' bên trong để không nuốt nhầm cả câu.
_MARKER_THUA_RE = re.compile(r"<+\s*/?\s*[A-Za-z][^<>]{0,60}>+")


def _bo_marker_thua(reply: str, psid: str = "") -> str:
    """Xoá marker nội bộ còn sót sau khi đã bóc handoff + ảnh. Có sót là lỗi prompt -> báo admin."""
    sot = _MARKER_THUA_RE.findall(reply or "")
    if not sot:
        return reply
    print(f"[marker] sót {sot} ở tin gửi {psid} - đã xoá trước khi gửi", file=sys.stderr)
    alerts.alert("brain:marker-sot",
                 "⚠️ BOT VIẾT MARKER SAI ĐỊNH DẠNG - đã chặn kịp, khách không thấy.\n"
                 f"Sót: {sot[:3]}\n➡️ Xem lại hướng dẫn marker trong Personal.md.")
    return _MARKER_THUA_RE.sub("", reply).strip()


async def lark_ping(text: str = "✅ Test bot admin Lark từ dashboard - kết nối OK.") -> dict:
    """Kiểm tra kết nối bot admin Lark (nút Test ở dashboard). Trả trạng thái cấu hình + kết quả gửi."""
    if not config.LARK_WEBHOOK_URL:
        return {"configured": False, "ok": False, "detail": "Chưa cấu hình LARK_WEBHOOK_URL trong .env"}
    ok, detail = await asyncio.to_thread(alerts.post_lark, text)
    return {"configured": True, "ok": ok, "detail": detail}


async def notify_admins(text: str) -> None:
    """Thông báo nghiệp vụ tới admin qua Lark group (khách mới, sđt, handoff) - KHÔNG gộp."""
    await asyncio.to_thread(alerts.notify, text)


async def alert_admins(key: str, text: str) -> None:
    """Báo LỖI có gộp (xem alerts.alert). `key` = loại sự cố, KHÔNG kèm psid."""
    await asyncio.to_thread(alerts.alert, key, text)


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


_NAMES_LOADED_AT = 0.0            # lần crawl /me/conversations gần nhất
_NAMES_COOLDOWN_S = 300.0         # crawl lại sớm nhất sau ngần này giây
_NAMES_LOCK = asyncio.Lock()


async def profile_name(psid: str) -> str:
    """Tên đầy đủ của khách. Nguồn: /me/conversations (cache); miss thì nạp lại 1 lần.

    Crawl có COOLDOWN + khoá. /me/conversations chỉ trả 500 hội thoại gần nhất, nên khách cũ
    KHÔNG BAO GIỜ có tên -> mỗi lần hỏi lại kích một crawl 5 trang mới. Trang admin hỏi 50 psid
    một lượt là 50 crawl (tới 250 request, mỗi cái timeout 15s) -> treo cả trang. Cooldown ép
    tối đa 1 crawl / 5 phút; trong lúc chờ, tên chưa biết trả rỗng."""
    global _NAMES_LOADED_AT
    if not config.PAGE_TOKEN:
        return ""
    if psid not in _NAME_CACHE and time.time() - _NAMES_LOADED_AT >= _NAMES_COOLDOWN_S:
        async with _NAMES_LOCK:
            # Kiểm lại trong khoá: 50 lời gọi cùng lúc thì chỉ đứa đầu crawl, phần còn lại
            # dùng cache vừa nạp.
            if time.time() - _NAMES_LOADED_AT >= _NAMES_COOLDOWN_S:
                _NAMES_LOADED_AT = time.time()
                await _names_from_conversations()
    return _NAME_CACHE.get(psid, "")


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
_PUBLIC_REPLY = "Dạ em cảm ơn Bác đã quan tâm tới Hồn Đá ạ!"
_PRIVATE_REPLY = ("Dạ em chào Bác ạ. Em là Thảo Vân, trợ lý bên Đá mỹ nghệ Hồn Đá. "
                  "Em thấy Bác quan tâm tới sản phẩm bên em. Bác đang tìm hiểu mẫu nào ạ - "
                  "Mộ đá, Long đình, Cổng hay Lan can đá? Em tư vấn chi tiết cho Bác nhé!")

# comment_id -> epoch, chống xử lý trùng khi FB gửi lại.
# GHI XUỐNG ĐĨA (không chỉ RAM): FB gửi lại event sau khi bot restart/deploy thì dedupe trong
# RAM đã trắng -> bot trả lời CÔNG KHAI lần 2 dưới cùng comment, khách nhìn thấy. Private reply
# được FB tự chặn (#10900) nhưng public thì không ai chặn.
_SEEN_COMMENTS: dict[str, float] = {}
_SEEN_MAX = 5000
_SEEN_KEEP_S = 7 * 24 * 3600              # FB gửi lại trong vài giờ; 7 ngày là dư an toàn
# Đuôi .state (KHÔNG phải .json) là cố ý: conversations/ bị nhiều chỗ quét bằng glob("*.json")
# (danh sách khách, follow-up). File .json ở đây sẽ bị đếm thành 1 "khách" ma.
_SEEN_PATH = brain._HIST_DIR / "_comments_seen.state"
_seen_loaded = False


def _load_seen() -> None:
    """Nạp 1 lần từ đĩa lúc dùng đầu tiên. File hỏng/thiếu -> coi như rỗng (không chặn bot)."""
    global _seen_loaded
    if _seen_loaded:
        return
    _seen_loaded = True
    data = util.read_json(_SEEN_PATH, {})
    if isinstance(data, dict):
        cutoff = time.time() - _SEEN_KEEP_S
        _SEEN_COMMENTS.update({k: v for k, v in data.items()
                               if isinstance(v, (int, float)) and v > cutoff})


def _comment_seen(cid: str) -> bool:
    """True nếu comment_id đã xử lý (kể cả ở lần chạy TRƯỚC). Ghi đĩa để sống qua restart."""
    _load_seen()
    now = time.time()
    if cid in _SEEN_COMMENTS:
        return True
    if len(_SEEN_COMMENTS) > _SEEN_MAX:
        cutoff = now - _SEEN_KEEP_S
        for k in [k for k, v in _SEEN_COMMENTS.items() if v < cutoff]:
            _SEEN_COMMENTS.pop(k, None)
    _SEEN_COMMENTS[cid] = now
    try:
        util.write_json_atomic(_SEEN_PATH, _SEEN_COMMENTS)
    except Exception as e:
        # Ghi hỏng -> dedupe tụt về mức RAM: vẫn chạy, nhưng restart là comment trùng lại.
        print(f"[comment] ghi dedupe lỗi: {type(e).__name__}: {e}", file=sys.stderr)
        alerts.alert(f"comment:seen:{type(e).__name__}",
                     f"⚠️ KHÔNG LƯU ĐƯỢC DANH SÁCH COMMENT ĐÃ XỬ LÝ - restart có thể trả lời "
                     f"TRÙNG công khai dưới comment khách.\n{type(e).__name__}: {e}")
    return False


def parse_comment_events(payload: dict):
    """[(comment_id, from_id, text)] từ webhook feed. Bỏ comment của chính Page, verb != add, đã xử lý.

    Log MỌI lần bỏ kèm lý do: không có log thì 'FB không gửi event' và 'bot tự bỏ event'
    nhìn giống hệt nhau (đều im lặng), debug thành đoán mò."""
    out = []
    if not isinstance(payload, dict) or payload.get("object") != "page":
        return out
    for entry in payload.get("entry", []) or []:
        page_id = str(entry.get("id", ""))
        for ch in (entry.get("changes") or []):
            if ch.get("field") != "feed":
                continue
            v = ch.get("value") or {}
            item, verb = v.get("item"), v.get("verb")
            if item != "comment" or verb != "add":
                print(f"[comment] bỏ qua: item={item} verb={verb} (chỉ xử lý comment/add)", file=sys.stderr)
                continue
            cid = str(v.get("comment_id") or "")
            from_id = str((v.get("from") or {}).get("id") or "")
            if not cid or not from_id:
                print(f"[comment] bỏ qua: thiếu comment_id/from (cid={cid!r} from={from_id!r})", file=sys.stderr)
                continue
            if from_id == page_id:                      # comment của chính Page -> chống loop vô hạn
                print(f"[comment] bỏ qua {cid}: comment của chính Page", file=sys.stderr)
                continue
            if _comment_seen(cid):
                print(f"[comment] bỏ qua {cid}: FB gửi trùng, đã xử lý rồi", file=sys.stderr)
                continue
            text = str(v.get("message") or "")
            print(f"[comment] NHẬN {cid} từ {from_id}: {text[:80]!r}", file=sys.stderr)
            out.append((cid, from_id, text))
    return out


async def reply_public(comment_id: str) -> bool:
    """Trả lời công khai dưới comment: cảm ơn. True = FB nhận."""
    if not (config.PAGE_TOKEN and comment_id):
        return False
    url = f"https://graph.facebook.com/{config.GRAPH_VER}/{comment_id}/comments"
    return await _fb_post(url, payload={"message": _PUBLIC_REPLY}, tag="comment-public")


async def reply_private(comment_id: str) -> bool:
    """Nhắn RIÊNG vào inbox người comment (private reply - FB chỉ cho 1 lần/comment). True = FB nhận."""
    if not (config.PAGE_TOKEN and comment_id):
        return False
    return await _fb_post(_SEND_API.format(ver=config.GRAPH_VER),
                          payload={"recipient": {"comment_id": comment_id}, "message": {"text": _PRIVATE_REPLY}},
                          tag="comment-private")


async def handle_comment(comment_id: str, from_id: str, text: str = "") -> None:
    """1 comment: LUÔN cảm ơn công khai; chỉ nhắn riêng khi comment có dấu hiệu nhu cầu.

    Khen đẹp/chào hỏi mà nhắn riêng là làm phiền, và đốt mất quyền private reply (FB chỉ cho
    1 lần/comment). Lỗi 1 kênh không chặn kênh kia; 2 kênh hỏng theo cách KHÁC nhau (public cần
    pages_manage_engagement; private chỉ 1 lần/comment và hết hạn sau 7 ngày) -> ghi rõ kênh nào."""
    pub = await reply_public(comment_id)
    want_private = await brain.comment_has_intent(text)
    priv = await reply_private(comment_id) if want_private else False
    priv_log = ("OK" if priv else "HỎNG") if want_private else "BỎ QUA (không có nhu cầu)"
    print(f"[comment] {comment_id}: công khai={'OK' if pub else 'HỎNG'} | riêng={priv_log}",
          file=sys.stderr)
    stats.log_event("comment", from_id,
                    note=f"public={pub} private={priv if want_private else 'skip'}")
    if not (pub or want_private):
        # Chủ ý không nhắn riêng -> chỉ còn public là kênh duy nhất, hỏng là khách không thấy gì.
        await alert_admins("comment:dead",
                           f"🔴 COMMENT KHÔNG ĐƯỢC TRẢ LỜI - trả lời công khai hỏng.\n"
                           f"comment_id: {comment_id}\n"
                           f"➡️ Thường do thiếu pages_manage_engagement hoặc token page hết hạn.")
        return
    if not (pub or priv):
        # Cả 2 kênh chết = khách comment xong KHÔNG nhận được gì. Chi tiết HTTP đã nằm ở
        # cảnh báo của _fb_post; đây là tin gộp cho biết comment thật sự rơi.
        await alert_admins("comment:dead",
                           f"🔴 COMMENT KHÔNG ĐƯỢC TRẢ LỜI - cả công khai lẫn nhắn riêng đều hỏng.\n"
                           f"comment_id: {comment_id}\n"
                           f"➡️ Thường do thiếu pages_manage_engagement hoặc token page hết hạn.")


# Khách gửi ẢNH/file (không kèm chữ) -> trả câu cố định, không gọi AI, không đọc ảnh.
_IMG_EVENT = "\x00IMG"           # khách gửi ẢNH/FILE thật (không chữ)
_STICKER_EVENT = "\x00STK"       # khách thả like/sticker/icon (không phải ảnh thật)
_REFERRAL_EVENT = "\x00REF"      # khách bấm quảng cáo Click-to-Messenger / link m.me / Bắt đầu -> chào chủ động
_IMAGE_REPLY = ("Dạ mẫu này bên em có khá nhiều biến thể về kích thước và loại đá ạ. "
                "Để tư vấn chính xác nhất cho Bác, Bác cho em xin số điện thoại, "
                "chuyên gia bên em sẽ liên hệ tư vấn cụ thể cho Bác ngay ạ!")


def _cau_khach_gui_anh(sdt: str) -> str:
    """Câu trả ảnh khách gửi (không đọc được). Đã có SĐT thì xác nhận số, không xin lại."""
    if sdt:
        return ("Dạ mẫu này bên em có khá nhiều biến thể về kích thước và loại đá ạ. "
                f"Chuyên gia bên em sẽ liên hệ tư vấn cụ thể cho Bác theo số {sdt} ngay ạ!")
    return _IMAGE_REPLY
# Like/sticker lần đầu: chào hỏi mời tư vấn như bình thường (KHÔNG xin SĐT dồn).
_STICKER_REPLY = ("Dạ em chào Bác ạ. Bác đang quan tâm hạng mục nào để em tư vấn giúp Bác ạ? "
                  "(Nhà thờ họ, Khu lăng mộ hay sản phẩm đá mỹ nghệ...)")
# Khách MỚI bấm quảng cáo/link mở chat (chưa gõ gì): chào chủ động mời tư vấn.
_REFERRAL_REPLY = ("Dạ em chào Bác ạ. Em là Thảo Vân bên Đá mỹ nghệ Hồn Đá. "
                   "Bác đang dự kiến làm nhà thờ họ, khu lăng mộ hay một sản phẩm "
                   "đá mỹ nghệ riêng ạ?")
# Khách CŨ bấm lại quảng cáo: chào trơ như người lạ là lộ ra bot quên sạch hội thoại trước
# -> đẩy vào AI kèm lịch sử để mở lời tiếp nối. Khách KHÔNG thấy prompt này.
_REFERRAL_PROMPT = (
    "(Khách vừa bấm lại quảng cáo mở chat nhưng CHƯA gõ gì. Mở lời TIẾP NỐI hội thoại trước, "
    "KHÔNG chào như người lạ, KHÔNG hỏi lại thứ khách đã nói: nhắc đúng 1 điều khách đã trao "
    "đổi rồi hỏi tiếp đúng 1 ý còn dở. Chưa có SĐT thì đây là lúc xin lại.)")
# Bot vừa nhắn xong mà khách bấm ad thêm lần nữa -> im, khỏi nhắn chồng lên tin chưa ai đọc.
_REFERRAL_IM_H = 0.5

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
    "2. Neu lich su truoc do da co loai da/vat lieu khach chon (dac biet granite/GRN/G20/An Do), PHAI giu loai da do khi tra gia; anh chi dung de nhan dien kieu dang, khong duoc tu doi vat lieu.\n"
    "3. DÒ trong BẢNG SẢN PHẨM tìm 1-3 mẫu GIỐNG/gần nhất với ảnh, nêu rõ mã + tên "
    "(nhắc mã -> hệ thống tự gửi ảnh mẫu kèm). Nếu không chắc mẫu chính xác, nói rõ đây là mẫu gần giống "
    "cùng dòng, đừng khẳng định chắc nịch.\n"
    "4. Nếu ảnh không phải sản phẩm đá (ảnh khác/không rõ), hỏi khách cần tư vấn gì.)")


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
                # Khách gửi ảnh (mẫu mộ, bản vẽ) mà bot KHÔNG thấy -> trả lời lệch, khách tưởng bot ngu.
                await alert_admins(f"cust-img:{type(e).__name__}",
                                   f"⚠️ KHÔNG TẢI ĐƯỢC ẢNH KHÁCH GỬI - bot trả lời mà không nhìn thấy ảnh.\n"
                                   f"{type(e).__name__}: {e}")
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
_SEND_GAP_S = 0.5   # giãn giữa 2 tin liên tiếp gửi khách -> FB không rớt/đảo tin (tránh "nuốt chữ")


def _split_sentences(text: str, per: int = 2) -> list[str]:
    """Reply -> nhiều tin ngắn ~per câu (chat tự nhiên). Gom per câu TRONG CÙNG 1 DÒNG - KHÔNG
    dồn qua ranh giới xuống dòng (list giá/bullet giữ nguyên từng dòng, không mash thành 1 câu dài).

    ponytail: viết tắt hiếm ('vd.') có thể bị tách thừa - chấp nhận, reply bot không mấy khi có."""
    text = (text or "").strip()
    if not text:
        return []
    out: list[str] = []
    for para in text.split("\n"):                     # mỗi dòng persona đặt -> tách riêng, không trộn
        para = para.strip()
        if not para:
            continue
        sents = [s.strip() for s in _SENT_RE.split(para) if s.strip()]
        for i in range(0, len(sents), per):
            chunk = " ".join(sents[i:i + per])
            out.extend(_split_text(chunk) if len(chunk) > _TEXT_MAX else [chunk])
    return out or [text]


async def _send_failed(tag: str, code: int, detail: str, psid: str) -> None:
    """Gửi FB hỏng = KHÁCH KHÔNG NHẬN ĐƯỢC GÌ, trong khi bot vẫn ghi 'ok'. Phải báo admin.
    Gộp theo (tag, code) vì token chết/rate-limit đập vào mọi khách cùng lúc."""
    # #10900 'Activity already replied to': FB chỉ cho private reply 1 lần/comment. Gặp khi FB
    # gửi lại event hoặc bot restart mất dedupe -> BÌNH THƯỜNG, báo là dạy admin phớt lờ cảnh báo.
    # Đọc MÃ LỖI THẬT của FB thay vì dò chuỗi con: '#10900' cũng chứa '#10' -> gợi ý sai bét
    # (đã bắn nhầm "ngoài cửa sổ 24h" cho lỗi 'đã trả lời rồi').
    try:
        fb_code = int(((json.loads(detail) or {}).get("error") or {}).get("code") or 0)
    except Exception:
        fb_code = 0
    if fb_code == 10900:
        return
    hint = ""
    if fb_code == 190 or "access token" in detail.lower():
        hint = "\n➡️ Nhiều khả năng MSGR_PAGE_TOKEN hết hạn/bị thu hồi - lấy token mới."
    elif fb_code in (4, 32, 613) or code == 429:
        hint = "\n➡️ FB rate-limit. Giảm BOT_MAX_CONCURRENT hoặc chờ."
    elif fb_code == 10:
        hint = "\n➡️ Ngoài cửa sổ 24h của FB - không nhắn chủ động được nữa."
    elif fb_code == 200:
        hint = "\n➡️ Thiếu quyền (pages_manage_engagement / pages_messaging) trên token page."
    if fb_code == 100 and tag == "img upload":
        hint = ("\n➡️ FB nghẹn file đính kèm (đã thử lại + ép JPEG vẫn hỏng). "
                "Kiểm tra ảnh trong Lark Base có mở được không.")
    who = await _label(psid) if psid else "(không rõ)"
    # Ảnh gửi SAU text (xem _process_inner) -> ảnh hỏng thì khách VẪN đọc được nội dung tư vấn,
    # chỉ thiếu hình. Nói "không nhận được tin" ở ca này là báo động quá mức, admin sẽ quen tay
    # bỏ qua rồi bỏ sót cả cảnh báo thật.
    thiet_hai = ("khách nhận được chữ nhưng THIẾU ẢNH" if tag == "img upload"
                 else "khách KHÔNG nhận được tin")
    await alert_admins(f"fb:{tag}:{code}",
                       f"🔴 GỬI FB HỎNG ({tag}) - {thiet_hai}\n"
                       f"Khách: {who}\nHTTP {code}: {detail}{hint}")


async def _fb_post(url: str, *, payload=None, data=None, files=None,
                   timeout: float = 20.0, tag: str = "send", psid: str = "",
                   im_lang: bool = False) -> bool:
    """POST tới FB Graph kèm access_token. <400 -> True. Lỗi/≥400 -> log [tag] + báo admin + False
    (không ném). Gom mọi chỗ gọi Send API về 1 chỗ (timeout/log/cảnh báo đồng nhất).

    im_lang=True: vẫn log ra stderr nhưng KHÔNG báo admin - dành cho lượt thử đầu của thứ có
    retry (báo ngay lượt đầu = admin nhận cảnh báo cho ca tự khỏi ở lượt sau)."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0)) as c:
            r = await c.post(url, params={"access_token": config.PAGE_TOKEN},
                             json=payload, data=data, files=files)
            if r.status_code >= 400:
                print(f"[{tag}] {r.status_code}: {r.text[:300]}", file=sys.stderr)
                if not im_lang:
                    await _send_failed(tag, r.status_code, r.text[:300], psid)
                return False
            return True
    except Exception as e:
        print(f"[{tag}] {type(e).__name__}: {e}", file=sys.stderr)
        if not im_lang:
            await _send_failed(tag, 0, f"{type(e).__name__}: {e}", psid)
        return False


def _bo_qua_vi_khoa(psid: str, tag: str, force: bool, chu_dong: bool) -> bool:
    """CHỐT CHẶN DUY NHẤT trước khi gửi bất cứ thứ gì cho khách. Cờ đọc từ database.

    - Khách khó chịu (đang trong hạn im): chặn MỌI tin, kể cả tin trả lời.
    - Khách đòi ngừng / từ chối / hoãn lại: chỉ chặn tin bot TỰ nhắn (chu_dong=True) -
      follow-up, trả lời bù, tin khách quay lại. Khách hỏi thì vẫn trả lời tử tế.

    force=True dành cho tin xoa dịu cuối cùng (tin đặt ra cái khoá) và tin admin gửi tay."""
    if force or not psid:
        return False
    chan, ly_do = state.cam_nhan_chu_dong(psid) if chu_dong else state.bi_khoa(psid)
    if chan:
        print(f"[khoa] bỏ gửi {tag} cho psid={psid}: {ly_do}", file=sys.stderr)
    return chan


async def _off(fn, *a, **kw):
    """Chạy hàm ĐỒNG BỘ chạm state/Firebase ở thread khác.

    state.get() khi cache local miss sẽ gọi Firebase ĐỒNG BỘ. Gọi thẳng trong coroutine là
    khoá event loop: cả bot ngừng nhận webhook trong lúc chờ. Sự cố 30/07/2026 - 3 tin FB bị
    reset sau 13.7s/9.4s/3.0s, /admin treo 173s, phải restart. Mọi lời gọi state.* từ code
    async PHẢI đi qua đây."""
    return await asyncio.to_thread(fn, *a, **kw)


async def send_text(psid: str, text: str, force: bool = False, chu_dong: bool = False) -> None:
    if not (config.PAGE_TOKEN and psid and text):
        return
    if await _off(_bo_qua_vi_khoa, psid, "text", force, chu_dong):
        return
    url = _SEND_API.format(ver=config.GRAPH_VER)
    for chunk in _split_text(text):
        body = {"recipient": {"id": psid}, "messaging_type": "RESPONSE",
                "message": {"text": chunk}}
        if not await _fb_post(url, payload=body, tag="send", psid=psid):
            break                                      # 1 chunk lỗi -> dừng, khỏi gửi tiếp rối


def _ep_jpeg(data: bytes) -> tuple[bytes, str] | None:
    """Ép ảnh về JPEG baseline. None nếu không giải mã được (giữ nguyên bản gốc mà gửi lại)."""
    try:
        import io

        from PIL import Image
        im = Image.open(io.BytesIO(data))
        if im.mode != "RGB":
            im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=_IMG_JPEG_Q, optimize=True)
        return (buf.getvalue(), "image/jpeg")
    except Exception as e:
        print(f"[img] ép JPEG lỗi: {type(e).__name__}: {e}", file=sys.stderr)
        return None


async def send_image_bytes(psid: str, data: bytes, ctype: str = "image/jpeg",
                           force: bool = False) -> None:
    """Gửi ảnh bằng CÁCH UPLOAD bytes thẳng (multipart). FB không phải tự fetch URL nữa
    -> ảnh tới ngay sau text, không trickle. Bytes đã warm sẵn nên gửi luôn.

    THỬ LẠI 1 lần khi hỏng: FB hay trả '#100 Upload attachment failure' (subcode 2018047)
    chập chờn dù file hợp lệ. Lượt 2 ép về JPEG baseline - loại nốt khả năng FB nghẹn PNG.
    Lượt đầu im lặng (không báo admin) để cảnh báo chỉ nổ khi ĐÃ hết cách."""
    if not (config.PAGE_TOKEN and psid and data):
        return
    if await _off(_bo_qua_vi_khoa, psid, "img", force, chu_dong=False):
        return
    url = _SEND_API.format(ver=config.GRAPH_VER)

    async def _thu(payload: bytes, ct: str, im_lang: bool) -> bool:
        ext = "png" if "png" in (ct or "") else "jpg"
        form = {
            "recipient": json.dumps({"id": psid}),
            "messaging_type": "RESPONSE",
            "message": json.dumps({"attachment": {"type": "image",
                                                  "payload": {"is_reusable": True}}}),
        }
        files = {"filedata": (f"image.{ext}", payload, ct or "image/jpeg")}
        return await _fb_post(url, data=form, files=files, timeout=30.0,
                              tag="img upload", psid=psid, im_lang=im_lang)

    if await _thu(data, ctype, im_lang=True):
        return
    await asyncio.sleep(_IMG_RETRY_GAP_S)                 # để FB thở, đừng dội lại ngay
    lai = _ep_jpeg(data) if "jpeg" not in (ctype or "") else None
    payload, ct = lai if lai else (data, ctype)
    if await _thu(payload, ct, im_lang=False):
        print(f"[img upload] thử lại THÀNH CÔNG psid={psid}", file=sys.stderr)


async def send_action(psid: str, action: str = "typing_on") -> None:
    if not (config.PAGE_TOKEN and psid):
        return
    await _fb_post(_SEND_API.format(ver=config.GRAPH_VER),
                   payload={"recipient": {"id": psid}, "sender_action": action},
                   timeout=10.0, tag="action")


_SEM = asyncio.Semaphore(config.MAX_CONCURRENT)   # trần đồng thời riêng của bot

# Gom tin (debounce) mỗi khách: đợi khách gõ xong rồi trả 1 lần thay vì rep rời rạc/bỏ tin.
_BUFFERS: dict[str, dict] = {}     # psid -> {"texts": [...], "task": Task, "first": ts}
# Khách ĐANG được xử lý (brain chạy 5-15s). Trả lời bù phải né, không thì webhook và vòng quét
# cùng trả lời 1 tin -> khách nhận 2 câu.
_INFLIGHT: set[str] = set()
_DEBOUNCE_S = 4.0                  # im bao lâu thì chốt gom
_MAX_WAIT_S = 20.0                 # trần chờ từ tin đầu (khách gõ liên tục không ngừng vẫn phải trả)
_MAX_BUFFER = 15                   # trần số tin gom 1 lượt


def _merge_texts(texts: list[str]) -> str:
    """Gom các tin thành 1. Ưu tiên: tin chữ > ảnh thật > sticker/like > referral (chào)."""
    _META = (_IMG_EVENT, _STICKER_EVENT, _REFERRAL_EVENT)
    real = [t for t in texts if t not in _META]
    if real:                                   # khách gõ thật -> bỏ chào referral, trả lời thẳng
        # Bỏ tin TRÙNG: webhook và luồng trả lời bù có thể cùng đẩy 1 tin vào buffer. Không lọc
        # thì prompt thành "xin giá\nxin giá" -> bot tưởng khách hỏi 2 lần, trả lời lặp lại.
        gon = list(dict.fromkeys(real))
        return "\n".join(gon)
    if _IMG_EVENT in texts:                    # ảnh thật ưu tiên hơn sticker
        return _IMG_EVENT
    if _STICKER_EVENT in texts:
        return _STICKER_EVENT
    return _REFERRAL_EVENT if texts else ""


_FOLLOWUP_TEXT = ("Dạ Bác ơi, không biết Bác còn đang phân vân mẫu nào không ạ? "
                  "Bác cứ nhắn em, em tư vấn thêm và gửi mẫu phù hợp cho Bác nhé!")


async def run_followups() -> None:
    """Quét khách im sau trả lời (chưa chốt) -> gửi 1 tin nhắc nhẹ. Chạy định kỳ từ app."""
    for psid, last_user_at in await _off(brain.followup_candidates, config.FOLLOWUP_AFTER_H):
        if psid in config.ADMIN_UIDS:
            continue
        if (await _off(_noise_state, psid)).get("stopped"):   # bot đã cố ý ngưng (nhiễu/sticker) -> đừng đào lại
            continue
        await send_text(psid, _FOLLOWUP_TEXT, chu_dong=True)
        # Vào log: khách đáp lại lời nhắc thì model biết CHÍNH NÓ vừa chủ động nhắn, không đọc
        # thành khách tự nhiên nhắn trống không. (Mốc tin khách cuối không đổi -> followup_
        # candidates/mark_followed vẫn tính đúng.)
        await brain.ghi_luot_async(psid, "", _FOLLOWUP_TEXT)
        await _off(brain.mark_followed, psid, last_user_at)
        stats.log_event("followup", psid)


# --- Lưới an toàn TIN RƠI: hỏi thẳng FB xem thread nào khách nhắn cuối mà page chưa trả lời ---
# Hỏi FB (không đọc lịch sử local) là CỐ Ý: tin rơi nặng nhất là tin bot CHƯA BAO GIỜ nhận
# (bot chết, token hết hạn, webhook 502) - không có trong lịch sử local nên quét file không thấy.
_MISSED_MAX_DAYS = 7            # cũ hơn = chuyện đã rồi, đào lên chỉ gây nhiễu
_MISSED_MAX_LINES = 20          # tin Lark quá dài bị cắt
_TRA_BU_TOI_DA_H = 24.0         # cửa sổ nhắn tin của FB; quá là gửi cũng bị từ chối (#10)
_page_id_cache = ""


def _spawn_bg(coro) -> None:
    """Chạy nền + GIỮ ref: task không được tham chiếu có thể bị GC nuốt giữa chừng."""
    t = asyncio.create_task(coro)
    _BG_TASKS.add(t)
    t.add_done_callback(_BG_TASKS.discard)


_BG_TASKS: set = set()


async def _page_id() -> str:
    """ID page của token hiện tại (để biết tin cuối là của page hay của khách). Cache RAM."""
    global _page_id_cache
    if not _page_id_cache:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as c:
                r = await c.get(f"https://graph.facebook.com/{config.GRAPH_VER}/me",
                                params={"access_token": config.PAGE_TOKEN, "fields": "id"})
            _page_id_cache = str((r.json() or {}).get("id") or "")
        except Exception as e:
            print(f"[missed] không lấy được page id: {type(e).__name__}: {e}", file=sys.stderr)
    return _page_id_cache


def pick_unanswered(threads: list, page_id: str, after_min: float, now: datetime) -> list[tuple]:
    """Lọc thread mà TIN CUỐI là của khách và đã quá `after_min` phút. Hàm THUẦN (test được).

    Trả [(psid, tên, nội dung, thời điểm ISO)]."""
    out = []
    for t in threads or []:
        msgs = ((t.get("messages") or {}).get("data")) or []
        if not msgs:
            continue
        m = msgs[0]                                    # FB trả tin MỚI NHẤT trước
        frm = m.get("from") or {}
        psid = str(frm.get("id") or "")
        if not psid or psid == page_id:                # page trả lời cuối -> không rơi
            continue
        created = m.get("created_time") or ""
        try:
            at = datetime.strptime(created, "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            continue
        age_min = (now - at).total_seconds() / 60
        if age_min < after_min or age_min > _MISSED_MAX_DAYS * 24 * 60:
            continue
        out.append((psid, frm.get("name") or psid, m.get("message") or "(không phải chữ)", created))
    return out


def _fb_time_to_local(at_iso: str) -> str | None:
    """Mốc ISO của FB (UTC) -> chuỗi giờ ĐỊA PHƯƠNG khớp định dạng lịch sử. Hỏng -> None."""
    try:
        return (datetime.strptime(at_iso, "%Y-%m-%dT%H:%M:%S%z")
                .astimezone().strftime("%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


def _da_tra_loi_sau(psid: str, at_iso: str) -> bool:
    """Lịch sử đã có câu trả lời SAU mốc tin đó chưa?

    Chốt cuối trước khi trả lời bù: giữa lúc hỏi FB và lúc gửi, lượt webhook có thể vừa xong
    hoặc người thật vừa rep tay. Đọc file local nên rẻ, không tốn thêm request FB.
    """
    try:
        moc = datetime.strptime(at_iso, "%Y-%m-%dT%H:%M:%S%z").astimezone().replace(tzinfo=None)
    except ValueError:
        return False
    for m in reversed(brain.load_history(psid)):
        if m.get("role") != "assistant":
            continue
        try:
            return datetime.strptime(m.get("at", ""), "%Y-%m-%d %H:%M:%S") > moc
        except ValueError:
            return False
    return False


async def run_missed_check() -> None:
    """Hỏi FB xem khách nào nhắn mà chưa được trả lời -> báo admin trả lời tay.

    Chỉ BÁO, KHÔNG tự trả lời: tin rơi thường đã cũ, bot trả lời trễ dễ lạc ngữ cảnh và
    có thể đã ngoài cửa sổ 24h của FB.
    """
    pid = await _page_id()
    if not pid:
        return
    url = f"https://graph.facebook.com/{config.GRAPH_VER}/{pid}/conversations"
    params = {"access_token": config.PAGE_TOKEN, "limit": 50,
              "fields": "id,updated_time,messages.limit(1){from,message,created_time}"}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as c:
            data = (await c.get(url, params=params)).json()
    except Exception as e:
        print(f"[missed] gọi FB lỗi: {type(e).__name__}: {e}", file=sys.stderr)
        return
    if "error" in data:
        print(f"[missed] FB trả lỗi: {json.dumps(data['error'], ensure_ascii=False)[:200]}", file=sys.stderr)
        await alert_admins("missed:api",
                           f"⚠️ KHÔNG QUÉT ĐƯỢC TIN RƠI - mất lưới an toàn, khách có thể bị bỏ mà không ai biết.\n"
                           f"{json.dumps(data['error'], ensure_ascii=False)[:200]}")
        return

    now = datetime.now(timezone.utc)
    rows = await _off(lambda: [r for r in pick_unanswered(data.get("data"), pid,
                                                          config.MISSED_AFTER_MIN, now)
                               if r[0] not in config.ADMIN_UIDS
                               and not brain.missed_already_reported(r[0], r[3])])
    if not rows:
        return

    tu_tra: list[tuple] = []      # bot trả lời bù được
    nguoi_that: list[tuple] = []  # phải người thật xử lý
    for psid, name, text, at in rows:
        try:
            tuoi_h = (now - datetime.strptime(at, "%Y-%m-%dT%H:%M:%S%z")).total_seconds() / 3600
        except ValueError:
            tuoi_h = 999
        if psid in _INFLIGHT or psid in _BUFFERS:
            # Webhook vừa tới và đang xử lý khách này -> số liệu FB đã cũ. Bỏ qua HOÀN TOÀN
            # (không đánh dấu) để vòng sau soi lại nếu lượt đó vẫn hỏng.
            print(f"[missed] bỏ qua {name}: đang xử lý ở luồng webhook", file=sys.stderr)
            continue
        if _da_tra_loi_sau(psid, at):
            # Lịch sử đã có câu trả lời SAU tin đó (người thật rep tay, hoặc lượt trước vừa xong)
            # -> không rơi nữa. Đánh dấu để khỏi soi lại mãi.
            await _off(brain.mark_missed_reported, psid, at)
            continue
        if (await _off(_noise_state, psid)).get("stopped"):
            # Bot đã CỐ Ý ngưng trả lời khách này (nhiễu/sticker liên tiếp, đã báo admin 1 lần).
            # Không đánh dấu = mỗi vòng quét lại thấy "chưa trả lời" -> báo lặp mãi mãi.
            await _off(brain.mark_missed_reported, psid, at)
            continue
        if await _off(brain.is_closed, psid):
            # Đã handoff/chốt phiếu -> chuyên gia đang cầm khách này, bot nhảy vào là phá.
            nguoi_that.append((psid, name, text, at, "đã handoff cho chuyên gia"))
        elif tuoi_h >= _TRA_BU_TOI_DA_H:
            # Ngoài cửa sổ 24h của FB: gửi RESPONSE sẽ bị từ chối (#10), có cố cũng vô ích.
            nguoi_that.append((psid, name, text, at, f"quá {int(tuoi_h)}h - ngoài cửa sổ 24h của FB"))
        elif config.MISSED_AUTOREPLY:
            tu_tra.append((psid, name, text, at))
        else:
            nguoi_that.append((psid, name, text, at, "tự trả lời bù đang TẮT"))

    for psid, name, text, at in tu_tra:
        # Đi ĐÚNG luồng tin bình thường (handle_event -> gom tin -> brain.answer -> gửi):
        # brain tự nạp lịch sử + bảng sản phẩm nên câu trả lời khớp ngữ cảnh, và mọi thứ khác
        # (handoff, gửi ảnh theo mã, ghi CRM) chạy y hệt. Viết đường trả lời riêng = 2 lối đi
        # dễ lệch nhau về sau.
        print(f"[missed] trả lời bù {name} ({psid}): {text[:40]!r}", file=sys.stderr)
        _spawn_bg(handle_event(psid, text, _fb_time_to_local(at)))
        stats.log_event("missed_autoreply", psid, note=text[:100])
        # CỐ Ý không mark: mark ở đây = "đã xong" ngay lúc mới GIAO việc. Bot chết/restart/gửi
        # hỏng giữa chừng -> khách im vĩnh viễn, không vòng nào soi lại. Trả lời xong thì lịch sử
        # có lượt assistant -> _da_tra_loi_sau chặn vòng sau; chưa xong thì _INFLIGHT/_BUFFERS chặn.

    if tu_tra:
        lines = [f"• {n}: \"{t[:60]}\"" for _, n, t, _ in tu_tra[:_MISSED_MAX_LINES]]
        await notify_admins(f"🤖 BOT TRẢ LỜI BÙ {len(tu_tra)} khách bị bỏ sót\n\n" + "\n".join(lines)
                            + "\n\n(Bot đọc lại lịch sử + bảng sản phẩm nên trả lời khớp ngữ cảnh.)")
    if nguoi_that:
        lines = [f"• {n} lúc {a[11:16]} {a[:10]} - {ly_do}\n  \"{t[:60]}\""
                 for _, n, t, a, ly_do in nguoi_that[:_MISSED_MAX_LINES]]
        more = (f"\n(và {len(nguoi_that) - _MISSED_MAX_LINES} khách nữa)"
                if len(nguoi_that) > _MISSED_MAX_LINES else "")
        await notify_admins(f"⚠️ {len(nguoi_that)} KHÁCH CẦN NGƯỜI THẬT TRẢ LỜI\n\n"
                            + "\n".join(lines) + more + "\n\n➡️ Bot KHÔNG tự trả lời các ca này.")
        for psid, _, _, at, _ly in nguoi_that:
            await _off(brain.mark_missed_reported, psid, at)
    stats.log_event("missed_check", "", note=f"bù {len(tu_tra)}, người thật {len(nguoi_that)}")


# --- Canh tunnel chết: ping PUBLIC_URL/webhook từ ngoài, đứt -> báo Lark 1 lần ---
_tunnel_alive = True    # trạng thái đã báo gần nhất (chỉ báo khi ĐỔI trạng thái, không spam)


async def _tunnel_ok() -> bool:
    """True chỉ khi SERVER MÌNH trả lời qua tunnel (200 + body == challenge).
    Phân biệt được với trang lỗi ngrok (tunnel chết nhưng domain vẫn trả HTTP)."""
    nonce = f"tw{int(time.time())}"
    url = f"{config.PUBLIC_URL}/webhook/messenger"
    params = {"hub.mode": "subscribe", "hub.verify_token": config.VERIFY_TOKEN, "hub.challenge": nonce}
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as c:
            r = await c.get(url, params=params)
        return r.status_code == 200 and r.text.strip() == nonce
    except Exception:
        return False


async def run_tunnel_check() -> None:
    """1 vòng ping. Fail liên tiếp đủ ngưỡng -> báo Lark 'tunnel chết'; sống lại -> báo phục hồi."""
    global _tunnel_alive
    if not config.PUBLIC_URL:
        return
    fails = 0
    for _ in range(max(1, config.TUNNEL_FAILS_TO_ALERT)):
        if await _tunnel_ok():
            break
        fails += 1
        if fails < config.TUNNEL_FAILS_TO_ALERT:
            await asyncio.sleep(5)
    ok = fails < config.TUNNEL_FAILS_TO_ALERT
    if not ok and _tunnel_alive:
        _tunnel_alive = False
        # Handler bị ping là GET verify - chỉ echo lại challenge, không I/O. Quá 10s tức là
        # BOT không chạy nổi event loop, gần như luôn là bot chứ không phải đường mạng.
        # Câu cũ bảo "bật lại ngrok/cloudflared" (dấu vết thời dev) khiến admin đi soi nhầm chỗ.
        await notify_admins(f"🔴 BOT KHÔNG TRẢ LỜI qua {config.PUBLIC_URL}\n"
                            f"Bot đang ĐIẾC - khách nhắn không tới.\n"
                            f"➡️ Trên VPS: docker compose logs --tail=100 bot\n"
                            f"➡️ Hỏi bot TỪ TRONG container (loại trừ Caddy/mạng):\n"
                            f"   docker compose exec bot python -c "
                            f"\"import httpx;print(httpx.get('http://localhost:7900/healthz').text)\"\n"
                            f"   Treo luôn ở đây = event loop bị chặn, không phải proxy.")
    elif ok and not _tunnel_alive:
        _tunnel_alive = True
        await notify_admins(f"🟢 BOT TRẢ LỜI LẠI: nhận tin bình thường qua {config.PUBLIC_URL}")


# --- CANH TOKEN PAGE ---
# Token chết là bot IM HOÀN TOÀN. Không có vòng này thì chỉ lộ lúc gửi tin hỏng, tức là đã có
# khách không nhận được tin rồi mới biết. Token sinh từ tài khoản cá nhân còn chết bất chợt khi
# chủ tài khoản đổi mật khẩu (OAuth #190 subcode 460), không đợi tới hạn.
_TOKEN_SAP_HET_S = 7 * 24 * 3600      # còn dưới ngần này thì kêu trước


def danh_gia_token(data: dict, now: float) -> str:
    """Soi kết quả debug_token. Trả '' nếu ổn, khác rỗng là nội dung cảnh báo. Hàm THUẦN."""
    if not data.get("is_valid"):
        ly_do = (data.get("error") or {}).get("message") or "Facebook không nói rõ lý do"
        return ("🔴 TOKEN PAGE ĐÃ CHẾT - bot KHÔNG gửi được tin nào cho khách.\n"
                f"{ly_do}\n➡️ Lấy token System User (hạn Never) rồi dán lại vào cấu hình.")
    het_han = data.get("expires_at") or 0
    if het_han and het_han - now < _TOKEN_SAP_HET_S:
        con = max(0, int((het_han - now) // 86400))
        return (f"⚠️ TOKEN PAGE SẮP HẾT HẠN - còn {con} ngày. Hết là bot im, khách nhắn không ai trả lời.\n"
                "➡️ Token System User có hạn vĩnh viễn (expires_at = 0), đổi sang loại đó là hết lo.")
    return ""


async def run_token_check() -> None:
    """1 vòng soi token page. Chết hoặc sắp hết hạn -> báo Lark (có gộp, không spam mỗi vòng)."""
    if not config.PAGE_TOKEN:
        return
    url = f"https://graph.facebook.com/{config.GRAPH_VER}/debug_token"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as c:
            r = await c.get(url, params={"input_token": config.PAGE_TOKEN,
                                         "access_token": config.PAGE_TOKEN})
        payload = r.json() or {}
    except Exception as e:
        # Mạng lỗi != token chết. Im, vòng sau soi lại - kêu ở đây chỉ tổ nhiễu.
        print(f"[token] gọi debug_token lỗi: {type(e).__name__}: {e}", file=sys.stderr)
        return
    if "error" in payload and "data" not in payload:
        # Token hỏng tới mức không tự soi được chính nó -> chắc chắn có chuyện.
        await alert_admins("fb:token:debug",
                           "🔴 KHÔNG SOI ĐƯỢC TOKEN PAGE - nhiều khả năng token đã chết.\n"
                           + json.dumps(payload["error"], ensure_ascii=False)[:200])
        return
    canh_bao = danh_gia_token(payload.get("data") or {}, time.time())
    if canh_bao:
        await alert_admins("fb:token:health", canh_bao)


async def _save_lead_to_crm(psid: str, deep: bool = True) -> None:
    """Trích lead từ hội thoại -> ghi Lark CRM. Best-effort, lỗi chỉ báo admin.

    deep=True: mốc quan trọng (handoff / khách vừa gõ số) -> tóm tắt lại bằng AI.
    deep=False: khách chat tiếp -> đồng bộ RẺ tên/địa chỉ/tỉnh từ hồ sơ, chỉ tóm tắt lại khi
    log đã dài thêm đáng kể. Không có nhánh này thì mọi thông tin khách nói SAU lúc cho số
    (địa chỉ, tỉnh, hạng mục) không bao giờ lên bảng Lark."""
    lead = await brain.extract_lead(psid, deep=deep,
                                    tom_tat_toi=await _off(lark_crm.tom_tat_toi, psid))
    if not lead:
        return
    name = await profile_name(psid)          # tên tài khoản FB - không xin tên khách nữa
    if name:
        lead["ten"] = name
    result = await asyncio.to_thread(lark_crm.upsert_lead, psid, lead)
    tag = {"created": "✅ Đã tạo lead CRM", "updated": "♻️ Đã cập nhật lead CRM"}.get(result)
    if tag:
        await _off(brain.mark_closed, psid)           # cờ ĐÃ CHỐT lên database, hết đeo bám
        code = lark_crm.lead_code(psid)               # mã Lead/Chance vừa lưu
        head = f"{tag}: {lead.get('ten') or '?'} - {lead.get('sdt') or '?'}"
        if code:
            head += f" [{code}]"
        await notify_admins(f"{head}\nKhách: {await _label(psid)}\n"
                            f"{lead.get('tinh') or ''} | {lead.get('tom_tat') or ''}")
    elif result == "error":
        # Token/quyền Lark hỏng -> lỗi lặp ở MỌI lead, gộp lại thay vì mỗi khách 1 tin.
        await alert_admins("crm:error",
                           f"🔴 GHI LEAD CRM LỖI - lead khách {await _label(psid)} "
                           f"({lead.get('sdt') or '?'}) chưa vào CRM (xem log server).")


async def _warm_images(img_tokens: list[str]) -> list[tuple[bytes, str]]:
    """Tải HẾT ảnh về xong (đợi tất cả) rồi mới trả. Giữ bytes để upload thẳng lên FB
    -> ảnh tới ngay sau text, không để FB tự fetch URL rồi trickle lẻ tẻ. Ảnh hỏng thì bỏ."""
    img_tokens = img_tokens[:_MAX_IMAGES_PER_MSG]
    if not img_tokens:
        return []
    warmed = await asyncio.gather(
        *(asyncio.to_thread(lark_image.download_media, t) for t in img_tokens),
        return_exceptions=True)
    imgs: list[tuple[bytes, str]] = []
    for tok, w in zip(img_tokens, warmed):
        if isinstance(w, Exception):
            print(f"[img] tải lỗi token {tok[:12]}...: {type(w).__name__}: {w}", file=sys.stderr)
        else:
            imgs.append(_shrink_image(*w))            # nén trước; (bytes, ctype)
    return imgs


async def _send_images(psid: str, imgs: list[tuple[bytes, str]]) -> None:
    """Gửi loạt ảnh, CÓ GIÃN CÁCH. Dội 4 attachment liên tiếp là FB hay trả
    '#100 Upload attachment failure' - text đã có giãn cách từ lâu, ảnh thì chưa."""
    for i, (data, ctype) in enumerate(imgs):
        if i:
            await asyncio.sleep(_SEND_GAP_S)
        await send_image_bytes(psid, data, ctype)


async def handle_event(psid: str, text: str, user_at: str | None = None) -> None:
    """Nhận 1 tin: gom vào buffer khách + hẹn giờ chốt. Nhiều tin dồn -> gom, trả 1 lần.

    user_at: mốc khách gửi THẬT (chỉ luồng trả lời bù truyền; webhook để None = giờ hiện tại)."""
    buf = _BUFFERS.setdefault(psid, {"texts": [], "task": None, "first": time.monotonic(),
                                     "at": None})
    buf["texts"].append(text)
    buf["at"] = buf.get("at") or user_at        # tin đầu có mốc thật thì giữ, tin sau không đè
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
    await _process(psid, _merge_texts(buf["texts"]), buf.get("at"))


async def _process(psid: str, text: str, user_at: str | None = None) -> None:
    """Xử 1 lượt đã gom: semaphore → brain → gửi lại. Lỗi = báo admin, không rep khách."""
    _INFLIGHT.add(psid)
    try:
        await _process_inner(psid, text, user_at)
    finally:
        _INFLIGHT.discard(psid)


async def _process_inner(psid: str, text: str, user_at: str | None = None) -> None:
    # Khách nhắn từ TRƯỚC khi bot lên: chưa có log -> dựng lại từ Graph API để brain trả lời
    # có ngữ cảnh. Im quá 1 ngày rồi quay lại -> báo admin qua Lark.
    # Chạy ĐẦU TIÊN, trước MỌI nhánh câu cố định: seed_history chỉ ghi khi log còn TRỐNG, mà
    # từ nay các nhánh đó cũng ghi log (brain.ghi_luot) -> để sau là seed vĩnh viễn không chạy
    # nữa, khách cũ bấm quảng cáo bị tiếp như người lạ.
    await returning.xu_ly_khach_quay_lai(psid)
    pending_urls = _PENDING_IMAGES.pop(psid, None)     # ảnh khách kèm lượt này (nếu có)
    images: list[tuple[bytes, str]] = []
    if pending_urls:
        images = await _fetch_customer_images(pending_urls)
    if text == _IMG_EVENT:
        _STICKER_COUNT.pop(psid, None)                 # ảnh thật = tương tác thật -> reset đếm
        stats.log_event("image", psid)
        if not images:
            # Tải ảnh hỏng hoặc attachment không phải ảnh (file/video) -> câu cố định cũ.
            cau = _cau_khach_gui_anh(await _off(brain.sdt_da_luu, psid))
            await send_text(psid, cau)
            # Câu này XIN SĐT -> phải vào log, không thì khách gõ số xong bot hỏi lại y hệt.
            await brain.ghi_luot_async(psid, "[khách gửi ảnh/file]", cau, user_at)
            return
        text = _IMG_ONLY_PROMPT                         # ảnh không kèm chữ -> Gemini tự nhìn ảnh tư vấn
    if text == _REFERRAL_EVENT:
        # Khách bấm quảng cáo/link mở chat, chưa gõ gì -> bot mở lời trước.
        _STICKER_COUNT.pop(psid, None)
        stats.log_event("referral", psid)
        hist = await brain.load_history_async(psid)
        if not hist:                                   # khách mới: câu chào cố định, khỏi gọi AI
            await send_text(psid, _REFERRAL_REPLY)
            # Ghi log: lượt sau model biết mình đã chào và đã hỏi hạng mục rồi. Bonus: bấm ad
            # lần 2 thì hist hết trống -> rơi vào nhánh im _REFERRAL_IM_H, không chào 2 lần.
            await brain.ghi_luot_async(psid, "", _REFERRAL_REPLY)
            return
        im_h = returning.gio_im_lang(hist, role="assistant")   # BOT vừa nhắn cách đây bao lâu
        if 0 <= im_h < _REFERRAL_IM_H:
            # Bấm ad mấy lần liên tiếp (Doàn Luyến bấm 4 lần/2 ngày) -> đừng nhắn chồng.
            print(f"[referral] {psid}: bot vừa nhắn {im_h:.2f}h trước, không mở lời lại",
                  file=sys.stderr)
            return
        text = _REFERRAL_PROMPT                        # khách cũ: AI tự mở lời theo ngữ cảnh
    if text == _STICKER_EVENT:

        decision, noise = await _off(_sticker_decision, psid)
        if decision == "blocked":
            stats.log_event("sticker_blocked", psid)
            return
        if decision == "stop":
            stats.log_event("sticker_stop", psid)
            await notify_admins(f"🔕 BOT DỪNG TRẢ LỜI: {await _label(psid)}\n"
                                f"PSID: {psid}\nLý do: {noise.get('count')} tương tác nhiễu liên tiếp, gồm sticker.\n"
                                "Bot sẽ tự mở lại khi khách gửi nội dung có ý nghĩa.")
            return
        _STICKER_COUNT[psid] = 0
        # Like/sticker/icon: lần đầu chào hỏi mời tư vấn; lần 2 liên tiếp -> im + báo admin.
        n = _STICKER_COUNT.get(psid, 0) + 1
        if len(_STICKER_COUNT) > _STICKER_COUNT_MAX:   # ponytail: chặn phình, xoá sạch (thô nhưng đủ)
            _STICKER_COUNT.clear()
        _STICKER_COUNT[psid] = n
        if n == 1:
            await send_text(psid, _STICKER_REPLY)
            await brain.ghi_luot_async(psid, "", _STICKER_REPLY)
        elif n == 2:
            stats.log_event("sticker_stop", psid)
            await notify_admins(f"🔕 Ngừng trả lời {await _label(psid)}: gửi like/sticker/icon "
                                "lần 2 liên tiếp, không có nội dung thật. Cần chuyên gia chủ "
                                "động nhắn/gọi khách.")
        # n >= 3: im lặng, không báo admin lại (tránh spam)
        return
    async with _SEM:
        _STICKER_COUNT.pop(psid, None)                 # có tin chữ thật -> reset đếm sticker

        decision, noise = await _off(_noise_decision, psid, text)
        if decision != "allow":
            # BOT im, nhưng LEAD thì không được im. Ba nhánh dưới đây đều thoát TRƯỚC
            # brain.answer nên tin này không vào log -> số khách gõ ở chính tin này phải ghi
            # thẳng vào hồ sơ, không thì trích lead đọc log không thấy gì. Ca đắt nhất: khách
            # gắt một lần bị khoá 24h, nguôi rồi quay lại nhắn SĐT - trước đây rơi thẳng vào
            # hư không, không lên Lark, không ai được báo.
            await _off(brain.ghi_sdt, psid, util.tim_sdt(text))
            await _save_lead_to_crm(psid)
        if decision == "clarify":
            stats.log_event("unclear", psid)
            await send_text(psid, _NOISE_REPLY)
            # Tin khách + câu hỏi lại đều vào log: khách nói rõ hơn ở lượt sau thì model còn
            # biết mình vừa hỏi gì, không hỏi lại vòng hai.
            await brain.ghi_luot_async(psid, text, _NOISE_REPLY, user_at)
            return
        if decision == "stop":
            stats.log_event("noise_stop", psid)
            recent = "\n".join(f"• {item}" for item in noise.get("recent", []) if item) or "(trống)"
            await notify_admins(f"🔕 BOT DỪNG TRẢ LỜI: {await _label(psid)}\n"
                                f"PSID: {psid}\nLý do: {noise.get('count')} tin mơ hồ liên tiếp.\n"
                                f"Tin gần nhất:\n{recent}\n"
                                "Bot sẽ tự mở lại khi khách gửi nội dung có ý nghĩa.")
            return
        if decision == "blocked":
            stats.log_event("noise_blocked", psid)
            return
        # Handoff cưỡng bức (tín hiệu tường minh): chặn trước AI, người thật vào ngay.
        # Khách nói thẳng là không có nhu cầu -> ghi ý định vào database NGAY, không chờ AI chấm.
        y_dinh_cung = _y_dinh_tu_khoa(text) if not images else ""
        if y_dinh_cung:
            await _off(state.patch, psid, y_dinh=y_dinh_cung)
        forced = _forced_handoff_reason(text) if not images else None
        if forced:
            doi_ngung = forced == _LY_DO_DOI_NGUNG
            kho_chiu = forced in _LY_DO_KHO_CHIU
            stats.log_event("khach_kho_chiu" if kho_chiu else "handoff", psid)
            # Khách đang gắt: câu mời để lại SĐT lúc này chỉ đổ thêm dầu -> xoa dịu rồi im.
            # force: tin xoa dịu là tin ĐẶT RA cái khoá, không được tự chặn chính nó.
            # Nhánh này THOÁT SỚM (không qua brain.answer): số khách gõ trong chính tin này phải
            # tự ghi vào hồ sơ, nếu không thì _save_lead_to_crm bên dưới đọc log không thấy số.
            sdt = await _off(brain.ghi_sdt, psid, util.tim_sdt(text))
            chot = _NGUNG_REPLY if doi_ngung else (
                _XOA_DIU_REPLY if kho_chiu else _cau_chuyen_nguoi_that(sdt))
            await send_text(psid, chot, force=True)
            # Vào log cả tin khách lẫn câu chốt: hết khoá, khách nhắn tiếp thì model đọc được
            # cả lý do chuyển người thật, không tiếp như chưa có chuyện gì.
            await brain.ghi_luot_async(psid, text, chot, user_at)
            if kho_chiu:
                await _off(_pause_bot, psid, forced)
            await notify_admins(f"{'😠 KHÁCH KHÓ CHỊU - BOT ĐÃ NGƯNG NHẮN' if kho_chiu else '🔔 CHUYỂN NGƯỜI THẬT'}"
                                f": {await _label(psid)}\nPSID: {psid}\nLý do: {forced}\nTin khách: {text}"
                                + (f"\n➡️ Cần người thật gọi/nhắn cho khách. Bot tự im {_PAUSE_H:g} giờ."
                                   if kho_chiu else ""))
            await _save_lead_to_crm(psid)
            return
        await send_action(psid, "typing_on")
        # to_thread: is_new_customer có thể chạm Firebase (cache miss) - offload để
        # Firebase chậm/chết không block event loop (mọi khách khác đứng).
        is_new = await asyncio.to_thread(brain.is_new_customer, psid)  # TRƯỚC khi brain ghi lịch sử
        t0 = time.monotonic()
        try:
            reply = await brain.answer(psid, text, images or None, user_at)
        except Exception as e:
            # KHÔNG nhắn gì cho khách: câu xin lỗi tự động làm lộ lỗi hệ thống, mà khách vẫn phải
            # chờ. Lịch sử KHÔNG được ghi (brain chết trước _save_hist) -> vòng quét tin rơi
            # (mỗi MISSED_CHECK_MIN phút) thấy tin chưa ai trả lời và cho bot TRẢ LỜI BÙ.
            # Còn lỗi thì vòng sau soi lại tiếp, tới khi trả lời được hoặc quá 24h của FB.
            print(f"[handle] {type(e).__name__}: {e}", file=sys.stderr)
            stats.log_event("error", psid, note=f"{type(e).__name__}: {e}")
            # Gộp theo LOẠI lỗi: key sai/hết quota Gemini làm MỌI khách fail, báo từng khách = bão.
            await alert_admins(f"brain:{type(e).__name__}",
                               f"⚠️ LỖI BOT khi trả lời khách {await _label(psid)}\n"
                               f"{type(e).__name__}: {e}\nTin khách: {text}\n"
                               f"➡️ Khách CHƯA nhận được gì. Bot sẽ tự trả lời bù ở vòng quét tới "
                               f"({config.MISSED_AFTER_MIN:g} phút); lỗi kéo dài thì cần người thật vào.")
            return
        reply, handoff_reason = _extract_handoff(reply)
        reply, pause_reason = _extract_pause(reply)
        reply, img_tokens = _extract_images(reply)
        reply = _bo_marker_thua(reply, psid)
        imgs = await _warm_images(img_tokens)          # tải trước: nhánh nào cũng phải gửi được ảnh
        if pause_reason:
            # Khách khó chịu: gửi NỐT tin xoa dịu (im bặt giữa chừng còn khó chịu hơn), rồi im.
            stats.log_event("khach_kho_chiu", psid)
            await _off(_pause_bot, psid, pause_reason)
            if reply:
                await send_text(psid, reply, force=True)   # tin cuối, gửi trước khi khoá có hiệu lực
            await notify_admins(f"😠 KHÁCH KHÓ CHỊU - BOT ĐÃ NGƯNG NHẮN: {await _label(psid)}\n"
                                f"PSID: {psid}\nLý do: {pause_reason}\n"
                                f"Tin khách: {text}\n"
                                f"Bot trả lời (tin cuối): {reply}\n"
                                f"➡️ Cần người thật gọi/nhắn cho khách. Bot tự im {_PAUSE_H:g} giờ.")
            await _save_lead_to_crm(psid)
            return
        if handoff_reason:
            # Handoff: gửi câu trả lời + ẢNH cho KHÁCH trước, rồi báo admin (kèm lý do).
            # Ảnh phải gửi ở đây: bot vẫn giữ cuộc chat sau handoff, bỏ ảnh là khách hỏi mẫu
            # xong chỉ nhận chữ trơn đúng lúc đang quan tâm nhất.
            stats.log_event("handoff", psid, duration_s=time.monotonic() - t0)
            if reply:
                await send_text(psid, reply)
            await _send_images(psid, imgs)
            await notify_admins(f"🔔 KHÁCH CẦN CHUYÊN GIA: {await _label(psid)}\n"
                                f"Lý do: {handoff_reason}\n\n{reply}")
            await _save_lead_to_crm(psid)
            return
        stats.log_event("ok", psid, duration_s=time.monotonic() - t0)
        if reply:
            chunks = _split_sentences(reply)           # tách 1-2 câu/tin -> chat tự nhiên
            for i, chunk in enumerate(chunks):
                if i:
                    await asyncio.sleep(_SEND_GAP_S)   # giãn giữa các tin -> FB không rớt/đảo (hết "nuốt chữ")
                await send_text(psid, chunk)
        await _send_images(psid, imgs)
        # Thông báo admin SAU khi đã trả lời khách (không bắt khách chờ). Admin tự nhắn thì bỏ qua.
        if psid not in config.ADMIN_UIDS:
            if is_new:
                await notify_admins(f"👋 KHÁCH MỚI: {await _label(psid)}\nTin đầu: {text}\n"
                                    f"Bot trả lời: {reply}")
            phone = util.tim_sdt(text)
            if phone:
                await notify_admins(f"📞 KHÁCH ĐỂ LẠI SĐT: {phone} - {await _label(psid)}\n"
                                    f"Tin khách: {text}")
            # SĐT = lead thật, kể cả khi chưa handoff. Trước đây chỉ handoff mới ghi CRM nên
            # khách để số giữa cuộc tư vấn là mất lead. upsert theo psid -> không trùng.
            #
            # Chạy MỌI lượt, không chỉ lượt có SĐT trong tin: khách cho số ở lượt 1 rồi mới nói
            # địa chỉ/tỉnh/hạng mục ở các lượt sau - bản cũ không lượt nào chạm CRM nên bảng
            # Lark đứng yên ở trạng thái lúc vừa nhận số. Khách chưa cho số -> extract_lead trả
            # None, thoát ngay không tốn gì. deep chỉ khi khách vừa gõ số -> không đẻ thêm
            # 1 lượt AI cho mỗi tin.
            await _save_lead_to_crm(psid, deep=bool(phone))
