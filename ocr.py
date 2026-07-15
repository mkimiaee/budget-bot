"""
پردازش فاکتور خرید (عکس یا PDF):
1) اگر PDF است، هر صفحه به عکس تبدیل می‌شود (با PyMuPDF)
2) استخراج متن هر عکس با Tesseract OCR (فارسی+انگلیسی)
3) جدا کردن ردیف‌های کالا+قیمت
4) تطبیق فازی (fuzzy) با آیتم‌های لیست خرید فعال

نکته مهم: OCR فارسی روی فاکتورهای فروشگاهی/چاپی حرارتی همیشه ۱۰۰٪ دقیق نیست.
به همین دلیل نتیجه همیشه برای تایید نهایی به کاربر نشان داده می‌شود، نه اینکه کورکورانه ثبت شود.
"""
import re
import io
from PIL import Image

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

try:
    import fitz  # PyMuPDF
    PDF_AVAILABLE = True
except ImportError:
    PDF_AVAILABLE = False

try:
    from rapidfuzz import fuzz
    def _similarity(a, b):
        return fuzz.partial_ratio(a, b) / 100.0
except ImportError:
    from difflib import SequenceMatcher
    def _similarity(a, b):
        return SequenceMatcher(None, a, b).ratio()

from categorize import normalize_text, extract_amount_detailed

MATCH_THRESHOLD = 0.6
MAX_PDF_PAGES = 5  # جلوگیری از پردازش سنگین روی PDFهای خیلی طولانی

# سقف عقلانی برای مبلغ یک ردیف تکی فاکتور. اعدادی بزرگ‌تر از این تقریباً همیشه شماره تلفن،
# بارکد، شناسه فاکتور، یا کد پیگیری هستند، نه قیمت یک کالا — صرف‌نظر از واحد پول.
MAX_PLAUSIBLE_LINE_AMOUNT = 100_000_000

# حداقل مبلغ قابل قبول وقتی عدد به‌صورت عدد صحیح (بدون علامت اعشار) نوشته شده — برای رد کردن
# کدهای کوچک/تعداد که به اشتباه به‌عنوان قیمت خونده می‌شن. برای مبالغ اعشاری (مثل 2,00 یورو)
# این حداقل اعمال نمی‌شه، چون خودِ فرمت اعشاری نشونه قویه که این یک قیمت واقعیه، حتی اگه کوچیک باشه.
MIN_PLAUSIBLE_INTEGER_AMOUNT = 100


def _ocr_image(image: Image.Image) -> str:
    # تبدیل به سیاه‌وسفید و بزرگ‌نمایی جزئی برای بهبود دقت OCR روی فاکتورهای کوچک
    image = image.convert("L")
    w, h = image.size
    if max(w, h) < 1600:
        scale = 1600 / max(w, h)
        image = image.resize((int(w * scale), int(h * scale)))
    return pytesseract.image_to_string(image, lang="fas+eng")


def extract_receipt_text(image_bytes: bytes) -> str:
    """OCR روی یک عکس تکی (jpg/png/...)."""
    if not TESSERACT_AVAILABLE:
        raise RuntimeError(
            "pytesseract/tesseract نصب نیست. راهنمای نصب در README را ببینید."
        )
    image = Image.open(io.BytesIO(image_bytes))
    return _ocr_image(image)


def pdf_to_images(pdf_bytes: bytes):
    """هر صفحه یک PDF را به تصویر PIL تبدیل می‌کند (حداکثر MAX_PDF_PAGES صفحه)."""
    if not PDF_AVAILABLE:
        raise RuntimeError("PyMuPDF نصب نیست. راهنمای نصب در README را ببینید.")
    images = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page_num, page in enumerate(doc):
            if page_num >= MAX_PDF_PAGES:
                break
            pix = page.get_pixmap(dpi=200)
            images.append(Image.open(io.BytesIO(pix.tobytes("png"))))
    return images


def extract_receipt_text_from_pdf(pdf_bytes: bytes) -> str:
    """OCR روی همه صفحات یک PDF و ترکیب متن‌ها (برای PDFهای اسکن‌شده/عکسی)."""
    if not TESSERACT_AVAILABLE:
        raise RuntimeError(
            "pytesseract/tesseract نصب نیست. راهنمای نصب در README را ببینید."
        )
    images = pdf_to_images(pdf_bytes)
    return "\n".join(_ocr_image(img) for img in images)


