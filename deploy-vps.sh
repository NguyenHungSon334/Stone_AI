#!/usr/bin/env bash
# Build lại bot TỪ MÃ ĐANG CÓ TRÊN VPS -> up. Chạy: cd ~/chatbot-mess && ./deploy-vps.sh
# Đẩy mã mới lên VPS trước bằng scp (DEPLOY.md mục 5), script này chỉ lo build + đổi container.
#
# KHÔNG `git pull` ở đây: repo trên VPS đứng ở commit cũ, còn mã thật do scp đè lên, và
# Caddyfile/docker-compose.yml trên VPS có chỉnh riêng (site hondatools của Agent Lỗ Ban).
# Pull là ff-only thất bại giữa chừng hoặc đè mất chỉnh tay -> sập site. Muốn deploy bằng git
# thì phải đồng bộ repo VPS trước, không phải nhét lén vào script này.
set -euo pipefail

cd "$(dirname "$0")"

# Document_ChatBot_Mess bị bind mount từ ./data/docs -> bản trong image KHÔNG có tác dụng.
# Không copy bước này thì sửa Personal.md xong build lại vẫn chạy persona cũ.
echo "==> Cập nhật persona + bảng sản phẩm"
mkdir -p data/docs
# data/ do Docker tạo nên có thể thuộc root -> cp thường bị Permission denied.
cp Document_ChatBot_Mess/* data/docs/ 2>/dev/null || sudo cp Document_ChatBot_Mess/* data/docs/

echo "==> Build image trên VPS (e2-micro chậm, ~3-5 phút lần đầu)"
docker compose build

echo "==> Khởi động lại bot"
docker compose up -d
docker image prune -f

echo "==> Chờ bot lên..."
sleep 5
docker compose ps
# Bot chỉ 'expose' 7900 (không publish) -> host không gọi localhost:7900 được. Hỏi từ trong container.
docker compose exec -T bot python -c "import httpx;print(httpx.get('http://localhost:7900/healthz').text)" \
    || echo "(chưa lên, xem: docker compose logs --tail=50 bot)"
