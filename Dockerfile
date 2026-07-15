FROM python:3.11-slim

# tesseract + پک زبان فارسی برای خواندن فاکتورها
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-fas \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# دیتابیس SQLite داخل این پوشه ذخیره می‌شود؛ روی Railway یک Volume به این مسیر وصل کنید
# تا اطلاعات بعد از هر دیپلوی جدید پاک نشوند.
RUN mkdir -p /app/data
ENV BOT_DB_PATH=/app/data/bot.db

CMD ["python", "bot.py"]
