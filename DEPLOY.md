# Deploy bot lên VPS GCP

Kiến trúc: build image -> VPS `docker compose up` (bot + Caddy tự SSL). Webhook FB -> `https://<domain>` -> Caddy -> bot.

State (lịch sử khách, stats, CRM meta, persona, .env) nằm trên VPS ở `data/` + `.env` -> update image KHÔNG mất dữ liệu.

## Hệ thống đang chạy (27/07/2026)

| Hạng mục | Giá trị |
|---|---|
| GCP project | `gen-lang-client-0667910564` |
| Instance | `hon-da-vps`, zone `us-central1-a`, **e2-medium** (2 vCPU, 3.8GB RAM) |
| IP tĩnh | `35.232.204.224` (đã reserve, tên `staticip-honda`) |
| Domain | `hondabotchat.duckdns.org` |
| Thư mục trên VPS | **`/home/admin/chatbot-mess`** (file thuộc user `admin` -> ghi phải `sudo`) |
| Meta App | `2073944916564638` |
| Page | `Hồn Đá - Lăng Mộ Đá Gia Tộc` (`122094807350018300`) |

VPS chạy **2 dự án** cùng lúc, mỗi cái compose riêng:

| Container | Thư mục | Trần RAM | Đỉnh đã chạm |
|---|---|---|---|
| `chatbot-mess-bot-1` | `/home/admin/chatbot-mess` | 1 GB | ~232 MB |
| `chatbot-mess-caddy-1` | (cùng compose trên) | không đặt | ~35 MB |
| `honda-tool-honda_app-1` | `/home/admin/honda-tool` | 2 GB | ~450 MB |

Máy 3.8GB, tổng trần 3GB - còn ~800MB cho OS khi cả hai chạm trần. **Chưa có swap.**

---

## 1. Tạo VPS (GCP Compute Engine)

1. https://console.cloud.google.com -> tạo Project (ghi lại PROJECT_ID).
2. Compute Engine -> Create Instance:
   - Region: **us-central1**
   - Machine: **e2-medium** (e2-micro free tier KHÔNG đủ nếu chạy nhiều app; bản đang dùng là e2-medium, có tính phí)
   - Boot disk: Debian 12, 30GB standard
   - Firewall: tick **Allow HTTP** + **Allow HTTPS**
3. Tạo xong **reserve IP tĩnh** (VPC network > IP addresses) - IP ephemeral đổi sau reboot là chết DNS.

## 2. DuckDNS (domain miễn phí)

1. https://www.duckdns.org -> đăng nhập (Google/GitHub).
2. Tạo subdomain, vd `hondabotchat` -> `hondabotchat.duckdns.org`.
3. Ô "current ip" điền **External IP của VPS** -> Update.
4. (Nếu IP VPS đổi: chạy lại update, hoặc đặt IP tĩnh trong GCP.)

> Hiện tại: domain `hondabotchat.duckdns.org` -> `35.232.204.224` (IP tĩnh đã reserve,
> tên `staticip-honda`). DuckDNS XOÁ subdomain không được update trong ~30 ngày - đã mất
> 1 lần khiến webhook FB chết. Nên có cron ping định kỳ trên VPS:
> `*/30 * * * * curl -fsS "https://www.duckdns.org/update?domains=hondabotchat&token=<TOKEN>&ip=" >/dev/null`

## 3. Cài Docker trên VPS

SSH vào VPS (nút SSH trong console GCP), chạy:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
# thoát SSH rồi vào lại cho nhóm docker có hiệu lực
```

## 4. Đưa file cấu hình lên VPS

Trên VPS:
```bash
mkdir -p ~/chatbot-mess/data && cd ~/chatbot-mess
```

Copy 3 file từ repo lên `~/chatbot-mess/` (dùng scp hoặc dán tay):
- `docker-compose.yml`
- `Caddyfile`
- `.env`  (copy từ `.env.example`, ĐIỀN đủ token thật + `DOMAIN=hondabotchat.duckdns.org`)

## 5. Deploy code (cách ĐANG dùng: build thẳng trên VPS)

Image chạy thật là `hondastone-bot:latest`, build tại VPS. **Sửa code Python phải rebuild** -
khác `Personal.md` và `.env` (bind-mount, sửa là ăn ngay sau restart).

Từ máy local, đóng gói mã nguồn rồi build trên VPS:

```bash
# 1. đóng gói (loại .env, data/, conversations/, key Firebase, Caddyfile, docker-compose.yml)
tar czf /tmp/src.tgz --exclude='__pycache__' *.py bot_tools Document_ChatBot_Mess \
    dashboard.html Dockerfile entrypoint.sh requirements.txt database.rules.json .dockerignore

