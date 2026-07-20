#!/usr/bin/env bash
# Deploy CHẠY THẲNG TRÊN VPS: git pull -> build tại chỗ -> up.
# Khác deploy.sh (build ở local rồi push Artifact Registry) - cách này không cần gcloud/Docker local.
# Chạy: cd ~/chatbot-mess && ./deploy-vps.sh
set -euo pipefail

cd "$(dirname "$0")"

echo "==> Kéo code mới"
git pull --ff-only

# Document_ChatBot_Mess bị bind mount từ ./data/docs -> bản trong image KHÔNG có tác dụng.
# Không copy bước này thì sửa Personal.md xong build lại vẫn chạy persona cũ.
echo "==> Cập nhật persona + bảng sản phẩm"
mkdir -p data/docs
cp Document_ChatBot_Mess/* data/docs/

echo "==> Build image trên VPS (e2-micro chậm, ~3-5 phút lần đầu)"
docker compose build

echo "==> Khởi động lại bot"
docker compose up -d
docker image prune -f

echo "==> Chờ bot lên..."
sleep 5
docker compose ps
curl -s http://localhost:7900/healthz || echo "(chưa lên, xem: docker compose logs --tail=50 bot)"
echo
