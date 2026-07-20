"""
Bảng điều khiển: TÌNH HÌNH THẬT của page + chi phí, gom về 1 chỗ để chủ shop tự kiểm soát.

Trả lời đúng 4 câu hỏi kinh doanh:
  1. Có khách nào đang bị bỏ không?   (inbox chưa trả lời + comment chưa trả lời)
  2. Đang tốn bao nhiêu tiền?          (hôm nay / 7 ngày / dự phóng + khách tốn nhất)
  3. Có gì hỏng / thiếu?               (cấu hình thiếu, kênh cảnh báo, lỗi gần đây)
  4. Bot làm ăn ra sao?                (số khách, tỉ lệ trả lời được, handoff, lead)

Gọi FB Graph nên CÓ CACHE: dashboard mở nhiều tab / F5 liên tục không được đấm API.
Mọi lỗi mạng đều nuốt và ghi vào phần `loi` - bảng điều khiển hỏng KHÔNG được kéo bot chết.
"""
import sys
import time

import httpx

import alerts
import brain
import config
import messenger
import stats

_CACHE: dict = {"at": 0.0, "data": None}
_CACHE_TTL_S = 60.0
_MAX_POSTS = 10            # số post gần nhất soi comment chưa trả lời
_MAX_THREADS = 50


async def _graph(path: str, params: dict) -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(25.0)) as c:
        r = await c.get(f"https://graph.facebook.com/{config.GRAPH_VER}/{path}",
                        params={**params, "access_token": config.PAGE_TOKEN})
    return r.json() or {}


async def _page_info() -> dict:
    """Danh tính page + hạn token. Token hết hạn = bot chết câm, phải thấy TRƯỚC khi mất khách."""
    d = await _graph("me", {"fields": "id,name,fan_count"})
    if "error" in d:
        return {"loi": d["error"].get("message", "")[:200]}
    tok = await _graph("debug_token", {"input_token": config.PAGE_TOKEN})
    info = (tok.get("data") or {})
    exp = info.get("expires_at") or 0
    return {
        "id": d.get("id"), "ten": d.get("name"), "theo_doi": d.get("fan_count"),
        "token_loai": info.get("type"), "token_con_han": bool(info.get("is_valid")),
        "token_het_han": time.strftime("%Y-%m-%d %H:%M", time.localtime(exp)) if exp else "vĩnh viễn",
    }


async def _inbox_cho(page_id: str) -> list[dict]:
    """Khách nhắn inbox mà page CHƯA trả lời (dùng chung bộ lọc với lưới cảnh báo tin rơi)."""
    d = await _graph(f"{page_id}/conversations", {
        "limit": _MAX_THREADS,
        "fields": "id,updated_time,messages.limit(1){from,message,created_time}"})
    if "error" in d:
        raise RuntimeError(d["error"].get("message", "")[:200])
    from datetime import datetime, timezone
    rows = messenger.pick_unanswered(d.get("data"), page_id, config.MISSED_AFTER_MIN,
                                     datetime.now(timezone.utc))
    return [{"psid": p, "ten": ten, "tin": txt[:100], "luc": at,
             "da_chot": brain.is_closed(p)} for p, ten, txt, at in rows]


async def _comment_cho(page_id: str) -> list[dict]:
    """Comment của khách mà page chưa trả lời dưới đó.

    Lưới cảnh báo tin rơi chỉ soi inbox - comment là đường khách vào KHÁC hẳn, bỏ sót là mất
    khách ngay trên bài đang chạy quảng cáo.
    """
    d = await _graph(f"{page_id}/posts", {
        "limit": _MAX_POSTS,
        "fields": "id,created_time,message,comments.limit(25){id,message,from,created_time,comments.limit(5){from}}"})
    if "error" in d:
        raise RuntimeError(d["error"].get("message", "")[:200])
    out = []
    for post in d.get("data", []):
        for c in ((post.get("comments") or {}).get("data") or []):
            frm = (c.get("from") or {})
            if str(frm.get("id")) == page_id:            # comment của chính page
                continue
            replies = ((c.get("comments") or {}).get("data") or [])
            if any(str((r.get("from") or {}).get("id")) == page_id for r in replies):
                continue                                  # page đã rep dưới comment này
            out.append({"comment_id": c.get("id"), "ten": frm.get("name") or "?",
                        "tin": (c.get("message") or "")[:100], "luc": c.get("created_time"),
                        "post_id": post.get("id"),
                        "post": (post.get("message") or "(post không chữ)")[:60]})
    return out


