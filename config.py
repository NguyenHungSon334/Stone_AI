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


PAGE_TOKEN = os.getenv("MSGR_PAGE_TOKEN", "").strip()
VERIFY_TOKEN = os.getenv("MSGR_VERIFY_TOKEN", "").strip()
APP_SECRET = os.getenv("MSGR_APP_SECRET", "").strip()
GRAPH_VER = os.getenv("MSGR_GRAPH_VER", "v21.0").strip()

MODEL = os.getenv("BOT_MODEL", "flash").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

# PSID của admin: nhận báo handoff (khách cần chuyên gia) + báo lỗi bot. VD: BOT_ADMIN_UIDS=123,456
ADMIN_UIDS = _split(os.getenv("BOT_ADMIN_UIDS", ""))

# URL công khai của bot (ngrok/domain) - để dựng link ảnh proxy FB tải được. VD https://abc.ngrok.app
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")

# Lark Base chứa ảnh sản phẩm (lấy từ link Base; đổi domain sang feishu.cn nếu dùng bản TQ).
LARK_DOMAIN = os.getenv("LARK_DOMAIN", "https://open.larksuite.com").strip().rstrip("/")
LARK_APP_ID = os.getenv("LARK_APP_ID", "").strip()
LARK_APP_SECRET = os.getenv("LARK_APP_SECRET", "").strip()
LARK_BASE_APP_TOKEN = os.getenv("LARK_BASE_APP_TOKEN", "Uj9FbhUZWa6y5PsjsDyl0i1egsf").strip()
LARK_TABLE_ID = os.getenv("LARK_TABLE_ID", "tbl1grmn4hpj4Pih").strip()

# Base CRM (RIÊNG base ảnh SP) - ghi lead khi bot thu đủ thông tin khách.
LARK_CRM_APP_TOKEN = os.getenv("LARK_CRM_APP_TOKEN", "GtRhbgyWaaKeicstUkIlWKuegqg").strip()
LARK_CRM_TABLE_ID = os.getenv("LARK_CRM_TABLE_ID", "tblkwMvDQkG4NB3m").strip()
LARK_PRODUCT_FIELD = os.getenv("LARK_PRODUCT_FIELD", "Mã Sản Phẩm").strip()
LARK_IMAGE_FIELD = os.getenv("LARK_IMAGE_FIELD", "Ảnh").strip()
PORT = int(os.getenv("PORT", "7900"))

# Rate-limit + đồng thời (giữ mặc định an toàn, chỉnh sau nếu cần).
MAX_CONCURRENT = int(os.getenv("BOT_MAX_CONCURRENT", "4"))
PER_PSID_RATE_S = float(os.getenv("BOT_PER_PSID_RATE_S", "3"))

# Token bảo vệ trang admin/dashboard. Trống = trang admin TẮT.
DASH_TOKEN = os.getenv("BOT_DASH_TOKEN", "").strip()

# Giá token USD / 1 TRIỆU token - để dashboard tính tiền. Đổi khi Google đổi giá.
PRICE_IN_USD = float(os.getenv("GEMINI_PRICE_IN_USD", "1.5"))
PRICE_OUT_USD = float(os.getenv("GEMINI_PRICE_OUT_USD", "9.0"))

# Follow-up: khách im sau khi bot trả lời, chưa chốt -> nhắc nhẹ 1 tin (trong cửa sổ 24h FB).
FOLLOWUP_ENABLED = os.getenv("BOT_FOLLOWUP_ENABLED", "1").strip() not in ("0", "", "false")
FOLLOWUP_AFTER_H = float(os.getenv("BOT_FOLLOWUP_AFTER_H", "4"))     # im bao lâu thì nhắc
FOLLOWUP_CHECK_MIN = int(os.getenv("BOT_FOLLOWUP_CHECK_MIN", "15"))  # chu kỳ quét


def reload_env() -> None:
    """Đọc lại .env và cập nhật các giá trị chỉnh được từ dashboard (ăn ngay, không restart).

    MAX_CONCURRENT không nằm đây: semaphore tạo lúc import, đổi cần restart."""
    global MODEL, ADMIN_UIDS, PER_PSID_RATE_S, DASH_TOKEN, PRICE_IN_USD, PRICE_OUT_USD
    load_dotenv(ROOT / ".env", override=True)
    MODEL = os.getenv("BOT_MODEL", "flash").strip()
    ADMIN_UIDS = _split(os.getenv("BOT_ADMIN_UIDS", ""))
    PER_PSID_RATE_S = float(os.getenv("BOT_PER_PSID_RATE_S", "3"))
    DASH_TOKEN = os.getenv("BOT_DASH_TOKEN", "").strip()
    PRICE_IN_USD = float(os.getenv("GEMINI_PRICE_IN_USD", "1.5"))
    PRICE_OUT_USD = float(os.getenv("GEMINI_PRICE_OUT_USD", "9.0"))


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
