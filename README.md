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
- Subscribe field (đủ 4): `messages`, `messaging_postbacks`, `messaging_referrals`, `feed`
  (`feed` = bot trả lời comment dưới bài viết; thiếu nó thì comment KHÔNG tới bot và log
  không có dòng `[comment]` nào). `message_echoes` KHÔNG cần - code chỉ dùng để bỏ qua.

## Token Facebook

### HAI môi trường, HAI App khác nhau - nhìn nhầm là chẩn đoán sai (31/07/2026)

| | **PRODUCTION** (VPS) | **DEV** (máy lập trình) |
|---|---|---|
| Meta App | `2073944916564638` | `1674782393625789` (tên hiển thị: **"Test"**) |
| Page | `Hồn Đá - Lăng Mộ Đá Gia Tộc` (`122094807350018300`) | `604876159375634` |
| `.env` | `/home/admin/chatbot-mess/.env` trên VPS | `.env` trong repo |
| Khách thật | CÓ | không |

**App Secret và Page Token phải cùng một App** - nên hai môi trường KHÔNG dùng chung `.env`
được. `.env` trong repo là bản DEV: token của app "Test", trỏ page test.

**Cả hai đều bắn cảnh báo vào CÙNG một group Lark** (`LARK_WEBHOOK_URL` giống nhau). Chạy bot
local trong lúc VPS đang chạy = group nhận cảnh báo trùng, và cảnh báo "token chết" của máy dev
trông y hệt cảnh báo của production. **Trước khi hoảng vì một cảnh báo, xác định nó của môi
trường nào**: soi token trong `.env` tương ứng (lệnh ở mục dưới), `application` trả về `"Test"`
tức là máy dev, không phải Page thật.

Muốn chạy local mà không làm nhiễu group: để trống `LARK_WEBHOOK_URL` trong `.env` dev.

### Trạng thái token production (27/07/2026)

Page token vĩnh viễn (`expires_at: 0`), sinh từ **user token dài hạn**.

Scope đang có (8): `pages_messaging`, `pages_manage_engagement`, `pages_manage_metadata`,
`pages_read_engagement`, `pages_read_user_content`, `pages_show_list`, `business_management`,
`public_profile`.

### Soi token bất kỳ (đọc được lý do chết, không phải đoán)

```bash
# chạy tại thư mục chứa .env cần soi
T=$(grep -m1 '^MSGR_PAGE_TOKEN=' .env | cut -d= -f2-)
S=$(grep -m1 '^MSGR_APP_SECRET=' .env | cut -d= -f2-)
A=<app-id-cua-.env-do>       # prod 2073944916564638 | dev 1674782393625789
curl -s "https://graph.facebook.com/v21.0/debug_token?input_token=$T&access_token=$A|$S" \
  | python3 -m json.tool
```

Đọc kết quả:

| Dấu hiệu | Nghĩa | Xử lý |
|---|---|---|
| `is_valid: true`, `expires_at: 0` | Khoẻ, vĩnh viễn | không làm gì |
| `application` ra tên app **lạ** | Đang soi nhầm `.env` / nhầm môi trường | soi lại đúng file |
| `#190` subcode **458** + `scopes: []` | Tài khoản cấp token **đã gỡ quyền cho app**, hoặc mất vai trò trong app đang ở Development mode | cấp lại quyền rồi sinh token mới |
| `#190` subcode **463** / **466** | Token **hết hạn** (bản ngắn hạn 60 ngày) | sinh lại từ user token DÀI HẠN |
| `#190` subcode **460** | Chủ tài khoản **đổi mật khẩu** | sinh token mới |
| `#190` không có `data` | Soi bằng App Secret của app KHÁC | dùng đúng cặp app id + secret |

Đã dính subcode **458** ngày 31/07/2026 trên `.env` dev: token app "Test" còn nguyên chuỗi,
`expires_at: 0`, `data_access_expires_at` tới 29/10/2026, nhưng `scopes` rỗng - quyền bị rút,
token thành giấy lộn. **`expires_at: 0` KHÔNG có nghĩa là token sống.**

### Một cái bẫy đã dính thật, đọc trước khi đụng token

**Token và App Secret phải CÙNG một App.** Lấy token ở app A mà `.env` giữ secret của app B
thì `verify_signature` (`messenger.py`) fail-closed, mọi webhook trả **403** và bot câm - trong
khi lưới tin rơi vẫn trả lời bù qua Graph API nên nhìn như "lúc được lúc không".

