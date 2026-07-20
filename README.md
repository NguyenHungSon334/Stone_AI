# Chatbot Messenger (standalone)

Bot Facebook Messenger bán đá mỹ nghệ, chạy ĐỘC LẬP (tách khỏi Javis OS).
Não = **Google Gemini API** (SDK `google-genai`).

## Chạy

```bash
cd chatbot-mess
python -m venv .venv && .venv\Scripts\activate    # Windows (Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env                             # rồi điền token (Linux: cp)
python app.py                                       # chạy port 7900
```

Bắt buộc trong `.env`: `GEMINI_API_KEY` (lấy ở aistudio.google.com/apikey),
`MSGR_PAGE_TOKEN`, `MSGR_VERIFY_TOKEN`, `MSGR_APP_SECRET`.

Model chọn qua `BOT_MODEL`: alias `flash` (mặc định, = gemini-3.5-flash), `pro`
(gemini-2.5-pro), `lite` (gemini-2.5-flash-lite), hoặc điền thẳng model id.

Test logic (chữ ký, parse tin):
```bash
python tests/test_messenger.py
```
Loadtest nhiều chat đồng thời qua webhook: `python tests/loadtest.py`.

## Kết nối Facebook

Bot cần URL public. Dev: ngrok/cloudflared (`ngrok http 7900`). Production: xem `DEPLOY.md`
(Docker + Caddy + DuckDNS, Caddy tự lấy SSL cho `DOMAIN`).

Meta Developers > App > Messenger > Webhooks:
- Callback URL: `https://<domain>/webhook/messenger`
- Verify token: khớp `MSGR_VERIFY_TOKEN`
- Subscribe field: `messages`, `feed` (feed = bot trả lời comment dưới bài viết)

## Cấu trúc

| File | Việc |
|---|---|
| `app.py` | Webhook FastAPI + 2 vòng lặp nền (follow-up, canh tunnel) |
| `messenger.py` | Giao thức FB: chữ ký, bóc tin/comment, gộp tin (debounce), gửi text/ảnh, rate-limit, handoff, ghi CRM |
| `brain.py` | Gọi Gemini trả lời. Lịch sử + tóm tắt từng khách. Tool `suggest_products` |
| `admin.py` | Router `/admin`: dashboard, xem khách, sửa `.env`, test Lark, xoá data, restart |
| `dashboard.html` | Giao diện trang admin |
| `config.py` | Đọc `.env` + persona + bảng sản phẩm (cache theo mtime) |
| `stats.py` | Đếm token, chi phí, sự kiện |
| `fb.py` | Mirror hội thoại + stats lên Firebase Realtime DB |
| `util.py` | psid an toàn, ghi JSON atomic |
| `Document_ChatBot_Mess/` | Kiến thức bot. `Personal.md` = persona; `Danh_Muc_San_Pham.csv` = bảng sản phẩm |
| `bot_tools/find_by_price.py` | Tra sản phẩm theo giá / theo mã (backend của tool `suggest_products`) |
| `bot_tools/lark_image.py` | Lấy ảnh sản phẩm từ Lark Base theo mã |
| `bot_tools/lark_crm.py` | Ghi lead vào Lark Base CRM |

## Cách "não" hoạt động

`brain.py` gọi Gemini. System instruction = persona + toàn bộ bảng sản phẩm CSV (~30k token).
Phần này TĨNH nên được đẩy lên **explicit cache của Gemini (TTL 1h)**; mỗi request chỉ tham
chiếu handle thay vì nhồi lại. Khoá cache = model + mtime `Personal.md` + mtime CSV, nên sửa
persona hoặc CSV là cache tự dựng lại. Cache lỗi/hết hạn thì fallback nhồi thẳng, không gãy.
Ghi chú thời gian thực nằm ngoài cache (nhét vào contents mỗi lượt) để prefix cache không đổi.

Bot có **1 tool**: `suggest_products` - lọc theo tầm giá (`max`/`min`/`stone`/`category`) hoặc
lấy đúng mã (`product_ids`). Hàm python chạy trong process, KHÔNG mở shell.

