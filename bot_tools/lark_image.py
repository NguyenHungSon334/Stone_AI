"""
Lấy ảnh sản phẩm từ Lark Base (Bitable), cột Ảnh, theo Mã Sản Phẩm.

- Base quốc tế larksuite.com -> API domain open.larksuite.com (đổi qua LARK_DOMAIN nếu là feishu.cn).
- Biến thể mã (M01.2, LD03.1) dùng CHUNG ảnh mã gốc -> cắt phần sau dấu chấm khi tra.
- Ảnh Lark cần auth, không public. get_image_tokens() trả file_token; app.py có endpoint
  /img/{token} proxy tải bằng download_media() rồi stream cho Messenger.

Cần trong .env: LARK_APP_ID, LARK_APP_SECRET (app nội bộ có quyền bitable:read + drive:read,
đã chia sẻ Base cho app). LARK_BASE_APP_TOKEN, LARK_TABLE_ID đã set sẵn từ link.
"""
import json
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # cho phép chạy trực tiếp
import config

_TOKEN = {"val": "", "exp": 0.0}
_LOCK = threading.Lock()

# Cache bytes ảnh: warm trước khi gửi tin -> FB fetch qua /img là có ngay, khách không chờ.
_MEDIA_CACHE: dict[str, tuple[float, bytes, str]] = {}   # token -> (ts, bytes, ctype)
_MEDIA_CACHE_LOCK = threading.Lock()
_MEDIA_TTL_S = 3600
_MEDIA_MAX = 64


def base_code(product_id: str) -> str:
    """M01.2 -> M01, LD03 -> LD03. Biến thể (.1/.2/.3) lấy ảnh của mã gốc."""
    return str(product_id).strip().upper().split(".")[0]


def _tenant_token() -> str:
    """tenant_access_token, cache tới gần hết hạn."""
    with _LOCK:
        if _TOKEN["val"] and time.time() < _TOKEN["exp"]:
            return _TOKEN["val"]
        r = httpx.post(f"{config.LARK_DOMAIN}/open-apis/auth/v3/tenant_access_token/internal",
                       json={"app_id": config.LARK_APP_ID, "app_secret": config.LARK_APP_SECRET},
                       timeout=15.0)
        d = r.json()
        if d.get("code") != 0:
            raise RuntimeError(f"Lark auth lỗi: {d.get('code')} {d.get('msg')}")
        _TOKEN["val"] = d["tenant_access_token"]
        _TOKEN["exp"] = time.time() + d.get("expire", 7200) - 120
        return _TOKEN["val"]


def get_image_tokens(product_id: str) -> list[str]:
    """file_token các ảnh của 1 mã (đã cắt biến thể). Rỗng nếu không thấy record/ảnh."""
    code = base_code(product_id)
    tok = _tenant_token()
    url = (f"{config.LARK_DOMAIN}/open-apis/bitable/v1/apps/{config.LARK_BASE_APP_TOKEN}"
           f"/tables/{config.LARK_TABLE_ID}/records/search")
    body = {
        "field_names": [config.LARK_PRODUCT_FIELD, config.LARK_IMAGE_FIELD],
        "filter": {"conjunction": "and", "conditions": [
            {"field_name": config.LARK_PRODUCT_FIELD, "operator": "is", "value": [code]}]},
        "automatic_fields": False,
    }
    r = httpx.post(url, headers={"Authorization": f"Bearer {tok}"}, json=body, timeout=20.0)
    d = r.json()
    if d.get("code") != 0:
        raise RuntimeError(f"Lark search lỗi: {d.get('code')} {d.get('msg')}")
    out: list[str] = []
    for item in (d.get("data", {}).get("items") or []):
        cell = (item.get("fields") or {}).get(config.LARK_IMAGE_FIELD)
        for att in (cell or []):
            ft = att.get("file_token")
            if ft:
                out.append(ft)
    return out


def download_media(file_token: str) -> tuple[bytes, str]:
    """Tải bytes 1 ảnh Lark (dùng cho endpoint proxy /img). Trả (bytes, content_type).

    Có cache RAM (TTL 1h): messenger warm trước khi gửi tin -> lần FB fetch là trúng cache.
    Ảnh trong Bitable BẮT BUỘC param extra=bitablePerm, không có sẽ 400.
    """
    now = time.time()
    with _MEDIA_CACHE_LOCK:
        hit = _MEDIA_CACHE.get(file_token)
        if hit and now - hit[0] < _MEDIA_TTL_S:
            return (hit[1], hit[2])
    tok = _tenant_token()
    extra = urllib.parse.quote(json.dumps({"bitablePerm": {"tableId": config.LARK_TABLE_ID, "rev": 1}}))
    url = f"{config.LARK_DOMAIN}/open-apis/drive/v1/medias/{file_token}/download?extra={extra}"
    r = httpx.get(url, headers={"Authorization": f"Bearer {tok}"}, timeout=30.0)
    r.raise_for_status()
    data, ctype = r.content, r.headers.get("content-type", "image/jpeg")
    with _MEDIA_CACHE_LOCK:
        if len(_MEDIA_CACHE) >= _MEDIA_MAX:            # đầy -> bỏ entry cũ nhất
            oldest = min(_MEDIA_CACHE, key=lambda k: _MEDIA_CACHE[k][0])
            _MEDIA_CACHE.pop(oldest, None)
        _MEDIA_CACHE[file_token] = (now, data, ctype)
    return (data, ctype)


if __name__ == "__main__":
    assert base_code("M01.2") == "M01"
    assert base_code("ld03") == "LD03"
    assert base_code(" M23 ") == "M23"
    print("lark_image selftest OK (chỉ logic cắt mã; gọi Lark cần .env + creds)")