# 2. đẩy lên
gcloud compute scp /tmp/src.tgz hon-da-vps:/tmp/src.tgz --zone=us-central1-a

# 3. giải nén + build + đổi container
gcloud compute ssh hon-da-vps --zone=us-central1-a --command="cd /home/admin/chatbot-mess && \
  sudo tar czf ~/backup-src-\$(date +%F-%H%M%S).tgz *.py && \
  sudo tar xzf /tmp/src.tgz -C . && rm -f /tmp/src.tgz && \
  docker compose build bot && docker compose up -d bot"
```

**Đừng đè `Caddyfile` và `docker-compose.yml`** - bản trên VPS có thể đã chỉnh riêng (trần RAM,
route `/privacy`). Downtime ~2-3 phút lúc đổi container; lưới tin rơi quét mỗi 2 phút sẽ nhắn
bù cho khách nhắn trong khoảng đó.

Kiểm mã nguồn trên VPS có đủ không trước khi build (đã từng thiếu `Dockerfile`, `admin.py`,
`alerts.py`, `bot_tools/`, `dashboard.html` -> build hỏng):
```bash
ls *.py Dockerfile entrypoint.sh requirements.txt bot_tools/ dashboard.html
```

### Cách thay thế: Artifact Registry (`deploy.sh`)

Build ở máy local rồi push registry. Sạch hơn nhưng cần Docker Desktop chạy + auth:
```bash
gcloud auth configure-docker us-central1-docker.pkg.dev
export GCP_PROJECT=gen-lang-client-0667910564 GCP_REGION=us-central1
export VPS_USER=admin VPS_HOST=hondabotchat.duckdns.org
./deploy.sh          # chạy test -> build -> push -> scp docs -> ssh up -d
```

## 6. Webhook FB

Meta app -> Messenger -> Settings.

**Access Tokens**: Add or Remove Pages -> chọn Page -> Generate Token (xem README mục
"Token Facebook" để lấy loại VĨNH VIỄN, đừng dùng token Explorer 1-2 giờ).

**Webhooks** -> Callback URL:
```
https://hondabotchat.duckdns.org/webhook/messenger
```
Verify token = `MSGR_VERIFY_TOKEN` trong `.env`. Verify + Save.

Add Subscriptions cho Page, tick đủ **4 field**:
```
messages  messaging_postbacks  messaging_referrals  feed
```
Thiếu `feed` thì comment KHÔNG tới bot (log không có dòng `[comment]` nào). Đăng ký `feed` cần
token có `pages_manage_metadata`; kiểm bằng:
```bash
curl -s "https://graph.facebook.com/v21.0/122094807350018300/subscribed_apps?fields=subscribed_fields&access_token=$T"
```

Trong `.env` trên VPS đổi `PUBLIC_URL=https://hondabotchat.duckdns.org` (để gửi ảnh), rồi bấm Restart trên dashboard hoặc `docker compose restart bot`.

## 8. Kiểm tra

```bash
curl https://hondabotchat.duckdns.org/healthz     # {"ok":true,...}
```
Dashboard: `https://hondabotchat.duckdns.org/admin?token=<BOT_DASH_TOKEN>`

---

## Cập nhật code về sau

Sửa code trên máy local -> làm lại mục 5. Bot tự dựng lại, **dữ liệu khách giữ nguyên** (nằm ở `data/` + `.env` trên VPS, không nằm trong image).

Sửa **persona / bảng sản phẩm** thì KHÔNG cần build - `data/docs/` là bind-mount đè lên bản trong image:
```bash
gcloud compute scp Document_ChatBot_Mess/Personal.md hon-da-vps:/tmp/Personal.md --zone=us-central1-a
gcloud compute ssh hon-da-vps --zone=us-central1-a --command="cd /home/admin/chatbot-mess && \
  sudo cp data/docs/Personal.md data/docs/Personal.md.bak-\$(date +%F-%H%M%S) && \
  sudo cp /tmp/Personal.md data/docs/Personal.md && docker compose restart bot"
```
Khách **đang** trò chuyện dở còn nhận bản cũ tối đa 1 tiếng (Gemini cache context TTL 3600s).

