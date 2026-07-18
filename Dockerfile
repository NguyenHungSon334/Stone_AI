# Bot Messenger Hồn Đá - image chạy trên VPS (GCP Compute Engine).
FROM python:3.11-slim

WORKDIR /app

# tzdata: image slim thiếu zoneinfo -> TZ=Asia/Ho_Chi_Minh không ăn nếu không cài.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# Cài phụ thuộc trước (tận dụng layer cache khi chỉ đổi code).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ code.
COPY . .

# Bản gốc persona + bảng sản phẩm để seed lần đầu (khi volume docs còn rỗng).
RUN cp -r Document_ChatBot_Mess /app/seed_docs

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 7900
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "app.py"]
