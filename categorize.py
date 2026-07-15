"""
دسته‌بندی خودکار خریدها بر اساس کلمات کلیدی فارسی.
"""
import re
from datetime import datetime, date
import db

PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")


def normalize_text(text: str) -> str:
    text = text.translate(PERSIAN_DIGITS).translate(ARABIC_DIGITS)
    text = text.replace("ي", "ی").replace("ك", "ک")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parse_number_detailed(raw: str):
    """
    یک رشته عددی خام را به float تبدیل می‌کند و جداکننده هزارگان/اعشار را حدس می‌زند.
    مثال‌ها: '50,000' -> (50000, False)   |   '12.50' -> (12.5, True)   |   '1.234.567' -> (1234567, False)   |   '12,50' -> (12.5, True)
    خروجی دوم می‌گوید آیا جداکننده به‌عنوان اعشار تفسیر شد یا نه (یعنی عدد واقعاً یک مقدار اعشاری/پولی به‌نظر می‌رسد).
    """
    is_decimal = False
    has_comma, has_dot = "," in raw, "." in raw
    if has_comma and has_dot:
        # جداکننده‌ای که آخر ظاهر شده، احتمالا اعشاره
        if raw.rfind(",") > raw.rfind("."):
            decimal_sep, thousands_sep = ",", "."
        else:
            decimal_sep, thousands_sep = ".", ","
        raw = raw.replace(thousands_sep, "").replace(decimal_sep, ".")
        is_decimal = True
    elif has_comma or has_dot:
        sep = "," if has_comma else "."
        parts = raw.split(sep)
        # اگر فقط یک بار جداکننده اومده و بعدش ۱ یا ۲ رقمه، احتمالا اعشاره (مثل قیمت یورویی 12.50)
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            raw = parts[0] + "." + parts[1]
            is_decimal = True
        else:
            raw = raw.replace(sep, "")
    try:
        return float(raw), is_decimal
    except ValueError:
        return None, False


def _parse_number(raw: str):
    amount, _ = _parse_number_detailed(raw)
    return amount


def extract_amount_detailed(text: str):
    """
    مثل extract_amount ولی یک مقدار سوم هم برمی‌گرداند: is_decimal
    (آیا مبلغ به‌صورت اعشاری/پولی نوشته شده بود، مثل 2,00 یا 9.97 — که نشانه قوی «قیمت واقعی» است).
    """
    text = normalize_text(text)
    # اعداد شامل رقم و به‌صورت اختیاری جداکننده هزارگان/اعشار
    matches = re.findall(r"\d[\d,\.]*", text)
    if not matches:
        return None, text, False
    best = max(matches, key=lambda m: len(m.replace(",", "").replace(".", "")))
    amount, is_decimal = _parse_number_detailed(best)
    if amount is None:
        return None, text, False
    remaining = text.replace(best, "").strip()
    return amount, remaining, is_decimal


def extract_amount(text: str):
    """اولین عدد معنی‌دار (مبلغ) را از متن استخراج می‌کند. اعداد با کاما یا نقطه جدا شده را هم می‌فهمد."""
    amount, remaining, _ = extract_amount_detailed(text)
    return amount, remaining


def categorize(item_text: str, household_id=None) -> str:
    text = normalize_text(item_text)
    cats = db.get_categories(household_id) if household_id else db.DEFAULT_CATEGORIES
    for cat in cats:
        if isinstance(cat, dict):
            name, keywords = cat["name"], cat["keywords"]
        else:
            name, keywords = cat
        for kw in (keywords or "").split(","):
            kw = kw.strip()
            if kw and kw in text:
                return name
    return "متفرقه"


def parse_free_text_expense(text: str, household_id=None):
    """
    از یک پیام آزاد مثل 'نان 50000' یا '50000 تومان بابت نان' مبلغ و شرح و دسته را استخراج می‌کند.
    """
    amount, remaining_text = extract_amount(text)
    description = remaining_text or text
    # حذف کلمات/نمادهای رایج واحد پول تا در نام آیتم باقی نمانند
    description = re.sub(
        r"\b(تومان|ریال|تومن|یورو|دلار|پوند|درهم|euro|eur|usd|dollar|gbp|aed)\b",
        "", description, flags=re.IGNORECASE,
    )
    description = re.sub(r"[€$£]", "", description).strip()
    category = categorize(description, household_id)
    return amount, description, category