Lịch sử mỗi khách (psid) lưu ở `conversations/`, **mirror lên Firebase RTDB làm nguồn chính** -
mất cache local thì tự kéo lại từ Firebase. Hội thoại dài được tóm tắt nền để khỏi phình prompt.

Khách gửi ảnh: bot đọc được (vision), ảnh nhét vào lượt hỏi.

## Gửi ảnh sản phẩm (Lark Base)

Tin nhắn nhắc tới mã sản phẩm **lần đầu** → hệ thống tự chèn marker `<<IMG:file_token>>`
(regex mã dựng từ CSV; mã biến thể `M01.2` lấy ảnh mã gốc `M01`). `messenger.py` bóc marker,
tải bytes từ Lark rồi **upload thẳng lên FB (multipart)** - không qua URL proxy. Trần
`_MAX_NEW_IMAGES = 4` ảnh/tin.

Cần `.env`: `LARK_APP_ID`, `LARK_APP_SECRET` (app đã được chia sẻ Base), quyền `bitable:read`
+ `drive:read`. `LARK_BASE_APP_TOKEN` / `LARK_TABLE_ID` có mặc định hardcode trong `config.py`
- dùng Base khác thì phải override trong `.env`.

## Handoff, CRM, báo admin

- Persona chèn marker ẩn `<<HANDOFF:lý do>>` khi cần chuyển chuyên gia; `messenger.py` bóc
  marker (khách không thấy) và báo về mọi `BOT_ADMIN_UIDS`. Khách để lại số điện thoại cũng
  kích hoạt handoff.
- Bot thu đủ thông tin khách → ghi lead vào Lark Base CRM riêng (`LARK_CRM_APP_TOKEN`,
  `LARK_CRM_TABLE_ID`; cũng có mặc định hardcode).
- Mọi lỗi bot tự báo admin. Có `LARK_WEBHOOK_URL` thì báo thêm vào group Lark.

## Chạy nền tự động

| Việc | Biến | Mặc định |
|---|---|---|
| Follow-up: khách im chưa chốt → nhắn nhẹ 1 tin | `BOT_FOLLOWUP_ENABLED` / `_AFTER_H` / `_CHECK_MIN` | bật, 4h, quét 15p |
| Canh tunnel: ping `PUBLIC_URL` từ ngoài, đứt → báo Lark | `BOT_TUNNEL_WATCH` / `_CHECK_MIN` / `BOT_TUNNEL_FAILS` | bật khi có `PUBLIC_URL`, 3p, 2 lần fail |

Giữ `BOT_FOLLOWUP_AFTER_H` dưới 24 cho hợp cửa sổ nhắn tin của FB.

## Trang admin

`http://localhost:7900/admin?token=<BOT_DASH_TOKEN>`. **`BOT_DASH_TOKEN` trống = trang admin
tắt hẳn.** Xem tổng quan token/chi phí, danh sách khách, log từng khách, sửa `.env` ngay trên
web, test webhook Lark, xoá toàn bộ data (local + Firebase, KHÔNG hoàn tác), restart.

`reload_env()` nạp nóng được: `BOT_MODEL`, `BOT_ADMIN_UIDS`, `BOT_PER_PSID_RATE_S`,
`BOT_DASH_TOKEN`, 2 biến giá. `BOT_MAX_CONCURRENT` đổi phải **restart** (semaphore tạo lúc import).

Giá tính tiền dashboard lấy từ `GEMINI_PRICE_IN_USD` / `GEMINI_PRICE_OUT_USD` (USD / 1 triệu
token, mặc định 1.5 / 9.0). Google đổi giá thì phải tự sửa, không thì số tiền hiển thị sai.

## Ghép vào Javis OS sau

Thay `config.py` (đọc `.env`) bằng đọc `settings.json` của Javis, và nối `handle_event`
vào supervisor/worker sẵn có. Logic bot không đổi.
