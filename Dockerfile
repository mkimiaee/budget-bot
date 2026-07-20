FROM python:3.11-slim

# tesseract + پک زبان فارسی برای خواندن فاکتورها
# فونت برای PDF گزارش‌ها:
#   ۱) fonts-noto-core از ریپوی رسمی دبیان نصب می‌شه — یه fallback مطمئن که همیشه کار می‌کنه.
#   ۲) فونت وزیر (Vazirmatn) مستقیم از jsDelivr (که آینه گیت‌هابه) دانلود می‌شه چون تو ریپوی دبیان
#      نیست؛ فونت اصلی و ترجیحی برای متن فارسیه. اگه دانلودش به هر دلیلی شکست بخوره، بیلد متوقف
#      نمی‌شه و همون Noto به‌عنوان fallback باقی می‌مونه.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-fas \
    fonts-noto-core \
    curl \
    ca-certificates \
    && mkdir -p /usr/share/fonts/truetype/vazirmatn \
    && ( curl -fsSL -o /usr/share/fonts/truetype/vazirmatn/Vazirmatn-Regular.ttf \
           https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/fonts/ttf/Vazirmatn-Regular.ttf \
         && curl -fsSL -o /usr/share/fonts/truetype/vazirmatn/Vazirmatn-Bold.ttf \
           https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/fonts/ttf/Vazirmatn-Bold.ttf \
         || echo "⚠️  Vazirmatn download failed — falling back to Noto Sans Arabic" ) \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# دیتابیس SQLite داخل این پوشه ذخیره می‌شود؛ روی Railway یک Volume به این مسیر وصل کنید
# تا اطلاعات بعد از هر دیپلوی جدید پاک نشوند. توجه: BOT_DB_PATH باید مسیر خودِ فایل دیتابیس باشه
# نه فقط پوشه‌ش — وگرنه sqlite با خطای "unable to open database file" کرش می‌کنه.
RUN mkdir -p /app/data
ENV BOT_DB_PATH=/app/data/bot.db

CMD ["python", "bot.py"]
