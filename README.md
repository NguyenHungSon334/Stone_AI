# Chatbot Messenger (standalone)

Bot Facebook Messenger chạy ĐỘC LẬP, tách khỏi Javis OS. Não = Anthropic Messages API.

## Chạy

```bash
cd chatbot-mess
python -m venv .venv && .venv\Scripts\activate    # Windows (Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env                             # rồi điền token (Linux: cp)
python app.py                                       # chạy port 7900
```

Cần `ANTHROPIC_API_KEY` trong `.env` (lấy ở console.anthropic.com).

Test não riêng (không cần webhook):
```bash
python brain.py "quán mở cửa mấy giờ?"
```

Test phần logic (chữ ký, parse):
```bash
python tests/test_messenger.py
```

## Kết nối Facebook

Bot chạy localhost, FB cần URL public. Dùng ngrok/cloudflared để expose:
```bash
ngrok http 7900
```
Vào Meta Developers > App > Messenger > Webhooks:
- Callback URL: `https://<domain-ngrok>/webhook/messenger`
- Verify token: khớp `MSGR_VERIFY_TOKEN` trong `.env`
- Subscribe field: `messages`

## Cấu trúc

| File | Việc |
|---|---|
| `app.py` | Webhook FastAPI (verify + nhận tin) |
| `messenger.py` | Giao thức FB: chữ ký, bóc tin, gửi trả, rate-limit |
| `brain.py` | Gọi Anthropic Messages API trả lời. Mỗi khách 1 phiên (nhớ hội thoại). Tool `find_by_price` |
| `config.py` | Đọc `.env` + persona + bảng sản phẩm |
| `Document_ChatBot_Mess/` | Kiến thức bot. `Personal.md` = persona; `Danh_Muc_San_Pham.csv` = bảng sản phẩm (nhúng vào prompt) |
| `bot_tools/find_by_price.py` | Hàm tra sản phẩm theo tầm giá (bot gọi qua function-calling) |
| `bot_tools/lark_image.py` | Lấy ảnh sản phẩm từ Lark Base theo mã (tool `get_product_image`) |

## Cách "não" hoạt động

`brain.py` gọi Anthropic Messages API. System prompt = persona + toàn bộ bảng sản phẩm CSV
(có prompt caching nên chỉ tính token 1 lần đầu). Khách hỏi theo tầm giá thì API gọi tool
`find_by_price` - hàm python chạy ngay trong process (KHÔNG mở shell). Mỗi khách (psid) giữ
lịch sử hội thoại riêng trong `.history.json` nên bot nhớ ngữ cảnh; người mới nhắn = phiên mới.

## Gửi ảnh sản phẩm (Lark Base)

Khách xin ảnh -> API gọi tool `get_product_image(mã)` -> tra Lark Base cột Ảnh (mã biến thể
`M01.2` tự lấy ảnh mã gốc `M01`). Ảnh Lark cần auth nên KHÔNG public: tool trả marker
`<<IMG:file_token>>`, `messenger.py` bóc marker và gửi ảnh qua URL proxy `PUBLIC_URL/img/{token}`;
endpoint `/img` (app.py) tự tải ảnh từ Lark rồi stream cho FB. Cần `.env`: `PUBLIC_URL`,
`LARK_APP_ID`, `LARK_APP_SECRET` (app đã được chia sẻ Base).

## Handoff + báo admin

Persona chèn marker ẩn `<<HANDOFF>>` khi chuyển chuyên gia; `messenger.py` bóc marker (khách
không thấy) và gửi thông tin về mọi `BOT_ADMIN_UIDS`. Mọi lỗi bot cũng tự báo về admin.

## Ghép vào Javis OS sau

Thay `config.py` (đọc `.env`) bằng đọc `settings.json` của Javis, và nối `handle_event`
vào supervisor/worker sẵn có. Logic bot không đổi.
