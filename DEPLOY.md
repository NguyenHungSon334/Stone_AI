# Deploy bot lên VPS GCP (Combo A - chi phí $0)

Kiến trúc: máy local `docker build` -> push Artifact Registry -> VPS `docker compose up` (bot + Caddy tự SSL). Webhook FB -> `https://<domain>` -> Caddy -> bot.

State (lịch sử khách, stats, CRM meta, persona, .env) nằm trên VPS ở `~/chatbot-mess/data` + `.env` -> update image KHÔNG mất dữ liệu.

---

## 1. Tạo VPS free (GCP Compute Engine)

1. https://console.cloud.google.com -> tạo Project (ghi lại PROJECT_ID).
2. Compute Engine -> Create Instance:
   - Region: **us-central1** (hoặc us-west1 / us-east1 - free tier)
   - Machine: **e2-micro** (free tier)
   - Boot disk: Debian 12, 30GB standard
   - Firewall: tick **Allow HTTP** + **Allow HTTPS**
3. Tạo xong ghi lại **External IP**.

## 2. DuckDNS (domain miễn phí)

1. https://www.duckdns.org -> đăng nhập (Google/GitHub).
2. Tạo subdomain, vd `hondastone` -> `hondastone.duckdns.org`.
3. Ô "current ip" điền **External IP của VPS** -> Update.
4. (Nếu IP VPS đổi: chạy lại update, hoặc đặt IP tĩnh trong GCP.)

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
- `.env`  (copy từ `.env.example`, ĐIỀN đủ token thật + `DOMAIN=hondastone.duckdns.org`)

## 5. Artifact Registry + auth (máy local)

Cài Google Cloud SDK, rồi:
```bash
gcloud auth login
gcloud config set project PROJECT_ID
gcloud artifacts repositories create hondastone --repository-format=docker --location=us-central1
gcloud auth configure-docker us-central1-docker.pkg.dev
```

## 6. Deploy lần đầu (máy local)

Trong thư mục repo, export biến rồi chạy `deploy.sh`:
```bash
export GCP_PROJECT=PROJECT_ID
export GCP_REGION=us-central1
export VPS_USER=<user-ssh>          # tên user SSH trên VPS
export VPS_HOST=hondastone.duckdns.org   # hoặc External IP
./deploy.sh
```

Script tự: build image -> push -> SSH vào VPS `docker compose pull && up -d`.

Lần đầu chưa có SSH key tới VPS thì thêm public key vào VPS (`~/.ssh/authorized_keys`) hoặc dùng `gcloud compute ssh`.

## 7. Trỏ webhook FB sang domain mới

Meta app -> Messenger -> Webhooks -> sửa Callback URL:
```
https://hondastone.duckdns.org/webhook/messenger
```
Verify token giữ nguyên. Verify + Save.

Trong `.env` trên VPS đổi `PUBLIC_URL=https://hondastone.duckdns.org` (để gửi ảnh), rồi bấm Restart trên dashboard hoặc `docker compose restart bot`.

## 8. Kiểm tra

```bash
curl https://hondastone.duckdns.org/healthz     # {"ok":true,...}
```
Dashboard: `https://hondastone.duckdns.org/admin?token=<BOT_DASH_TOKEN>`

---

## Cập nhật code về sau

Sửa code trên máy local -> chạy lại `./deploy.sh`. Bot tự dựng lại, **dữ liệu khách giữ nguyên** (nằm ở `data/` + `.env` trên VPS, không nằm trong image).

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
| Caddy không lấy được SSL | DuckDNS đã trỏ đúng IP chưa? Port 80/443 mở chưa (firewall GCP)? |
| Bot 502 | `docker compose logs bot` xem lỗi; `.env` đủ token chưa |
| Webhook verify fail | `DOMAIN` trong .env khớp Callback URL; bot đang chạy |
| Mất dữ liệu sau update | Kiểm tra volume `./data` mount đúng; KHÔNG xóa thư mục data |
