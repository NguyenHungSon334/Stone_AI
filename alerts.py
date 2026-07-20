"""
Báo lỗi/cảnh báo về group Lark của admin. ĐỒNG BỘ + không phụ thuộc module nào của bot
(chỉ config) -> brain/fb/messenger/app đều import được, không sinh vòng import.

Gộp (throttle) là bắt buộc chứ không phải tối ưu: 1 sự cố hệ thống (token page chết, hết
quota Gemini, Firebase sập) đập vào MỌI khách cùng lúc - báo thô = bão tin nhắn, admin tắt
thông báo, mất luôn tác dụng cảnh báo.
"""
import base64
import hashlib
import hmac
import sys
import threading
import time

import httpx

import config

ALERT_WINDOW_S = 900.0                      # cùng 1 key chỉ báo 1 lần / 15 phút
_ALERTS: dict[str, dict] = {}               # key -> {"until": ts, "dropped": n}
_LOCK = threading.Lock()                    # gọi từ nhiều thread nền (fb mirror, brain)


def _sign(secret: str, ts: str) -> str:
    """Chữ ký custom-bot Lark: HMAC-SHA256(key=f'{ts}\\n{secret}', msg='') -> base64."""
    h = hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
    return base64.b64encode(h).decode()


def post_lark(text: str) -> tuple[bool, str]:
    """POST 1 tin vào Lark webhook. Trả (ok, chi_tiết). URL rỗng -> (False, 'chưa cấu hình')."""
    url = config.LARK_WEBHOOK_URL
    if not url:
        return (False, "Chưa cấu hình LARK_WEBHOOK_URL")
    body: dict = {"msg_type": "text", "content": {"text": text}}
    if config.LARK_WEBHOOK_SECRET:
        ts = str(int(time.time()))
        body["timestamp"] = ts
        body["sign"] = _sign(config.LARK_WEBHOOK_SECRET, ts)
    try:
        r = httpx.post(url, json=body, timeout=10.0)
        d = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        code = d.get("code")
        if code in (0, None):
            return (True, "OK")
        return (False, f"Lark code={code}: {d.get('msg', '')}")
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}")


# Sức khoẻ CHÍNH kênh báo lỗi. Lark hỏng thì mọi cảnh báo khác bay hơi mà không ai hay
# -> ghi lại đây để /healthz và dashboard soi được (không tự báo được qua chính kênh đang hỏng).
health: dict = {"configured": False, "last_ok": None, "last_error": None, "failed": 0}


def status() -> dict:
    """Sức khoẻ kênh cảnh báo. `configured` tính LÚC ĐỌC từ config - nếu chỉ cập nhật trong
    notify() thì bot chưa gửi cảnh báo nào sẽ báo nhầm 'chưa cấu hình Lark'."""
    return {**health, "configured": bool(config.LARK_WEBHOOK_URL)}


def notify(text: str) -> None:
    """Gửi thẳng 1 tin cho admin (KHÔNG gộp). Dùng cho thông báo nghiệp vụ: khách mới, sđt,
    handoff - mỗi cái là 1 sự kiện riêng, gộp là mất việc."""
    health["configured"] = bool(config.LARK_WEBHOOK_URL)
    if not config.LARK_WEBHOOK_URL:
        health["last_error"] = "Chưa cấu hình LARK_WEBHOOK_URL - KHÔNG có cảnh báo nào tới admin"
        health["failed"] += 1
        print("[lark] chưa cấu hình LARK_WEBHOOK_URL -> cảnh báo bị nuốt", file=sys.stderr)
        return
    ok, detail = post_lark(text)
    if ok:
        health["last_ok"] = time.strftime("%Y-%m-%d %H:%M:%S")
        health["last_error"] = None
    else:
        health["failed"] += 1
        health["last_error"] = detail
        print(f"[lark] webhook {detail}", file=sys.stderr)


def _should_send(key: str) -> tuple[bool, int]:
    """Cửa sổ gộp. Trả (có_gửi, số_lần_đã_dồn). Giữ lock ngắn, KHÔNG gọi mạng trong lock."""
    now = time.time()
    with _LOCK:
        st = _ALERTS.get(key)
        if st and now < st["until"]:
            st["dropped"] += 1
            return (False, 0)
        dropped = st["dropped"] if st else 0
        _ALERTS[key] = {"until": now + ALERT_WINDOW_S, "dropped": 0}
        return (True, dropped)


def alert(key: str, text: str) -> None:
    """Báo LỖI có gộp. `key` = loại sự cố (KHÔNG kèm psid), không thì gộp vô nghĩa."""
    send, dropped = _should_send(key)
    if not send:
        return
    if dropped:
        text += f"\n\n(+{dropped} lần nữa trong {int(ALERT_WINDOW_S // 60)} phút qua)"
    notify(text)