## Backup dữ liệu (khuyến nghị)

Trên VPS, cron hằng ngày nén `data/` + `.env`:
```bash
# crontab -e
0 2 * * * cd ~/chatbot-mess && tar czf ~/backup-$(date +\%F).tgz data .env && find ~ -name 'backup-*.tgz' -mtime +14 -delete
```
Muốn đẩy lên Cloud Storage (5GB free): cài `gcloud`, thay lệnh bằng `... && gsutil cp ~/backup-*.tgz gs://<bucket>/`.

## Nút Restart trên dashboard

Trong Docker, `RESTART_MODE=exit` (đã set sẵn ở docker-compose) khiến nút Restart cho container thoát -> `restart: always` dựng lại trong ~5 giây. Log stdout xem bằng `docker compose logs -f bot`.

## Xử lý sự cố

| Triệu chứng | Cách |
|---|---|
| **Webhook trả 403, bot câm** | `MSGR_APP_SECRET` KHÁC app đang gửi webhook. Token và secret phải CÙNG App. Đã dính thật, mất mấy tiếng. |
| **Sửa token trên dashboard mà không ăn** | Dashboard chỉ ghi 4 key non-secret (`admin.py:36`), token/secret bị vứt im lặng. Sửa `.env` tay qua SSH. |
| **Comment không được trả lời** | Thiếu field `feed` trong Subscriptions, hoặc token thiếu `pages_manage_engagement` (công khai) / `pages_messaging` (nhắn riêng). Xem log `[comment]`. |
| **Domain NXDOMAIN** | DuckDNS xoá subdomain không update trong ~30 ngày. Đã mất 1 lần. Cài cron ping (mục 2). |
| Caddy không lấy được SSL | DuckDNS đã trỏ đúng IP chưa? Port 80/443 mở chưa (firewall GCP)? |
| Bot 502 | `docker compose logs bot` xem lỗi; `.env` đủ token chưa |
| Webhook verify fail | `DOMAIN` trong .env khớp Callback URL; bot đang chạy |
| Mất dữ liệu sau update | Kiểm tra volume `./data` mount đúng; KHÔNG xóa thư mục data |
| Ghi `.env` báo Permission denied | File thuộc user `admin`, phải `sudo`. Dùng đường dẫn tuyệt đối `/home/admin/chatbot-mess/.env` - hardcode sai đường dẫn làm mất luôn dòng token. |

## Lệnh chẩn đoán hay dùng

```bash
Z="--zone=us-central1-a --strict-host-key-checking=no"

# webhook có tới không, 200 hay 403
gcloud compute ssh hon-da-vps $Z --command="docker logs chatbot-mess-bot-1 --since 30m 2>&1 | grep 'POST /webhook' | grep -o -e '200 OK' -e '403 Forbidden' | sort | uniq -c"

# comment có vào không
gcloud compute ssh hon-da-vps $Z --command="docker logs chatbot-mess-bot-1 --since 30m 2>&1 | grep comment || echo '(khong co)'"

# khách cũ quay lại
gcloud compute ssh hon-da-vps $Z --command="docker logs chatbot-mess-bot-1 --since 30m 2>&1 | grep quaylai || echo '(khong co)'"

# hạn token + scope
gcloud compute ssh hon-da-vps $Z --command="cd /home/admin/chatbot-mess; T=\$(sudo grep -m1 '^MSGR_PAGE_TOKEN=' .env | cut -d= -f2-); S=\$(sudo grep -m1 '^MSGR_APP_SECRET=' .env | cut -d= -f2-); curl -s \"https://graph.facebook.com/v21.0/debug_token?input_token=\$T&access_token=2073944916564638%7C\$S\" | python3 -m json.tool"

# RAM từng container + đỉnh đã chạm
gcloud compute ssh hon-da-vps $Z --command="docker stats --no-stream"
```

`grep` không khớp trả exit code 1, gcloud dịch thành "SSH failed" - thêm `|| echo` để phân biệt
lỗi thật với "không có dòng nào".
