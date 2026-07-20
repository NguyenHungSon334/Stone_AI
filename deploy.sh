#!/usr/bin/env bash
# Deploy từ máy local: build image -> push Artifact Registry -> SSH vào VPS pull + up.
# Chạy: ./deploy.sh   (cần điền config bên dưới hoặc export biến môi trường trước khi chạy)
#
# Yêu cầu 1 lần trên máy local:
#   gcloud auth login
#   gcloud auth configure-docker $REGION-docker.pkg.dev
#   docker (Desktop) đang chạy
set -euo pipefail

# ==== ĐIỀN CÁC GIÁ TRỊ NÀY (hoặc export trước khi chạy) ====
PROJECT="${GCP_PROJECT:?export GCP_PROJECT=ten-project-gcp}"
REGION="${GCP_REGION:-us-central1}"          # khớp region VPS free tier
REPO="${GCP_REPO:-hondastone}"               # tên Artifact Registry repo
VPS_USER="${VPS_USER:?export VPS_USER=user-ssh-vps}"
VPS_HOST="${VPS_HOST:?export VPS_HOST=ip-hoac-domain-vps}"
VPS_DIR="${VPS_DIR:-~/chatbot-mess}"         # thư mục chứa docker-compose.yml trên VPS
# ============================================================

IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/bot:latest"

# Chặn ở đây rẻ hơn nhiều so với phát hiện trên VPS: script này push thẳng lên prod, hỏng là
# khách nhắn không ai trả lời cho tới lần deploy sau.
echo "==> Chạy test"
python -m pytest tests/ -q

echo "==> Build image: $IMAGE"
docker build -t "$IMAGE" .

echo "==> Push lên Artifact Registry"
docker push "$IMAGE"

# Document_ChatBot_Mess bị bind mount từ ./data/docs trên VPS -> bản trong image KHÔNG có tác dụng.
# Sửa Personal.md/CSV ở local mà chỉ build image thì VPS vẫn chạy bản cũ. Copy thẳng lên host.
echo "==> Đồng bộ persona + bảng sản phẩm lên VPS"
ssh "$VPS_USER@$VPS_HOST" "mkdir -p $VPS_DIR/data/docs"
scp Document_ChatBot_Mess/* "$VPS_USER@$VPS_HOST:$VPS_DIR/data/docs/"

echo "==> Deploy trên VPS ($VPS_HOST)"
ssh "$VPS_USER@$VPS_HOST" "cd $VPS_DIR && export IMAGE='$IMAGE' && docker compose pull && docker compose up -d && docker image prune -f"

echo "==> Xong. Kiểm tra: https://\$DOMAIN/healthz"