### Đổi token: qua dashboard hoặc sửa tay

Dashboard **ghi được** secret: tab Cài đặt > ô `Page Access Token` > Lưu > Restart. Đường này
đi qua `admin.py` `_CONFIG_FIELDS` / `POST /api/config`, có validate và **để trống = giữ giá trị
cũ** (không thể vô tình xoá token bằng cách bỏ trống ô).

> Đừng nhầm với `_ENV_EDITABLE` cũng trong `admin.py`: đó là đường `POST /api/settings` đời
> đầu, chỉ nhận 4 key thường (`BOT_MODEL`, `BOT_ADMIN_UIDS`, `BOT_PER_PSID_RATE_S`,
> `BOT_MAX_CONCURRENT`) và KHÔNG đụng tới secret.

Sửa tay trên VPS khi không vào được dashboard:

```bash
gcloud compute ssh hon-da-vps --zone=us-central1-a
cd ~/chatbot-mess && sudo nano .env      # file thuộc user admin -> phải sudo
docker compose restart bot
```

### Lấy lại token vĩnh viễn (khi token chết)

Page token chỉ vĩnh viễn khi sinh từ **user token dài hạn**. Đổi thẳng từ page token ngắn hạn
chỉ ra 60 ngày.

> Dính subcode **458** (quyền bị rút) thì làm **bước 0** trước, không thì Explorer sinh ra
> token cũng chết y như cũ: developers.facebook.com > App > **App Roles** - tài khoản sinh token
> phải còn vai trò Admin/Developer/Tester (bắt buộc khi app ở **Development mode**). Rồi
> facebook.com/settings?tab=business_tools > gỡ app > cấp lại từ đầu để xoá sạch `declined`.

1. Graph API Explorer, chọn đúng App, để **User Token**, tick `pages_show_list`,
   `pages_messaging`, `pages_read_engagement`, `pages_manage_metadata`,
   `pages_manage_engagement`, `business_management` > Generate. Popup: chọn Page + **bật hết**
   nút gạt (Facebook nhớ cái đã từ chối - bị `declined` thì phải gỡ app ở
   `facebook.com/settings?tab=business_tools` rồi cấp lại từ đầu).
2. Trên VPS, đổi user token ngắn hạn -> dài hạn, rồi lấy page token từ đó:

```bash
cd ~/chatbot-mess
U='<user-token-tu-explorer>'; A=2073944916564638
S=$(sudo grep -m1 '^MSGR_APP_SECRET=' .env | cut -d= -f2-)
LU=$(curl -s "https://graph.facebook.com/v21.0/oauth/access_token?grant_type=fb_exchange_token&client_id=$A&client_secret=$S&fb_exchange_token=$U" | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')
PT=$(curl -s "https://graph.facebook.com/v21.0/me/accounts?access_token=$LU" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(next(p["access_token"] for p in d["data"] if p["id"]=="122094807350018300"))')
curl -s "https://graph.facebook.com/v21.0/debug_token?input_token=$PT&access_token=$A|$S" | python3 -m json.tool | grep expires_at
```

`expires_at` phải là **0**. Khác 0 thì bước đổi user token trượt, ĐỪNG ghi vào `.env`.

3. Ghi + restart (dùng đường dẫn tuyệt đối, `/home/admin/chatbot-mess`):

```bash
sudo cp .env .env.bak-$(date +%F-%H%M%S)
sudo sed -i '/^MSGR_PAGE_TOKEN=/d' .env
printf 'MSGR_PAGE_TOKEN=%s\n' "$PT" | sudo tee -a /home/admin/chatbot-mess/.env >/dev/null
docker compose restart bot
```

### "Vĩnh viễn" vẫn có thể chết

Không có hẹn giờ, nhưng vẫn huỷ khi: đổi mật khẩu Facebook, gỡ app khỏi Business
Integrations, mất quyền Admin Page, reset App Secret, hoặc Meta thu hồi. Đã dính một lần:
`OAuthException #190` subcode `460` - "session has been invalidated because the user changed
their password".

Vòng canh token (`messenger.run_token_check`, quét mỗi `BOT_FOLLOWUP_CHECK_MIN` phút) gọi
`debug_token` và báo Lark khi token chết hoặc còn dưới 7 ngày - không phải đợi khách nhắn mới
biết. **Giữ vòng này kể cả sau khi có token vĩnh viễn.**

