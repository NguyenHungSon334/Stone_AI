import requests
import webbrowser
import threading
import time
from flask import Flask, Response, render_template_string, request
import urllib.parse

APP_ID            = "cli_a94347b5cfb8deea"
APP_SECRET        = "K7CkUsBCqATkYEaikp60UdMS1fMjxOjS"  # ⚠️ Thay bằng app_secret mới

BITABLE_APP_TOKEN = "Uj9FbhUZWa6y5PsjsDyl0i1egsf"
BITABLE_TABLE_ID  = "tbl1grmn4hpj4Pih"

app = Flask(__name__)
LARK_TOKEN = None  # Token dùng chung trong session


def get_lark_token():
    url = "https://open.larksuite.com/open-apis/auth/v3/tenant_access_token/internal"
    response = requests.post(url, json={"app_id": APP_ID, "app_secret": APP_SECRET})
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise Exception(f"Lấy token thất bại: {data.get('msg')}")
    return data["tenant_access_token"]


def get_media(ma_sp: str, token: str) -> dict:
    url = f"https://open.larksuite.com/open-apis/bitable/v1/apps/{BITABLE_APP_TOKEN}/tables/{BITABLE_TABLE_ID}/records"
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params={
            "filter": f'CurrentValue.[Mã Sản Phẩm]="{ma_sp.strip().upper()}"',
            "page_size": 1
        }
    )
    data = response.json()
    if data.get("code") != 0:
        raise Exception(f"Lỗi: {data.get('msg')}")

    items = data.get("data", {}).get("items", [])
    if not items:
        return {"error": f"Không tìm thấy '{ma_sp}'"}

    fields = items[0].get("fields", {})

    def get_files(field_name):
        files = fields.get(field_name, []) or []
        result = []
        for f in files:
            if not f.get("file_token"):
                continue
            result.append({
                "token": f["file_token"],
                "name": f.get("name", ""),
                "type": f.get("type", ""),
                # url already has extra={"bitablePerm":...} baked in — use directly
                "url": f.get("url", ""),
            })
        return result

    return {
        "ma_sp"      : fields.get("Mã Sản Phẩm", ma_sp),
        "ten_sp"     : fields.get("Tên sản phẩm", ""),
        "anh"        : get_files("Ảnh"),
        "anh_bao_gia": get_files("Ảnh báo giá(1 ảnh rõ sản phẩm)"),
        "video"      : get_files("Video"),
    }


# ─── Proxy route: trình duyệt gọi /proxy?t=FILE_TOKEN → server stream về từ Lark ───
@app.route("/proxy")
def proxy():
    # Accept either full encoded url (?u=...) or just file_token (?t=...)
    lark_url = request.args.get("u")
    file_token = request.args.get("t")

    if not LARK_TOKEN:
        return "no token", 400
    if not lark_url and not file_token:
        return "missing u or t", 400

    if not lark_url:
        lark_url = f"https://open.larksuite.com/open-apis/drive/v1/medias/{file_token}/download"

    r = requests.get(lark_url, headers={"Authorization": f"Bearer {LARK_TOKEN}"}, stream=True)
    content_type = r.headers.get("Content-Type", "application/octet-stream")
    print(f"[proxy] status={r.status_code} ct={content_type} url={lark_url[:80]}")
    if "json" in content_type or "text" in content_type:
        print(f"[proxy] NON-IMAGE: {r.content[:200]}")
    return Response(r.iter_content(chunk_size=8192), content_type=content_type)


