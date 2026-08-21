"""Khách cũ quay lại: dựng lại lịch sử từ Graph API + báo admin qua Lark.

Bot lên SAU khi page đã chạy một thời gian -> khách từng nhắn TRƯỚC đó không có log ở
local lẫn Firebase. Bot tiếp như người lạ, hỏi lại từ đầu những thứ khách đã nói.

Module vá đúng chỗ đó, 2 việc mỗi khi khách nhắn tới:
  1. Chưa có log -> kéo hội thoại cũ của RIÊNG khách đó từ Graph API, dựng lại log để
     brain trả lời có ngữ cảnh.
  2. Khách im >= NGUONG_QUAY_LAI_H rồi nhắn lại -> báo admin qua Lark, kèm SĐT + mã lead
     nếu đã có hồ sơ CRM.

Mọi lỗi đều NUỐT: nạp lịch sử hỏng không được chặn bot trả lời khách. Thà trả lời thiếu
ngữ cảnh còn hơn im lặng.
"""
import re
import sys
import time
from datetime import datetime

import httpx

import brain
import config
import state
from bot_tools import lark_crm

_MAX_TIN = 30                 # số tin cũ kéo về/khách. Đủ ngữ cảnh, chặn phình token Gemini.
NGUONG_QUAY_LAI_H = 24.0      # im lâu hơn ngần này rồi nhắn lại = "quan tâm lại", báo admin
_TIMEOUT_S = 15.0             # Graph chậm KHÔNG được giữ khách chờ -> cắt sớm, bỏ qua nạp

_page_id_cache: str = ""


async def _graph(path: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(_TIMEOUT_S)) as c:
        r = await c.get(f"https://graph.facebook.com/{config.GRAPH_VER}/{path}",
                        params={**params, "access_token": config.PAGE_TOKEN})
    return r.json() or {}


async def _page_id() -> str:
    """Id page (cache RAM). Cần để phân biệt tin của page với tin của khách."""
    global _page_id_cache
    if not _page_id_cache:
        d = await _graph("me", {"fields": "id"})
        _page_id_cache = str(d.get("id") or "")
    return _page_id_cache


# Tin do Meta/automation của Page sinh ra, KHÔNG phải bot nói. Nạp vào log dưới role
# "assistant" là model coi đó như văn mẫu của chính mình rồi CHÉP LẠI vào câu trả lời -
# khách đã nhận nguyên câu quảng cáo dán ở đuôi tin tư vấn.
_TIN_HE_THONG = re.compile(
    r"replied to an ad|started a conversation|was sent from|đã trả lời quảng cáo"
    r"|^bạn đã gửi|^you sent", re.I)
# Auto-reply quảng cáo của Page: câu chào rập khuôn xin SĐT, luôn giống hệt nhau mỗi lần
# khách bấm ad. Nhận diện bằng cụm đặc trưng để không nuốt nhầm tin tư vấn thật.
_TIN_TRANG_THAI_LEAD = re.compile(
    r"^lead (?:stage|status) (?:set to|changed to)\b|^assigned to\b"
    r"|^lead (?:stage|status):\b", re.I)
_CHAO_QUANG_CAO = re.compile(
    r"(quan tâm đến hạng mục|để nắm rõ nhu cầu cũng như mong muốn)", re.I)
# Câu auto-reply đó CHÍNH LÀ câu xin SĐT. Bỏ hẳn khỏi log thì khách bấm ad, được Page hỏi số,
# gõ số ra -> model chỉ thấy một dãy số trơ không đầu không đuôi: nó chào lại như người lạ rồi
# XIN SỐ LẦN NỮA (khách 27234963929538234, 28/07: gõ số ở tin đầu, 40 giây sau bị hỏi lại).
# Nên: giữ DẤU VẾT "Page đã xin số", bỏ CHỮ của quảng cáo để model không chép lại.
_CHAO_QUANG_CAO_NOTE = (
    "[Ghi chú hệ thống - KHÔNG chép lại câu này cho khách] Page đã tự động chào và ĐÃ XIN SỐ "
    "ĐIỆN THOẠI của khách. Số khách gõ sau đây là để trả lời câu xin số đó: xác nhận đã nhận "
    "số, TUYỆT ĐỐI không xin lại.")


def _bo_tin_page_may(noi_dung: str, la_page: bool) -> bool:
    """True = bỏ tin này khỏi lịch sử nạp lại."""
    if _TIN_HE_THONG.search(noi_dung):
        return True
    return la_page and bool(_TIN_TRANG_THAI_LEAD.search(noi_dung))


def doi_tin_fb(msgs: list, page_id: str, fb_time_to_local) -> list:
    """Tin thô Graph -> log bot ({"role", "content", "at"}), cũ trước mới sau.

    Graph trả MỚI TRƯỚC nên phải đảo. Bỏ: tin rỗng (ảnh/sticker/file - API không trả nội
    dung, giữ lại chỉ tạo lượt trống), tin hệ thống FB, và tin Page trùng lặp liên tiếp.
    Auto-reply quảng cáo của Page KHÔNG bỏ mà thay bằng _CHAO_QUANG_CAO_NOTE."""
    out = []
    for m in reversed(msgs or []):
        noi_dung = (m.get("message") or "").strip()
        if not noi_dung:
            continue
        la_page = str((m.get("from") or {}).get("id") or "") == page_id
        if _bo_tin_page_may(noi_dung, la_page):
            continue
        if la_page and _CHAO_QUANG_CAO.search(noi_dung):
            noi_dung = _CHAO_QUANG_CAO_NOTE             # giữ việc "đã xin số", bỏ chữ quảng cáo
        if out and out[-1]["content"] == noi_dung:      # Page/khách gửi trùng liên tiếp
            continue
        ban_ghi = {"role": "assistant" if la_page else "user", "content": noi_dung}
        at = m.get("created_time")
        if at:
            try:
                ban_ghi["at"] = fb_time_to_local(at)
            except Exception:
                pass                      # mốc hỏng -> bỏ mốc, nội dung vẫn dùng được
        out.append(ban_ghi)
    return out


