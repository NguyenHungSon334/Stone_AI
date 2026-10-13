"""
Cấu hình bot Messenger standalone. Đọc từ .env (dotenv) + persona.md.

Cố tình đơn giản: 1 bot 1 page, mọi thứ qua biến môi trường. Ghép vào Javis OS về
sau thì thay lớp này bằng đọc settings.json - phần còn lại (messenger/brain/app) giữ nguyên.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
DOCS_DIR = ROOT / "Document_ChatBot_Mess"
CATALOG_CSV = DOCS_DIR / "Danh_Muc_San_Pham.csv"

load_dotenv(ROOT / ".env")


def _split(v: str) -> list[str]:
    return [x.strip() for x in (v or "").split(",") if x.strip()]


# MẶC ĐỊNH của mọi khoá .env chỉnh được từ dashboard. NGUỒN DUY NHẤT: trang Cấu hình đọc
# thẳng dict này, mọi os.getenv bên dưới cũng lấy từ đây.
# Trước đây admin.py giữ một bản chép tay riêng -> lệch dần (UI hiện "nhắc sau 4 giờ" trong khi
# bot chạy 23 giờ, "tin rơi 10 phút" trong khi thật là 2) và người xem chỉnh theo số sai.
MAC_DINH = {
    "MSGR_GRAPH_VER": "v21.0",
    "BOT_MODEL": "flash",
    "BOT_FOLLOWUP_ENABLED": "1",
    "BOT_FOLLOWUP_AFTER_H": "23",
    "BOT_FOLLOWUP_CHECK_MIN": "15",
    "BOT_MISSED_AFTER_MIN": "2",
    "BOT_MISSED_CHECK_MIN": "2",
    "BOT_MISSED_AUTOREPLY": "1",
    "BOT_MAX_CONCURRENT": "4",
    "BOT_TUNNEL_WATCH": "1",
    "BOT_TUNNEL_CHECK_MIN": "3",
    "BOT_STATS_KEEP_DAYS": "2",
    "BOT_STATS_PRUNE_H": "12",
    "LARK_DOMAIN": "https://open.larksuite.com",
    "LARK_PRODUCT_FIELD": "Mã sản phẩm",
    # Nhiều cột ảnh ngăn bằng dấu phẩy, thứ tự = thứ tự ưu tiên gửi khách.
    "LARK_IMAGE_FIELD": "Ảnh chính,Ảnh phụ",
}


PAGE_TOKEN = os.getenv("MSGR_PAGE_TOKEN", "").strip()
VERIFY_TOKEN = os.getenv("MSGR_VERIFY_TOKEN", "").strip()
APP_SECRET = os.getenv("MSGR_APP_SECRET", "").strip()
GRAPH_VER = os.getenv("MSGR_GRAPH_VER", MAC_DINH["MSGR_GRAPH_VER"]).strip()

MODEL = os.getenv("BOT_MODEL", "flash").strip()    # mặc định trùng MAC_DINH["BOT_MODEL"]
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# BOT_MODEL: alias -> model id API. Có dấu '.' hoặc bắt đầu 'gemini' thì coi là id đầy đủ.
# Để ở đây (không ở brain) vì bảng giá bên dưới cũng phải quy alias về id thật mới tra được.
MODEL_ALIAS = {"flash": "gemini-3.5-flash", "pro": "gemini-2.5-pro",
               "lite": "gemini-2.5-flash-lite"}


def model_id(m: str | None = None) -> str:
    m = (m if m is not None else MODEL).strip()
    return MODEL_ALIAS.get(m, m)

# PSID của admin: nhận báo handoff (khách cần chuyên gia) + báo lỗi bot. VD: BOT_ADMIN_UIDS=123,456
ADMIN_UIDS = _split(os.getenv("BOT_ADMIN_UIDS", ""))

# Báo admin qua Lark: dán URL custom-bot-webhook của group Lark. Rỗng -> bỏ qua (chỉ báo FB).
# Bật "ký" ở webhook thì điền thêm secret; không bật thì để trống.
LARK_WEBHOOK_URL = os.getenv("LARK_WEBHOOK_URL", "").strip()
LARK_WEBHOOK_SECRET = os.getenv("LARK_WEBHOOK_SECRET", "").strip()

# URL công khai của bot (ngrok/domain) - để dựng link ảnh proxy FB tải được. VD https://abc.ngrok.app
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")

# Lark Base chứa ảnh sản phẩm (lấy từ link Base; đổi domain sang feishu.cn nếu dùng bản TQ).
LARK_DOMAIN = os.getenv("LARK_DOMAIN", MAC_DINH["LARK_DOMAIN"]).strip().rstrip("/")
LARK_APP_ID = os.getenv("LARK_APP_ID", "").strip()
LARK_APP_SECRET = os.getenv("LARK_APP_SECRET", "").strip()
LARK_BASE_APP_TOKEN = os.getenv("LARK_BASE_APP_TOKEN", "GtRhbgyWaaKeicstUkIlWKuegqg").strip()
LARK_TABLE_ID = os.getenv("LARK_TABLE_ID", "tbl12qihLVsupZw5").strip()

# Base CRM (RIÊNG base ảnh SP) - ghi lead khi bot thu đủ thông tin khách.
LARK_CRM_APP_TOKEN = os.getenv("LARK_CRM_APP_TOKEN", "GtRhbgyWaaKeicstUkIlWKuegqg").strip()
LARK_CRM_TABLE_ID = os.getenv("LARK_CRM_TABLE_ID", "tblkwMvDQkG4NB3m").strip()
LARK_PRODUCT_FIELD = os.getenv("LARK_PRODUCT_FIELD", MAC_DINH["LARK_PRODUCT_FIELD"]).strip()
LARK_IMAGE_FIELD = os.getenv("LARK_IMAGE_FIELD", MAC_DINH["LARK_IMAGE_FIELD"]).strip()
# Bảng SP mới tách ảnh thành 2 cột (Ảnh chính / Ảnh phụ) -> đọc gộp, giữ thứ tự khai báo.
LARK_IMAGE_FIELDS = [c.strip() for c in LARK_IMAGE_FIELD.split(",") if c.strip()]
PORT = int(os.getenv("PORT", "7900"))

# Trần đồng thời (giữ mặc định an toàn, chỉnh sau nếu cần).
# BOT_PER_PSID_RATE_S đã BỎ: khai báo + hiện ô nhập trên dashboard nhưng không đường xử lý tin
# nào đọc tới -> nút chết, chỉnh không đổi gì. Gom tin mỗi khách đã do debounce + _INFLIGHT lo.
MAX_CONCURRENT = int(os.getenv("BOT_MAX_CONCURRENT", MAC_DINH["BOT_MAX_CONCURRENT"]))

# Token bảo vệ trang admin/dashboard. Trống = trang admin TẮT.
DASH_TOKEN = os.getenv("BOT_DASH_TOKEN", "").strip()

# Giá token USD / 1 TRIỆU token - để dashboard tính tiền.
# Giá phải theo ĐÚNG model đang chạy: trước đây cắm cứng 1.5/9.0 (giá tầng Pro) trong khi bot
# chạy flash -> dashboard báo đắt gấp ~5 lần số tiền Google thật sự thu.
# Cột thứ 3 là giá token ĐÃ CACHE: system prompt ~12k token được gửi lại mỗi lượt nhưng Google
# tính rẻ hơn nhiều; coi nó bằng giá input thường là phồng thêm lần nữa.
# ĐỔI KHI GOOGLE ĐỔI GIÁ (hoặc ghi đè bằng GEMINI_PRICE_* trong .env).
_GIA = {                                    # model id -> (input, output, input đã cache)
    "gemini-2.5-flash": (0.30, 2.50, 0.075),
    "gemini-2.5-flash-lite": (0.10, 0.40, 0.025),
    "gemini-2.5-pro": (1.25, 10.0, 0.31),
    "gemini-3.5-flash": (0.30, 2.50, 0.075),
    "gemini-3.5-flash-lite": (0.10, 0.40, 0.025),
}
_GIA_LA = (0.30, 2.50, 0.075)               # model lạ -> tạm tính tầng flash, sửa bảng trên khi biết giá


def gia_model(m: str | None = None) -> tuple[float, float, float]:
    return _GIA.get(model_id(m), _GIA_LA)


_g = gia_model()
PRICE_IN_USD = float(os.getenv("GEMINI_PRICE_IN_USD", str(_g[0])))
PRICE_OUT_USD = float(os.getenv("GEMINI_PRICE_OUT_USD", str(_g[1])))
PRICE_CACHED_IN_USD = float(os.getenv("GEMINI_PRICE_CACHED_IN_USD", str(_g[2])))

# Follow-up: khách im sau khi bot trả lời, chưa chốt -> nhắc nhẹ 1 tin (trong cửa sổ 24h FB).
FOLLOWUP_ENABLED = os.getenv("BOT_FOLLOWUP_ENABLED", MAC_DINH["BOT_FOLLOWUP_ENABLED"]).strip() not in ("0", "", "false")
FOLLOWUP_AFTER_H = float(os.getenv("BOT_FOLLOWUP_AFTER_H", MAC_DINH["BOT_FOLLOWUP_AFTER_H"]))     # im bao lâu thì nhắc
FOLLOWUP_CHECK_MIN = int(os.getenv("BOT_FOLLOWUP_CHECK_MIN", MAC_DINH["BOT_FOLLOWUP_CHECK_MIN"]))  # chu kỳ quét

# Lưới an toàn tin RƠI: khách nhắn mà bot chưa trả lời quá X phút -> bot trả lời bù.
# Đây cũng là đường phục hồi khi Gemini lỗi: lượt đó bot im, vòng quét này nhắn bù.
# Ngưỡng phải > thời gian gom tin (debounce 4s) + thời gian bot nghĩ, không thì báo giả.
# 2 phút: đủ dài cho 1 lượt chậm nhất (retry 4 lần + đổi model dự phòng ~90s), đủ ngắn để
# khách không chờ lâu. Khách đang được xử lý vẫn được _INFLIGHT/_BUFFERS chặn nên không trùng.
MISSED_AFTER_MIN = float(os.getenv("BOT_MISSED_AFTER_MIN", MAC_DINH["BOT_MISSED_AFTER_MIN"]))
MISSED_CHECK_MIN = float(os.getenv("BOT_MISSED_CHECK_MIN", MAC_DINH["BOT_MISSED_CHECK_MIN"]))     # chu kỳ quét tin rơi
# Bot TỰ trả lời bù khách bị bỏ sót (đọc lại lịch sử nên khớp ngữ cảnh). Không áp dụng cho
# khách đã handoff (chuyên gia đang cầm) và tin quá 24h (FB chặn gửi).
MISSED_AUTOREPLY = os.getenv("BOT_MISSED_AUTOREPLY", MAC_DINH["BOT_MISSED_AUTOREPLY"]).strip() not in ("0", "", "false")

# Dọn stats: kho sự kiện phình mãi (~700 bản ghi/ngày, riêng vòng quét tin rơi đã 720/ngày).
# Giữ N ngày gần nhất, cũ hơn thì xoá. CHỈ đụng stats/events - lịch sử khách và CRM KHÔNG bao giờ
# bị dọn. Nới số ngày nếu cần biểu đồ chi phí dài hơn (xem STATS_KEEP_DAYS trong README).
STATS_KEEP_DAYS = float(os.getenv("BOT_STATS_KEEP_DAYS", MAC_DINH["BOT_STATS_KEEP_DAYS"]))
STATS_PRUNE_H = float(os.getenv("BOT_STATS_PRUNE_H", MAC_DINH["BOT_STATS_PRUNE_H"]))          # chu kỳ dọn

# Canh tunnel (ngrok/cloudflared) chết: tự ping PUBLIC_URL/webhook từ ngoài -> đứt thì báo Lark.
# Mặc định bật khi có PUBLIC_URL. Tắt: BOT_TUNNEL_WATCH=0.
TUNNEL_WATCH_ENABLED = os.getenv("BOT_TUNNEL_WATCH", MAC_DINH["BOT_TUNNEL_WATCH"]).strip() not in ("0", "", "false")
TUNNEL_CHECK_MIN = int(os.getenv("BOT_TUNNEL_CHECK_MIN", MAC_DINH["BOT_TUNNEL_CHECK_MIN"]))       # chu kỳ ping
TUNNEL_FAILS_TO_ALERT = int(os.getenv("BOT_TUNNEL_FAILS", "2"))      # số lần fail liên tiếp mới báo (tránh báo giả)

# Firebase Realtime DB: mirror conversations + stats lên cloud (backup/xem remote).
# Cả 2 trống -> tắt hẳn. FIREBASE_CRED = path service account json; DB_URL = link RTDB.
FIREBASE_CRED = os.getenv("FIREBASE_CRED", "").strip()
if FIREBASE_CRED and not Path(FIREBASE_CRED).exists():
    # .env dùng chung cho Docker (/app/key.json) và máy dev (Windows) -> đường dẫn tuyệt đối
    # kiểu Linux chết ở local. Không thấy file thì tìm cùng tên ngay cạnh mã nguồn.
    _fallback = ROOT / Path(FIREBASE_CRED).name
    if _fallback.exists():
        FIREBASE_CRED = str(_fallback)
FIREBASE_DB_URL = os.getenv("FIREBASE_DB_URL", "").strip()


def reload_env() -> None:
    """Đọc lại .env và cập nhật các giá trị chỉnh được từ dashboard (ăn ngay, không restart).

    MAX_CONCURRENT không nằm đây: semaphore tạo lúc import, đổi cần restart.

    Mọi khoá KHÁC mà dashboard cho sửa đều phải nạp lại ở đây: giao diện chỉ hiện "cần khởi động
    lại" cho ô có cờ restart, nên ô không có cờ mà không nạp lại là dashboard nói dối - người
    dùng đổi "nhắc sau 6 giờ", thấy báo lưu xong, mà bot vẫn chạy 23 giờ tới lúc restart."""
    global MODEL, ADMIN_UIDS, DASH_TOKEN, PRICE_IN_USD, PRICE_OUT_USD, PRICE_CACHED_IN_USD
    global FOLLOWUP_ENABLED, FOLLOWUP_AFTER_H, FOLLOWUP_CHECK_MIN
    global MISSED_AFTER_MIN, MISSED_CHECK_MIN, MISSED_AUTOREPLY
    global STATS_KEEP_DAYS, STATS_PRUNE_H, TUNNEL_WATCH_ENABLED, TUNNEL_CHECK_MIN
    load_dotenv(ROOT / ".env", override=True)
    MODEL = os.getenv("BOT_MODEL", MAC_DINH["BOT_MODEL"]).strip()
    ADMIN_UIDS = _split(os.getenv("BOT_ADMIN_UIDS", ""))
    DASH_TOKEN = os.getenv("BOT_DASH_TOKEN", "").strip()
    # Đổi model là đổi luôn bảng giá - không thì vừa hạ model rẻ đi mà dashboard vẫn báo giá cũ.
    g = gia_model()
    PRICE_IN_USD = float(os.getenv("GEMINI_PRICE_IN_USD", str(g[0])))
    PRICE_OUT_USD = float(os.getenv("GEMINI_PRICE_OUT_USD", str(g[1])))
    PRICE_CACHED_IN_USD = float(os.getenv("GEMINI_PRICE_CACHED_IN_USD", str(g[2])))
    FOLLOWUP_ENABLED = os.getenv("BOT_FOLLOWUP_ENABLED", MAC_DINH["BOT_FOLLOWUP_ENABLED"]).strip() not in ("0", "", "false")
    FOLLOWUP_AFTER_H = float(os.getenv("BOT_FOLLOWUP_AFTER_H", MAC_DINH["BOT_FOLLOWUP_AFTER_H"]))
    FOLLOWUP_CHECK_MIN = int(os.getenv("BOT_FOLLOWUP_CHECK_MIN", MAC_DINH["BOT_FOLLOWUP_CHECK_MIN"]))
    MISSED_AFTER_MIN = float(os.getenv("BOT_MISSED_AFTER_MIN", MAC_DINH["BOT_MISSED_AFTER_MIN"]))
    MISSED_CHECK_MIN = float(os.getenv("BOT_MISSED_CHECK_MIN", MAC_DINH["BOT_MISSED_CHECK_MIN"]))
    MISSED_AUTOREPLY = os.getenv("BOT_MISSED_AUTOREPLY", MAC_DINH["BOT_MISSED_AUTOREPLY"]).strip() not in ("0", "", "false")
    STATS_KEEP_DAYS = float(os.getenv("BOT_STATS_KEEP_DAYS", MAC_DINH["BOT_STATS_KEEP_DAYS"]))
    STATS_PRUNE_H = float(os.getenv("BOT_STATS_PRUNE_H", MAC_DINH["BOT_STATS_PRUNE_H"]))
    TUNNEL_WATCH_ENABLED = os.getenv("BOT_TUNNEL_WATCH", MAC_DINH["BOT_TUNNEL_WATCH"]).strip() not in ("0", "", "false")
    TUNNEL_CHECK_MIN = int(os.getenv("BOT_TUNNEL_CHECK_MIN", MAC_DINH["BOT_TUNNEL_CHECK_MIN"]))


_DEFAULT_PERSONA = ("Bạn là trợ lý chăm sóc khách hàng qua Messenger. Trả lời ngắn gọn, thân thiện, "
                    "xưng em. Chỉ trả lời dựa trên bảng sản phẩm được cung cấp. "
                    "Không bịa. Không dùng dấu gạch ngang dài.")

# Cache theo mtime: đọc đĩa 1 lần, chỉ đọc lại KHI file đổi -> sửa Personal.md/CSV vẫn ăn ngay,
# không đọc đĩa mỗi tin (mỗi câu trả lời gọi nhiều lần qua các vòng tool).
_FILE_CACHE: dict[str, tuple[float, str]] = {}


def _cached_read(path: Path, encoding: str, default: str) -> str:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return default
    key = str(path)
    hit = _FILE_CACHE.get(key)
    if hit is None or hit[0] != mtime:
        val = path.read_text(encoding=encoding).strip()
        _FILE_CACHE[key] = (mtime, val)
        return val
    return hit[1]


def persona() -> str:
    """System prompt = Document_ChatBot_Mess/Personal.md nếu có, không thì mặc định gọn."""
    return _cached_read(DOCS_DIR / "Personal.md", "utf-8", _DEFAULT_PERSONA)


def catalog_csv() -> str:
    """Toàn bộ bảng sản phẩm (CSV thô) để nhúng vào system prompt cho bot tra cứu."""
    return _cached_read(CATALOG_CSV, "utf-8-sig", "(chưa có bảng sản phẩm)")