def extract_pdf_text_layer(pdf_bytes: bytes) -> str:
    """
    اگر PDF یک لایه متنِ قابل‌استخراج دارد (یعنی به‌صورت دیجیتال تولید شده، نه اسکن یک عکس)،
    آن متن را مستقیم و بدون نیاز به OCR برمی‌گرداند — دقت این روش تقریباً همیشه ۱۰۰٪ است.
    اگر PDF فقط عکس اسکن‌شده باشد (بدون لایه متن)، رشته خالی برمی‌گردد.
    """
    if not PDF_AVAILABLE:
        raise RuntimeError("PyMuPDF نصب نیست. راهنمای نصب در README را ببینید.")
    texts = []
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page_num, page in enumerate(doc):
            if page_num >= MAX_PDF_PAGES:
                break
            texts.append(page.get_text())
    return "\n".join(texts)


# خطوطی که معمولا سطر کالا نیستند (سربرگ، تاریخ، جمع کل، شماره فاکتور، مالیات، پرداخت و ...)
# و اگر در متن خط ظاهر شوند، آن خط نادیده گرفته می‌شود تا در جمع هزینه‌ها دوبار حساب نشود یا
# اطلاعات بی‌ربط (تلفن، بارکد، شماره پیگیری) به‌عنوان کالا ثبت نشود.
# چندزبانه است چون فاکتورها ممکنه فارسی، انگلیسی، اسپانیایی و... باشن؛ بدون حساسیت به بزرگی/کوچکی حروف چک می‌شه.
NON_ITEM_KEYWORDS = [
    # فارسی
    "جمع", "مجموع", "تاریخ", "فاکتور", "شماره", "تخفیف", "مالیات",
    "پرداخت", "نقدی", "کارتخوان", "فروشگاه", "نشانی", "تلفن", "ساعت",
    # انگلیسی / بین‌المللی
    "total", "subtotal", "tax", "vat", "card", "cash", "change",
    "receipt", "invoice", "date", "time", "phone", "tel", "fax",
    "auth", "ref", "barcode", "thank", "cashier", "store", "address",
    # اسپانیایی (فاکتورهای اروپایی مثل مرکادونا)
    "factura", "iva", "tarjeta", "tarj", "importe", "cuota", "imponible",
    "teléfono", "telefono", "fecha", "hora", "op:", "aut:", "arc:", "aid:",
    "cif", "nif", "gracias", "devoluciones", "verificado", "dispositivo",
    "bancaria", "banco", "descripción", "descripcion", "operación",
]


def _has_letter(text: str) -> bool:
    """آیا متن حداقل یک حرف الفبایی دارد (فارسی یا لاتین)؟ برای رد کردن ردیف‌هایی که فقط عدد/نماد هستند."""
    return re.search(r"[^\W\d_]", text, re.UNICODE) is not None


# اگر یکی از این‌ها به‌عنوان تیتر ستون‌های کالا در فاکتور دیده بشه، یعنی از این خط به بعد لیست کالاهاست
# و هر چی قبلش بوده (نام فروشگاه، آدرس، تلفن، شناسه مالیاتی و...) قطعاً هدر است، نه کالا.
# این کمک می‌کنه اعداد بزرگ تو سربرگ (مثل شناسه مالیاتی یا کدپستی) اشتباهی به‌عنوان قیمت خونده نشن.
HEADER_START_KEYWORDS = [
    "descripción", "descripcion", "description", "producto", "product",
    "cantidad", "qty", "item", "کالا", "شرح",
]


def parse_receipt_lines(raw_text: str):
    """
    هر خط فاکتور را به (نام کالا، مبلغ) تبدیل می‌کند.
    خطوطی که مبلغ معتبر ندارند، با کلمات غیرکالایی مطابقت دارند، نام‌شان فقط عدد/نماد است،
    یا مبلغ‌شان غیرمنطقی بزرگ است (شماره تلفن/بارکد/کد پیگیری) نادیده گرفته می‌شوند.
    اگر تیتر ستون کالا (مثل «Descripción») پیدا شود، خطوط قبل از آن (سربرگ فروشگاه) اصلاً بررسی نمی‌شوند.

    توجه: OCR ۱۰۰٪ دقیق نیست؛ همیشه نتیجه را قبل از اعتماد کامل، در پیام ربات مرور کن.
    """
    raw_lines = raw_text.splitlines()
    start_idx = 0
    for i, raw_line in enumerate(raw_lines):
        if any(h in normalize_text(raw_line).lower() for h in HEADER_START_KEYWORDS):
            start_idx = i + 1
            break

    lines = []
    for raw_line in raw_lines[start_idx:]:
        line = normalize_text(raw_line)
        if not line or len(line) < 2:
            continue
        line_lower = line.lower()
        if any(kw in line_lower for kw in NON_ITEM_KEYWORDS):
            continue

        amount, name, is_decimal = extract_amount_detailed(line)
        if amount is None:
            continue
        # اعداد صحیح خیلی کوچک (بدون علامت اعشار) معمولا کد/تعداد هستند، نه قیمت.
        # اما اگر عدد به‌شکل اعشاری/پولی نوشته شده (مثل 2,00) حتی مقدار کوچیک هم قابل‌قبوله.
        if not is_decimal and amount < MIN_PLAUSIBLE_INTEGER_AMOUNT:
            continue
        # اعداد غیرمنطقی بزرگ تقریباً همیشه تلفن/بارکد/کد پیگیری‌اند، نه قیمت یک کالا.
        if amount >= MAX_PLAUSIBLE_LINE_AMOUNT:
            continue

        name = re.sub(r"[*#:\-]+", " ", name).strip()
        if not name or not _has_letter(name):
            continue
        lines.append({"name": name, "amount": amount})
    return lines


