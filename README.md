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

## Token Facebook - VIỆC CẦN LÀM

**Hiện trạng (20/07/2026): đang dùng Page token sinh từ tài khoản cá nhân. Chấp nhận tạm vì
còn test, CHƯA có Business Manager.**

Soi bằng `debug_token` ra: `type: PAGE`, `user_id: 2274691409966415`, `expires_at: 0`,
`data_access_expires_at: 1792295595`.

Vấn đề: token Page loại này tuy mang tên Page nhưng vòng đời **buộc vào phiên đăng nhập của
người dùng đã cấp nó**. `expires_at: 0` chỉ nghĩa là không có hẹn giờ hết hạn, KHÔNG phải
không thể bị huỷ. Nó chết khi người đó đổi mật khẩu, đăng xuất mọi thiết bị, bật/reset 2FA,
gỡ app, mất quyền admin Page, hoặc Facebook tự huỷ phiên vì lý do bảo mật. Thêm mốc
`data_access_expires_at` ~90 ngày, tới đó phải xin quyền lại.

Đã dính thật một lần: `OAuthException #190` subcode `460` - "session has been invalidated
because the user changed their password". Bot im, khách nhắn không ai trả lời, chỉ lộ khi gửi
tin hỏng.

**Cách xử lý dứt điểm - System User token.** System User là tài khoản máy thuộc Business,
không có mật khẩu, không có phiên đăng nhập, nên không chết theo người nào cả. Đặt
`Token expiration: Never` thì không phải thay định kỳ, và không có đồng hồ 90 ngày.

Các bước (làm 1 lần, cần Business Manager; Page và App đều phải thuộc Business đó):

1. `business.facebook.com` > Business Settings > Users > System Users > Add, role **Admin**
2. Add Assets > Pages > chọn Page > bật **Manage Page** (full control)
3. Add Assets > Apps > chọn app Messenger > **Develop**
4. **Generate New Token** > chọn App > **Token expiration: Never** > tick quyền:
   `pages_messaging`, `pages_manage_metadata`, `pages_read_engagement`, `pages_show_list`,
   `pages_manage_engagement` (cần cho trả lời comment)
5. Copy token (chỉ hiện 1 lần) > dán vào ô **Page Token** ở `/admin` > Lưu > Restart

Xác nhận đã đúng loại: `user_id` TRỐNG, `expires_at: 0`, không còn `data_access_expires_at`.

Token System User không hết hạn nên ai cầm được là dùng vô thời hạn - lộ thì vào Business
Settings thu hồi và tạo cái mới. `.gitignore` đã chặn mọi biến thể `.env`.

Trong lúc chưa chuyển: vòng canh token (`messenger.run_token_check`, quét mỗi
`BOT_FOLLOWUP_CHECK_MIN` phút) gọi `debug_token`, token chết hoặc còn dưới 7 ngày là báo Lark
ngay - không phải đợi khách nhắn mới biết. Vòng này vẫn nên giữ cả sau khi đổi sang System
User, để bắt các ca thu hồi token / gỡ quyền / Facebook hạn chế app.

## Cấu trúc

| File | Việc |
|---|---|
| `app.py` | Webhook FastAPI + vòng lặp nền (follow-up, tin rơi, canh token; canh tunnel chạy riêng) |
| `messenger.py` | Giao thức FB: chữ ký, bóc tin/comment, gộp tin (debounce), gửi text/ảnh, rate-limit, handoff, ghi CRM |
| `brain.py` | Gọi Gemini trả lời. Lịch sử + tóm tắt từng khách. Tool `suggest_products` |
| `admin.py` | Router `/admin`: dashboard, xem khách, ô nhập cấu hình (ghi `.env`), test Lark, xoá data, restart |
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
| Tin rơi: hỏi thẳng FB xem khách nào nhắn mà chưa được trả lời | `BOT_MISSED_AFTER_MIN` / `BOT_MISSED_AUTOREPLY` | 10p, tự trả lời bù |
| Canh token: `debug_token`, chết hoặc còn <7 ngày → báo Lark | (không có biến, luôn bật) | theo `BOT_FOLLOWUP_CHECK_MIN` |
| Canh tunnel: ping `PUBLIC_URL` từ ngoài, đứt → báo Lark | `BOT_TUNNEL_WATCH` / `_CHECK_MIN` / `BOT_TUNNEL_FAILS` | bật khi có `PUBLIC_URL`, 3p, 2 lần fail |

Giữ `BOT_FOLLOWUP_AFTER_H` dưới 24 cho hợp cửa sổ nhắn tin của FB.

Ba vòng đầu dùng CHUNG một loop, mỗi vòng `try` riêng - một cái lỗi không nuốt cái còn lại.
Loop quét ngay khi khởi động (chờ 30s cho ổn định) chứ không ngủ trọn chu kỳ trước, để restart
giữa chừng không bỏ khách thêm 15 phút.

Trả lời bù đi ĐÚNG luồng tin bình thường (`handle_event` → gom tin → `brain.answer` → gửi) nên
đọc lại lịch sử, khớp ngữ cảnh, và mang theo **mốc giờ thật khách gửi** lấy từ FB - không đóng
dấu giờ xử lý, không thì bot tưởng khách vừa nhắn. Không đánh dấu "đã xử lý" lúc mới giao việc:
bot chết giữa chừng là khách im vĩnh viễn. Vòng sau tự bỏ qua nhờ lịch sử đã có lượt trả lời.

## Trang admin

`http://localhost:7900/admin?token=<BOT_DASH_TOKEN>`. **`BOT_DASH_TOKEN` trống = trang admin
tắt hẳn.** Xem tổng quan token/chi phí, danh sách khách, log từng khách, sửa cấu hình bằng ô
nhập, test webhook Lark, xoá toàn bộ data (local + Firebase, KHÔNG hoàn tác), restart.

Cấu hình chỉ sửa qua **ô nhập** (`/api/config`); editor `.env` thô đã bỏ - hai đường ghi cùng
một file mà đường kia không validate được, dán nhầm là hỏng file và mất luôn token dashboard.
Ô hiện giá trị **đang có hiệu lực**, biến chưa khai trong `.env` thì hiện mặc định của
`config.py` kèm ghi chú, không để trống gây tưởng chưa cấu hình. Ô bí mật luôn để trống, giá
trị che hiện ở dòng riêng bên dưới - đổ vào `value` thì bấm Lưu sẽ ghi đè chính chuỗi che lên
token thật.

`reload_env()` nạp nóng được: `BOT_MODEL`, `BOT_ADMIN_UIDS`, `BOT_PER_PSID_RATE_S`,
`BOT_DASH_TOKEN`, 2 biến giá. `BOT_MAX_CONCURRENT` đổi phải **restart** (semaphore tạo lúc import).

Giá tính tiền dashboard lấy từ `GEMINI_PRICE_IN_USD` / `GEMINI_PRICE_OUT_USD` (USD / 1 triệu
token, mặc định 1.5 / 9.0). Google đổi giá thì phải tự sửa, không thì số tiền hiển thị sai.

## Ghép vào Javis OS sau

Thay `config.py` (đọc `.env`) bằng đọc `settings.json` của Javis, và nối `handle_event`
vào supervisor/worker sẵn có. Logic bot không đổi.
