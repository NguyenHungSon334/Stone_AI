#!/bin/sh
# Seed persona + bảng sản phẩm vào volume docs LẦN ĐẦU (thư mục mount còn rỗng).
# -> sửa persona qua dashboard vẫn giữ khi update image; lần đầu không bị trống.
set -e

if [ ! -f /app/Document_ChatBot_Mess/Personal.md ]; then
    mkdir -p /app/Document_ChatBot_Mess
    cp -rn /app/seed_docs/. /app/Document_ChatBot_Mess/ 2>/dev/null || true
fi

exec "$@"