async def _keo_lich_su_cu(psid: str, fb_time_to_local) -> list:
    """Hội thoại cũ của 1 khách từ Graph API. [] nếu không có/lỗi/thiếu quyền."""
    d = await _graph("me/conversations", {
        "user_id": psid,
        "fields": f"messages.limit({_MAX_TIN})" + "{from,message,created_time}"})
    if "error" in d:
        # Thường là thiếu pages_read_user_content. Log 1 dòng, KHÔNG báo admin: khách nào
        # cũng dính thì thành bão thông báo, mà bot vẫn chạy được (chỉ mất ngữ cảnh cũ).
        print(f"[quaylai] đọc hội thoại {psid} lỗi: {d['error'].get('message', '')[:150]}",
              file=sys.stderr)
        return []
    for conv in d.get("data", []) or []:
        tin = (conv.get("messages") or {}).get("data") or []
        if tin:
            return doi_tin_fb(tin, await _page_id(), fb_time_to_local)
    return []


def _da_bao(psid: str, moc: str) -> bool:
    """Đã báo admin đúng lần quay lại này chưa? Chặn báo lặp khi khách nhắn liền mấy tin."""
    return str(state.get(psid).get("returning_at") or "").strip() == moc


def _ghi_mark(psid: str, moc: str) -> None:
    """Mốc lần quay lại đã báo - lưu database, không còn file .quaylai local."""
    state.patch(psid, returning_at=moc)


def gio_im_lang(msgs: list, bay_gio: datetime | None = None, role: str | None = None) -> float:
    """Số giờ từ tin CUỐI của `role` (None = mọi role) tới giờ. -1 nếu không đọc được mốc nào.

    Hai người gọi hỏi hai chuyện KHÁC NHAU, phải tách bằng `role`:
      - "khách im bao lâu rồi quay lại?"  -> role="user"
      - "bot vừa nhắn cách đây bao lâu?"  -> role="assistant"
    Để chung (lấy tin cuối bất kể của ai) thì mỗi tin bot tự gửi lại reset đồng hồ im lặng của
    KHÁCH: bot nhắc khách lúc T, khách đáp lúc T+2h -> tính ra "im 2 giờ", nuốt mất thông báo
    🔁 KHÁCH QUAN TÂM LẠI của đúng khách vừa được đánh thức.
    """
    moc = next((m.get("at") for m in reversed(msgs or [])
                if m.get("at") and (role is None or m.get("role") == role)), None)
    if not moc:
        return -1.0
    try:
        truoc = datetime.strptime(moc, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return -1.0
    return ((bay_gio or datetime.now()) - truoc).total_seconds() / 3600


def _tom_tat_crm(psid: str) -> str:
    """Dòng hồ sơ CRM cho thông báo admin. Rỗng nếu khách chưa có hồ sơ."""
    meta = lark_crm._load_meta(psid) or {}
    if not meta:
        return ""
    phan = [f"Mã lead: {meta['lead_code']}"] if meta.get("lead_code") else []
    if meta.get("sdt"):
        phan.append(f"SĐT: {meta['sdt']}")
    if meta.get("updated"):
        phan.append(f"Lần chốt trước: {meta['updated']}")
    return "\n".join(phan)


async def xu_ly_khach_quay_lai(psid: str) -> None:
    """Gọi TRƯỚC brain.answer mỗi lượt khách nhắn. Nuốt mọi lỗi."""
    try:
        import messenger                      # import trong hàm: messenger đã import module này

        msgs = await brain.load_history_async(psid)
        if not msgs:
            # Khách nhắn từ trước khi bot lên -> dựng lại log từ Graph API.
            cu = await _keo_lich_su_cu(psid, messenger._fb_time_to_local)
            if cu and await brain.seed_history_async(psid, cu):
                print(f"[quaylai] nạp {len(cu)} tin cũ cho {psid}", file=sys.stderr)
                msgs = cu

        if not msgs:
            return                            # khách mới thật -> luồng khách mới lo, không báo

        im_h = gio_im_lang(msgs, role="user")     # KHÁCH im bao lâu, không tính tin bot tự gửi
        if im_h < NGUONG_QUAY_LAI_H:
            return                            # đang trò chuyện liên tục, không phải "quay lại"

        moc = next((m.get("at") for m in reversed(msgs)
                    if m.get("at") and m.get("role") == "user"), "") or ""
        if _da_bao(psid, moc):
            return

        ho_so = _tom_tat_crm(psid)
        than = (f"🔁 KHÁCH QUAN TÂM LẠI: {await messenger._label(psid)}\n"
                f"Im {int(im_h / 24)} ngày ({int(im_h)} giờ) rồi nhắn lại.\n")
        if ho_so:
            than += ho_so + "\n"
        than += "➡️ Khách cũ - bot đã có ngữ cảnh trước đó, chuyên gia nên gọi lại sớm."
        await messenger.notify_admins(than)
        _ghi_mark(psid, moc)
        print(f"[quaylai] báo admin {psid}: im {int(im_h)}h", file=sys.stderr)
    except Exception as e:
        print(f"[quaylai] {type(e).__name__}: {e}", file=sys.stderr)