**Nâng cấp còn treo - System User token.** Tài khoản máy thuộc Business, không có mật khẩu
nên không chết theo người nào. Cần Business Verification. Các bước: `business.facebook.com` >
Business Settings > Users > System Users > Add (role Admin) > Add Assets (Page: Manage Page;
App: Develop) > Generate New Token > **Token expiration: Never** + tick 5 quyền `pages_*`.
Đúng loại khi `debug_token` cho `user_id` TRỐNG và không còn `data_access_expires_at`.

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
| `returning.py` | Khách cũ quay lại: dựng lại lịch sử từ Graph API + báo Lark khi khách im >24h nhắn lại |
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

### Khi FB nuốt không trôi ảnh (`#100` subcode `2018047`)

Thứ tự gửi là **text trước, ảnh sau** (`messenger.py` `_process_inner`). Nên ảnh hỏng thì khách
**vẫn đọc được nội dung tư vấn, chỉ thiếu hình** - cảnh báo Lark cho tag `img upload` nói đúng
như vậy, đừng đọc thành "khách không nhận được gì".

`Upload attachment failure` hay nổ chập chờn dù file hoàn toàn hợp lệ. Hai lớp đỡ:

- `_send_images()` **giãn `_SEND_GAP_S` giữa các ảnh** - dội 4 attachment liên tiếp là FB nghẹn.
- `send_image_bytes()` **thử lại 1 lần**, lượt 2 ép về JPEG baseline. Lượt đầu hỏng chỉ ghi
  stderr, **không báo Lark** - báo ngay lượt đầu thì admin toàn nhận cảnh báo cho ca tự khỏi.

Còn cảnh báo sau cả hai lớp này = ảnh đó có vấn đề thật, mở thử record trong Lark Base.
Test: `tests/test_img_send.py`.

## Khách cũ quay lại (`returning.py`)

Bot lên SAU khi Page đã chạy -> khách từng nhắn trước đó không có log ở local lẫn Firebase,
bot tiếp như người lạ và hỏi lại những thứ khách đã nói. Mỗi lượt khách nhắn, trước khi gọi
`brain.answer`:

1. Chưa có log -> `GET me/conversations?user_id=<psid>` kéo 30 tin cũ, dựng lại thành log
   (`brain.seed_history`, CHỈ ghi khi log đang trống - bản dựng từ FB thiếu ảnh và marker nội
   bộ, đè lên log thật là mất dữ liệu).
2. Im >= 24h rồi nhắn lại -> báo Lark kèm mã lead + SĐT đọc từ `conversations/<psid>.crm.json`.
   File mốc `.quaylai` chặn báo lặp khi khách nhắn liền nhiều tin.

Chạy TRƯỚC `is_new_customer` - nạp được log thì khách này là khách CŨ. Cần scope
`pages_read_user_content`. Mọi lỗi đều nuốt: Graph hỏng thì bot vẫn trả lời, chỉ thiếu ngữ
cảnh cũ. Log: `[quaylai] nạp N tin cũ cho <psid>` / `[quaylai] báo admin`.

Lưu ý nghiệp vụ: tin **nhân viên nhắn tay** trước đây vào log với vai `assistant`, bot coi là
lời của chính mình. Liền mạch thì tốt, nhưng nếu nhân viên từng báo giá/hứa hẹn thì bot sẽ
tiếp nối cam kết đó. Thấy lệch thì hạ `_MAX_TIN` (mặc định 30).

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

> **Ô nhập KHÔNG ghi được token/secret.** `_ENV_EDITABLE` (`admin.py:36`) chỉ whitelist 4 key
> `BOT_MODEL`, `BOT_ADMIN_UIDS`, `BOT_PER_PSID_RATE_S`, `BOT_MAX_CONCURRENT`. Các ô bí mật vẫn
> hiện ra mời điền nhưng vòng ghi (`admin.py:202`) chỉ duyệt whitelist nên giá trị bị **vứt im
> lặng**, API vẫn trả `{"ok": true}`. Đây là bug thật đã tốn hàng giờ debug. Muốn đổi secret:
> sửa `.env` tay qua SSH (xem mục Token Facebook). Muốn sửa hẳn thì hoặc thêm key vào
> `_ENV_EDITABLE`, hoặc ẩn các ô bí mật khỏi giao diện - đừng để nguyên trạng đánh lừa.
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