@app.route("/")
def index():
    ma_sp = request.args.get("ma", "LD01")
    try:
        media = get_media(ma_sp, LARK_TOKEN)
    except Exception as e:
        media = {"error": str(e)}

    if "error" in media:
        return f"<h2>❌ {media['error']}</h2>"

    def img_tag(f):
        src = f"/proxy?u={urllib.parse.quote(f['url'])}" if f.get("url") else f"/proxy?t={urllib.parse.quote(f['token'])}"
        return f'<div class="item"><img src="{src}" loading="lazy"><p class="cap">{f["name"]}</p></div>'

    def video_tag(f):
        src = f"/proxy?u={urllib.parse.quote(f['url'])}" if f.get("url") else f"/proxy?t={urllib.parse.quote(f['token'])}"
        return f'<div class="item video-item"><video controls><source src="{src}"></video><p class="cap">{f["name"]}</p></div>'

    anh_html    = "".join(img_tag(f) for f in media["anh"]) or '<p class="empty">Không có</p>'
    baogia_html = "".join(img_tag(f) for f in media["anh_bao_gia"]) or '<p class="empty">Không có</p>'
    video_html  = "".join(video_tag(f) for f in media["video"]) or '<p class="empty">Không có</p>'

    return render_template_string("""<!DOCTYPE html>
<html lang="vi"><head><meta charset="UTF-8">
<title>{{ ma }} — {{ ten }}</title>
<style>
* { box-sizing: border-box; }
body { font-family: sans-serif; background: #f0f0f0; margin: 0; padding: 24px; }
h1   { font-size: 22px; margin: 0 0 4px; }
.sub { font-size: 15px; color: #666; margin-bottom: 28px; }
h3   { font-size: 14px; font-weight: 600; color: #333; margin: 28px 0 12px;
       border-bottom: 1px solid #ddd; padding-bottom: 6px; }
.grid { display: flex; flex-wrap: wrap; gap: 12px; }
.item { background: #fff; border-radius: 10px; padding: 8px;
        box-shadow: 0 1px 4px rgba(0,0,0,.1); }
.item img  { width: 220px; height: 180px; object-fit: cover;
             border-radius: 6px; display: block; }
.video-item { width: 440px; }
.video-item video { width: 100%; border-radius: 6px; display: block; }
.cap   { font-size: 11px; color: #999; margin: 6px 0 0; text-align: center; }
.empty { color: #aaa; font-size: 13px; padding: 8px 0; }
form   { margin-bottom: 20px; display: flex; gap: 8px; }
input  { padding: 8px 12px; border: 1px solid #ccc; border-radius: 6px; font-size: 14px; }
button { padding: 8px 16px; background: #333; color: #fff; border: none;
         border-radius: 6px; cursor: pointer; font-size: 14px; }
</style></head><body>
<h1>📦 {{ ma }}</h1>
<div class="sub">{{ ten }}</div>

<form method="get">
  <input name="ma" value="{{ ma }}" placeholder="Nhập mã SP...">
  <button type="submit">Xem</button>
</form>

<h3>🖼 Ảnh ({{ n_anh }} file)</h3>
<div class="grid">{{ anh_html | safe }}</div>

<h3>📋 Ảnh báo giá ({{ n_baogia }} file)</h3>
<div class="grid">{{ baogia_html | safe }}</div>

<h3>🎬 Video ({{ n_video }} file)</h3>
<div class="grid">{{ video_html | safe }}</div>
</body></html>""",
        ma=media["ma_sp"], ten=media["ten_sp"],
        n_anh=len(media["anh"]), n_baogia=len(media["anh_bao_gia"]), n_video=len(media["video"]),
        anh_html=anh_html, baogia_html=baogia_html, video_html=video_html
    )


app.add_url_rule("/proxy", endpoint="proxy", view_func=proxy)

# =====================
# CHẠY
# =====================
MA_SP = "LD01"  # ← Thay mã sản phẩm tại đây
PORT  = 5500

if __name__ == "__main__":
    print("[1] Lấy token...")
    LARK_TOKEN = get_lark_token()
    print(f"    OK")

    print(f"[2] Mở trình duyệt: http://localhost:{PORT}?ma={MA_SP}")
    threading.Timer(1, lambda: webbrowser.open(f"http://localhost:{PORT}?ma={MA_SP}")).start()

    print(f"[3] Server đang chạy... Ctrl+C để thoát\n")
    app.run(port=PORT, debug=False)