def _van_de(page: dict, alerts_health: dict, loi: dict) -> list[dict]:
    """Danh sách việc CẦN LÀM, nặng trước. Mỗi mục nói rõ hậu quả chứ không chỉ tên lỗi."""
    v: list[dict] = []

    def add(muc, tieu_de, chi_tiet):
        v.append({"muc": muc, "tieu_de": tieu_de, "chi_tiet": chi_tiet})

    thieu = [k for k, val in {"MSGR_PAGE_TOKEN": config.PAGE_TOKEN,
                              "MSGR_VERIFY_TOKEN": config.VERIFY_TOKEN,
                              "MSGR_APP_SECRET": config.APP_SECRET,
                              "GEMINI_API_KEY": config.GEMINI_API_KEY}.items() if not val]
    if thieu:
        add("nang", f"Thiếu cấu hình: {', '.join(thieu)}", "Bot KHÔNG trả lời được khách nào.")
    if page.get("loi"):
        add("nang", "Không hỏi được Facebook", f"{page['loi']} - có thể token hỏng/hết hạn.")
    elif page.get("token_loai") and page["token_loai"] != "PAGE":
        add("nang", f"Token sai loại ({page['token_loai']}, cần PAGE)",
            "Token USER không gửi tin thay page được.")
    elif page.get("token_het_han") != "vĩnh viễn":
        add("vua", f"Token hết hạn {page['token_het_han']}",
            "Hết hạn là bot chết câm, khách nhắn không ai biết. Nên đổi sang token vĩnh viễn.")
    if not alerts_health.get("configured"):
        add("nang", "Chưa cấu hình cảnh báo Lark",
            "Mọi lỗi của bot sẽ bị nuốt, không ai được báo.")
    elif alerts_health.get("last_error"):
        add("vua", "Gửi cảnh báo Lark đang lỗi", str(alerts_health["last_error"])[:150])
    if not (config.FIREBASE_CRED and config.FIREBASE_DB_URL):
        add("nhe", "Firebase tắt", "Lịch sử chat chỉ nằm trên máy, mất máy là mất sạch.")
    if not (config.LARK_APP_ID and config.LARK_CRM_APP_TOKEN):
        add("vua", "Chưa cấu hình CRM Lark", "Khách để lại SĐT nhưng lead KHÔNG được ghi vào CRM.")
    for nguon, msg in loi.items():
        add("vua", f"Không lấy được dữ liệu {nguon}", str(msg)[:150])
    thu_tu = {"nang": 0, "vua": 1, "nhe": 2}
    return sorted(v, key=lambda x: thu_tu.get(x["muc"], 9))


async def snapshot(days: int = 30, force: bool = False) -> dict:
    """Toàn cảnh page + chi phí. Cache 60s (mỗi lần gọi = vài request Graph)."""
    now = time.time()
    if not force and _CACHE["data"] and now - _CACHE["at"] < _CACHE_TTL_S:
        return {**_CACHE["data"], "tu_cache": True}

    loi: dict[str, str] = {}
    suc_khoe = alerts.status()
    page = await _page_info()
    page_id = page.get("id") or ""

    inbox_cho: list = []
    comment_cho: list = []
    if page_id:
        try:
            inbox_cho = await _inbox_cho(page_id)
        except Exception as e:
            loi["inbox"] = f"{type(e).__name__}: {e}"
        try:
            comment_cho = await _comment_cho(page_id)
        except Exception as e:
            loi["comment"] = f"{type(e).__name__}: {e}"

    chi_phi = stats.cost_breakdown(days)
    tom_tat = stats.summary(7)
    loi_gan_day = stats.recent_errors(7)

    data = {
        "luc": time.strftime("%Y-%m-%d %H:%M:%S"),
        "page": page,
        "dang_cho": {
            "inbox": inbox_cho,
            "comment": comment_cho,
            "tong": len(inbox_cho) + len(comment_cho),
        },
        "chi_phi": chi_phi,
        "hoat_dong": tom_tat,
        "loi_gan_day": loi_gan_day,
        "canh_bao": suc_khoe,
        "van_de": _van_de(page, suc_khoe, loi),
        "tu_cache": False,
    }
    _CACHE.update(at=now, data=data)
    return data