def parse_simple_date(text: str):
    """
    یک تاریخ میلادی را به فرمت YYYY-MM-DD برمی‌گرداند، یا None اگر معتبر نبود.
    هم فرمت 'YYYY-MM-DD' (سال اول) و هم 'DD-MM-YYYY' (روز اول، رایج در اروپا) را می‌فهمد —
    با تشخیص اینکه کدوم بخش ۴ رقمیه (سال).
    """
    cleaned = normalize_text(text).strip().replace("/", "-").replace(".", "-")
    parts = cleaned.split("-")
    if len(parts) != 3:
        return None
    p1, p2, p3 = parts
    try:
        if len(p1) == 4:
            y, m, d = int(p1), int(p2), int(p3)
        elif len(p3) == 4:
            d, m, y = int(p1), int(p2), int(p3)
        else:
            return None
        return date(y, m, d).isoformat()
    except (ValueError, TypeError):
        return None


# الگوی یک تاریخ خام بدون کلمه راهنما (مثل «تاریخ»)، برای وقتی کاربر مستقیم می‌نویسه
# مثلاً '15-07-2026' یا '2026/07/15' بدون اینکه بگه این یه تاریخه.
_BARE_DATE_RE = re.compile(r"\b(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4})\b")


def parse_free_text_expense_detailed(text: str, household_id=None):
    """
    مثل parse_free_text_expense ولی اگر کاربر اسم فروشگاه یا تاریخ خرید را هم مشخص کرده باشد
    آن‌ها را هم استخراج می‌کند. دو حالت پشتیبانی می‌شود:
    ۱) با کلمه راهنما: 'نان 50000 فروشگاه رفاه تاریخ 2026-07-10'
    ۲) بدون کلمه راهنما، فقط با نوشتن یک تاریخ معتبر تو متن: 'mercadona 9.97 eur 15-07-2026'
       (تاریخ خودکار تشخیص داده و از محاسبه مبلغ کنار گذاشته می‌شود تا با مبلغ اشتباه گرفته نشود)
    خروجی: (amount, description, category, store, tx_date) — store/tx_date اگر مشخص نشده باشند None هستند.
    """
    working = normalize_text(text)

    store = None
    tx_date = None

    m_date = re.search(r"(?:تاریخ|date)\s+([0-9]{1,4}[-/.][0-9]{1,2}[-/.][0-9]{1,4})", working, re.IGNORECASE)
    if m_date:
        tx_date = parse_simple_date(m_date.group(1))
        working = (working[:m_date.start()] + working[m_date.end():]).strip()
    else:
        # کلمه راهنما نبود؛ دنبال یک الگوی تاریخِ خام بگرد (مثلاً 15-07-2026) تا با مبلغ قاطی نشه
        m_bare = _BARE_DATE_RE.search(working)
        if m_bare:
            candidate = parse_simple_date(m_bare.group(1))
            if candidate:
                tx_date = candidate
                working = (working[:m_bare.start()] + working[m_bare.end():]).strip()

    m_store = re.search(r"(?:فروشگاه|مغازه|store|shop)\s+([^\n]+)", working, re.IGNORECASE)
    if m_store:
        store = m_store.group(1).strip() or None
        working = (working[:m_store.start()] + working[m_store.end():]).strip()

    amount, description, category = parse_free_text_expense(working, household_id)
    return amount, description, category, store, tx_date


def parse_week_start_input(text: str):
    """
    ورودی کاربر برای انتخاب «روز شروع هفته» را تفسیر می‌کند.
    یا نام روز هفته است (مثل 'دوشنبه')، یا یک تاریخ میلادی (مثل '2026-07-13' یا '2026/07/13')
    که در این صورت روز هفته‌ی همان تاریخ استخراج می‌شود.
    خروجی: عدد ۰ تا ۶ (۰=دوشنبه ... ۶=یکشنبه) یا None اگر قابل تشخیص نبود.
    """
    text = normalize_text(text).strip()
    for idx, name in db.WEEKDAY_FA_NAMES.items():
        if name in text:
            return idx
    iso_date = parse_simple_date(text.replace(" ", ""))
    if iso_date:
        return datetime.strptime(iso_date, "%Y-%m-%d").date().weekday()
    return None