def parse_receipt_lines_columnar(text: str):
    """
    برای متن استخراج‌شده مستقیم از لایه PDF (نه OCR) — که معمولاً نام کالا و قیمتش در دو خط
    جدا از هم قرار می‌گیرند (چون از دو ستون مجزا استخراج شده‌اند)، مثلاً:
        1 CROISSANT
        1,85
        2 TORTITAS DE ARROZ
        1,10
        2,20
    (وقتی تعداد بیش از ۱ باشد، دو عدد می‌آید: قیمت واحد و قیمت کل — عدد آخر یعنی مبلغ کل همان ردیف.)

    منطق: هر خط را یا «توصیف کالا» (دارای حرف) یا «قیمت» (فقط عدد) در نظر می‌گیرد؛ برای هر کالا
    آخرین قیمتِ دیده‌شده قبل از توصیف کالای بعدی را به‌عنوان مبلغ نهایی آن ثبت می‌کند.
    """
    raw_lines = [normalize_text(l) for l in text.splitlines()]
    raw_lines = [l for l in raw_lines if l.strip()]

    start_idx = 0
    for i, l in enumerate(raw_lines):
        if any(h in l.lower() for h in HEADER_START_KEYWORDS):
            start_idx = i + 1
            break

    items = []
    current_name = None
    current_amount = None

    for line in raw_lines[start_idx:]:
        line_lower = line.lower()
        if any(kw in line_lower for kw in NON_ITEM_KEYWORDS):
            # اگر هنوز به هیچ کالایی نرسیده‌ایم، این یک تکه از سربرگ/ستون‌عنوان باقی‌مانده است؛ ردش کن.
            # اگر قبلاً کالا جمع کرده‌ایم، یعنی به بخش جمع‌کل/پرداخت رسیده‌ایم؛ دیگه کالایی نیست.
            if items or (current_name and current_amount is not None):
                break
            continue

        amount, name_part, _ = extract_amount_detailed(line)
        name_part_clean = re.sub(r"[*#:\-%]+", " ", name_part).strip()

        if amount is not None and not _has_letter(name_part_clean):
            # این خط فقط عدد است (قیمت) — به کالای در حال جمع‌آوری فعلی نسبت بده
            if current_name:
                current_amount = amount
            continue

        # این خط توصیف کالاست (حرف دارد) — کالای قبلی را (اگر کامل بود) ثبت کن و کالای جدید را شروع کن
        if current_name and current_amount is not None:
            items.append({"name": current_name, "amount": current_amount})
        current_name = re.sub(r"^\d+\s+", "", line).strip()
        current_amount = None

    if current_name and current_amount is not None:
        items.append({"name": current_name, "amount": current_amount})

    # همون فیلترهای عقلانیِ مبلغ که در parse_receipt_lines هست، اینجا هم اعمال کن
    filtered = []
    for it in items:
        if it["amount"] < 0.01 or it["amount"] >= MAX_PLAUSIBLE_LINE_AMOUNT:
            continue
        filtered.append(it)
    return filtered


def match_against_list(receipt_lines, list_items):
    """
    receipt_lines: [{"name":..., "amount":...}, ...]
    list_items: خروجی db.get_list_items (شامل id, item_name, bought)

    خروجی: (matches, unmatched_receipt_lines)
    matches: [{"list_item": {...}, "receipt_line": {...}, "score": float}]
    """
    matches = []
    used_receipt_idx = set()
    candidates = [it for it in list_items if not it["bought"]]

    for item in candidates:
        item_name_norm = normalize_text(item["item_name"])
        best_idx, best_score = None, 0.0
        for idx, rline in enumerate(receipt_lines):
            if idx in used_receipt_idx:
                continue
            score = _similarity(item_name_norm, rline["name"])
            if score > best_score:
                best_score, best_idx = score, idx
        if best_idx is not None and best_score >= MATCH_THRESHOLD:
            used_receipt_idx.add(best_idx)
            matches.append({
                "list_item": item,
                "receipt_line": receipt_lines[best_idx],
                "score": best_score,
            })

    unmatched = [rl for idx, rl in enumerate(receipt_lines) if idx not in used_receipt_idx]
    return matches, unmatched
