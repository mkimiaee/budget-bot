"""
لایه دیتابیس ربات دخل‌وخرج تلگرام
از SQLite استفاده می‌کند - ساده، بدون نیاز به سرور جدا.
"""
import sqlite3
import os
import secrets
import string
from datetime import datetime, date, timedelta
from contextlib import contextmanager
from collections import Counter

DB_PATH = os.environ.get("BOT_DB_PATH", os.path.join(os.path.dirname(__file__), "data", "bot.db"))

# واحد پول پیش‌فرض برای خانواده‌های جدید؛ با متغیر محیطی DEFAULT_CURRENCY قابل تنظیم است.
# هر خانواده بعداً می‌تواند با دستور /currency آن را برای خودش عوض کند.
DEFAULT_CURRENCY = os.environ.get("DEFAULT_CURRENCY", "€")

CURRENCY_PRESETS = {
    "toman": "تومان", "تومان": "تومان",
    "rial": "ریال", "ریال": "ریال",
    "eur": "€", "euro": "€", "یورو": "€", "€": "€",
    "usd": "$", "dollar": "$", "دلار": "$", "$": "$",
    "gbp": "£", "پوند": "£", "£": "£",
    "aed": "درهم", "درهم": "درهم",
}

# روزهای هفته به ترتیب تقویم پایتون: دوشنبه=0 ... یکشنبه=6
WEEKDAY_FA_NAMES = {
    0: "دوشنبه", 1: "سه‌شنبه", 2: "چهارشنبه", 3: "پنجشنبه",
    4: "جمعه", 5: "شنبه", 6: "یکشنبه",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS households (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    invite_code TEXT UNIQUE NOT NULL,
    currency TEXT DEFAULT 'تومان',
    budget_period TEXT DEFAULT 'monthly',      -- 'monthly' | 'weekly'
    week_start_weekday INTEGER,                -- 0=دوشنبه .. 6=یکشنبه (فقط وقتی weekly است)
    owner_id INTEGER,                          -- telegram_id کسی که خانواده رو ساخته؛ فقط همون کد دعوت رو می‌بینه
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    household_id INTEGER NOT NULL,
    display_name TEXT,
    joined_at TEXT NOT NULL,
    FOREIGN KEY (household_id) REFERENCES households(id)
);

CREATE TABLE IF NOT EXISTS budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL,
    period_type TEXT NOT NULL,     -- 'monthly'
    amount REAL NOT NULL,
    period_key TEXT NOT NULL,      -- e.g. '2026-07'
    created_at TEXT NOT NULL,
    FOREIGN KEY (household_id) REFERENCES households(id)
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER,          -- NULL = دسته سراسری پیش‌فرض
    name TEXT NOT NULL,
    keywords TEXT DEFAULT ''       -- کلمات کلیدی جدا شده با کاما برای دسته‌بندی خودکار
);

CREATE TABLE IF NOT EXISTS recurring_bills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    category TEXT,                 -- اگه خالی باشه، موقع ثبت "قبض" استفاده می‌شه
    created_at TEXT NOT NULL,
    FOREIGN KEY (household_id) REFERENCES households(id)
);

CREATE TABLE IF NOT EXISTS category_budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL,
    category TEXT NOT NULL,
    period_type TEXT NOT NULL,     -- 'monthly' | 'weekly'
    amount REAL NOT NULL,
    period_key TEXT NOT NULL,      -- e.g. '2026-07'
    created_at TEXT NOT NULL,
    FOREIGN KEY (household_id) REFERENCES households(id)
);

CREATE TABLE IF NOT EXISTS email_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL UNIQUE,
    email_address TEXT NOT NULL,
    app_password TEXT NOT NULL,       -- App Password (نه رمز اصلی حساب)
    imap_host TEXT NOT NULL DEFAULT 'imap.gmail.com',
    imap_port INTEGER NOT NULL DEFAULT 993,
    sender_filter TEXT DEFAULT 'mercadona',  -- فقط ایمیل‌هایی که فرستنده‌شون شامل این رشته باشه چک می‌شه
    last_uid INTEGER DEFAULT 0,       -- آخرین UID پردازش‌شده، برای جلوگیری از پردازش تکراری
    created_at TEXT NOT NULL,
    FOREIGN KEY (household_id) REFERENCES households(id)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    type TEXT NOT NULL,            -- 'income' | 'expense'
    amount REAL NOT NULL,
    category TEXT,
    description TEXT,
    store TEXT,                    -- نام فروشگاه (اختیاری)
    source TEXT DEFAULT 'manual',  -- 'manual' | 'ocr' | 'list'
    receipt_id TEXT,               -- شناسه مشترک بین همه ردیف‌های یک فاکتور (عکس/PDF) برای حذف دسته‌جمعی
    tx_date TEXT NOT NULL,         -- YYYY-MM-DD (تاریخ خرید، نه لزوما تاریخ ثبت)
    in_budget INTEGER DEFAULT 1,   -- 1=جزو بودجه (خوراک/هزینه اصلی) | 0=هزینه جانبی (قبض و غیره)، فقط در گزارش
    created_at TEXT NOT NULL,
    FOREIGN KEY (household_id) REFERENCES households(id)
);

CREATE TABLE IF NOT EXISTS shopping_lists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    status TEXT DEFAULT 'active',  -- 'active' | 'done'
    created_at TEXT NOT NULL,
    FOREIGN KEY (household_id) REFERENCES households(id)
);

CREATE TABLE IF NOT EXISTS shopping_list_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    list_id INTEGER NOT NULL,
    item_name TEXT NOT NULL,
    quantity TEXT DEFAULT '',
    bought INTEGER DEFAULT 0,      -- 0/1
    matched_price REAL,
    FOREIGN KEY (list_id) REFERENCES shopping_lists(id)
);
"""

DEFAULT_CATEGORIES = [
    ("خوار و بار", "برنج,روغن,شکر,چای,حبوبات,رب,آرد,نمک,ماکارونی,اسپاگتی,عدس,لوبیا,نخود"),
    ("لبنیات", "شیر,ماست,پنیر,کره,خامه,دوغ"),
    ("میوه و سبزی", "میوه,سبزی,سیب,پرتقال,موز,خیار,گوجه,سیب زمینی,پیاز,هویج,سبزیجات"),
    ("گوشت و پروتئین", "گوشت,مرغ,ماهی,تخم مرغ,سوسیس,کالباس"),
    ("نان و صبحانه", "نان,عسل,مربا,کره بادام زمینی"),
    ("بهداشتی", "شامپو,صابون,دستمال,مایع ظرفشویی,پودر لباسشویی,خمیر دندان,شوینده"),
    ("لوازم خانه", "ظرف,لوازم آشپزخانه,لامپ,باتری"),
    ("تنقلات و نوشیدنی", "نوشابه,آبمیوه,چیپس,بیسکویت,شکلات,آدامس,تنقلات"),
    ("حمل و نقل", "بنزین,تاکسی,اسنپ,پارکینگ,مترو,اتوبوس"),
    ("قبض و اجاره", "قبض,اجاره,شارژ,اینترنت,آب,برق,گاز"),
    ("خرید از Mercadona", "mercadona"),
    ("خرید از Consum", "consum"),
    ("خرید از Lidl", "lidl"),
    ("متفرقه", ""),
]

# وقتی موقع تایید فاکتور، کاربر فروشگاه رو دستی از دکمه انتخاب می‌کنه (نه از روی تشخیص خودکار متن)،
# این نگاشت مشخص می‌کنه اسم دسته متناظر با هر فروشگاه چیه — باید با نام‌های DEFAULT_CATEGORIES بالا یکی باشه.
RECEIPT_STORE_CATEGORY = {
    "Mercadona": "خرید از Mercadona",
    "Consum": "خرید از Consum",
    "Lidl": "خرید از Lidl",
}


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        # مهاجرت ساده: اگر دیتابیس قدیمی‌تر از اضافه‌شدن این ستون‌ها باشد، اضافه‌شان کن
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(households)").fetchall()]
        if "currency" not in cols:
            conn.execute("ALTER TABLE households ADD COLUMN currency TEXT DEFAULT 'تومان'")
        if "budget_period" not in cols:
            conn.execute("ALTER TABLE households ADD COLUMN budget_period TEXT DEFAULT 'monthly'")
        if "week_start_weekday" not in cols:
            conn.execute("ALTER TABLE households ADD COLUMN week_start_weekday INTEGER")
        if "owner_id" not in cols:
            conn.execute("ALTER TABLE households ADD COLUMN owner_id INTEGER")
            # برای خانواده‌های قدیمی‌تر که owner_id ندارن، قدیمی‌ترین عضو رو مالک در نظر می‌گیریم
            households_without_owner = conn.execute(
                "SELECT id FROM households WHERE owner_id IS NULL"
            ).fetchall()
            for h in households_without_owner:
                first_member = conn.execute(
                    "SELECT telegram_id FROM users WHERE household_id=? ORDER BY joined_at LIMIT 1",
                    (h["id"],),
                ).fetchone()
                if first_member:
                    conn.execute(
                        "UPDATE households SET owner_id=? WHERE id=?",
                        (first_member["telegram_id"], h["id"]),
                    )
        tx_cols = [r["name"] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()]
        if "store" not in tx_cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN store TEXT")
        if "receipt_id" not in tx_cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN receipt_id TEXT")
        if "in_budget" not in tx_cols:
            # تراکنش‌های قدیمی همه جزو بودجه حساب می‌شن (رفتار قبلی دست‌نخورده می‌مونه)
            conn.execute("ALTER TABLE transactions ADD COLUMN in_budget INTEGER DEFAULT 1")
        # هر دسته پیش‌فرض که هنوز تو دیتابیس نیست رو اضافه کن (نه فقط بار اول) — این‌جوری وقتی بعداً
        # به DEFAULT_CATEGORIES یک دسته جدید اضافه می‌شه (مثل فروشگاه‌های جدید)، با ری‌استارت بعدی ربات
        # خودش به دیتابیس‌های قدیمی هم اضافه می‌شه، بدون اینکه دسته‌های موجود دوباره ساخته/تکرار بشن.
        existing_names = {
            r["name"] for r in conn.execute("SELECT name FROM categories WHERE household_id IS NULL").fetchall()
        }
        for name, kw in DEFAULT_CATEGORIES:
            if name not in existing_names:
                conn.execute(
                    "INSERT INTO categories (household_id, name, keywords) VALUES (NULL, ?, ?)",
                    (name, kw),
                )


def _new_invite_code():
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(6))


def get_user_household(telegram_id):
    with get_conn() as conn:
        row = conn.execute("SELECT household_id FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
        return row["household_id"] if row else None


def create_household_and_user(telegram_id, display_name, household_name=None):
    with get_conn() as conn:
        code = _new_invite_code()
        while conn.execute("SELECT 1 FROM households WHERE invite_code=?", (code,)).fetchone():
            code = _new_invite_code()
        name = household_name or f"خانواده {display_name or telegram_id}"
        cur = conn.execute(
            "INSERT INTO households (name, invite_code, currency, owner_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (name, code, DEFAULT_CURRENCY, telegram_id, datetime.utcnow().isoformat()),
        )
        household_id = cur.lastrowid
        conn.execute(
            "INSERT INTO users (telegram_id, household_id, display_name, joined_at) VALUES (?, ?, ?, ?)",
            (telegram_id, household_id, display_name, datetime.utcnow().isoformat()),
        )
        return household_id, code


def join_household(telegram_id, display_name, invite_code):
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM households WHERE invite_code=?", (invite_code.strip().upper(),)).fetchone()
        if not row:
            return None
        household_id = row["id"]
        conn.execute(
            "INSERT OR REPLACE INTO users (telegram_id, household_id, display_name, joined_at) VALUES (?, ?, ?, ?)",
            (telegram_id, household_id, display_name, datetime.utcnow().isoformat()),
        )
        return household_id


def get_invite_code(household_id):
    with get_conn() as conn:
        row = conn.execute("SELECT invite_code FROM households WHERE id=?", (household_id,)).fetchone()
        return row["invite_code"] if row else None


def get_owner_id(household_id):
    with get_conn() as conn:
        row = conn.execute("SELECT owner_id FROM households WHERE id=?", (household_id,)).fetchone()
        return row["owner_id"] if row else None


def get_household_members(household_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT telegram_id, display_name, joined_at FROM users WHERE household_id=? ORDER BY joined_at",
            (household_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_household_ids():
    with get_conn() as conn:
        rows = conn.execute("SELECT id FROM households").fetchall()
        return [r["id"] for r in rows]


def remove_member(household_id, telegram_id):
    """عضو رو از خانواده حذف می‌کنه؛ خودش می‌تونه بعداً /start بزنه (خانواده جدید بسازه) یا دوباره join کنه."""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM users WHERE household_id=? AND telegram_id=?",
            (household_id, telegram_id),
        )
        return cur.rowcount


def is_household_owner(household_id, telegram_id):
    return get_owner_id(household_id) == telegram_id


def get_owner_display_name(household_id):
    with get_conn() as conn:
        row = conn.execute(
            """SELECT u.display_name FROM users u
               JOIN households h ON h.owner_id = u.telegram_id
               WHERE h.id=?""",
            (household_id,),
        ).fetchone()
        return row["display_name"] if row and row["display_name"] else None


def get_currency(household_id):
    with get_conn() as conn:
        row = conn.execute("SELECT currency FROM households WHERE id=?", (household_id,)).fetchone()
        return (row["currency"] if row and row["currency"] else DEFAULT_CURRENCY)


def set_currency(household_id, currency_input):
    normalized = CURRENCY_PRESETS.get(currency_input.strip().lower(), currency_input.strip())
    with get_conn() as conn:
        conn.execute("UPDATE households SET currency=? WHERE id=?", (normalized, household_id))
    return normalized


# ---------- بازه بودجه (ماهانه یا هفتگی با روز شروع دلخواه) ----------

def get_budget_period(household_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT budget_period, week_start_weekday FROM households WHERE id=?", (household_id,)
        ).fetchone()
        if not row:
            return {"period_type": "monthly", "week_start_weekday": None}
        return {
            "period_type": row["budget_period"] or "monthly",
            "week_start_weekday": row["week_start_weekday"],
        }


def set_budget_period(household_id, period_type, week_start_weekday=None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE households SET budget_period=?, week_start_weekday=? WHERE id=?",
            (period_type, week_start_weekday, household_id),
        )
    # هر بودجه با یک period_key مشخص (مثل '2026-07' یا تاریخ شروع هفته) ذخیره می‌شه. عوض‌کردن بازه
    # (ماهانه/هفتگی یا روز شروع هفته) باعث می‌شه period_key فعلی عوض بشه، و بودجه‌ای که قبلاً برای
    # period_key قدیمی ثبت شده بود دیگه پیدا نشه (انگار پاک شده، هرچند واقعاً تو دیتابیس هست).
    # برای جلوگیری از این «گم‌شدن» — چه کاربر اول بودجه رو بزنه بعد بازه رو عوض کنه، چه برعکس —
    # اگه برای بازه‌ی تازه هنوز بودجه‌ای ثبت نشده، آخرین مبلغ بودجه‌ای که ثبت کرده بود رو خودکار
    # به بازه جدید هم منتقل می‌کنیم.
    if get_budget(household_id) is None:
        with get_conn() as conn:
            last = conn.execute(
                "SELECT amount FROM budgets WHERE household_id=? ORDER BY id DESC LIMIT 1",
                (household_id,),
            ).fetchone()
        if last is not None:
            set_budget(household_id, last["amount"])


def get_current_period_bounds(household_id, ref_date=None):
    """
    بازه بودجه جاری خانواده را برمی‌گرداند: (تاریخ شروع, تاریخ پایان, کلید یکتای بازه, نوع بازه)
    برای بازه هفتگی، هفته از نزدیک‌ترین روز هفته‌ی انتخاب‌شده (که <= امروز است) شروع می‌شود.
    """
    ref_date = ref_date or date.today()
    period = get_budget_period(household_id)
    if period["period_type"] == "weekly":
        wd = period["week_start_weekday"] if period["week_start_weekday"] is not None else 0
        days_since_start = (ref_date.weekday() - wd) % 7
        start = ref_date - timedelta(days=days_since_start)
        end = start + timedelta(days=6)
        period_key = start.isoformat()
    else:
        start = ref_date.replace(day=1)
        if ref_date.month == 12:
            next_month = ref_date.replace(year=ref_date.year + 1, month=1, day=1)
        else:
            next_month = ref_date.replace(month=ref_date.month + 1, day=1)
        end = next_month - timedelta(days=1)
        period_key = ref_date.strftime("%Y-%m")
    return start, end, period_key, period["period_type"]


def set_budget(household_id, amount):
    start, end, period_key, period_type = get_current_period_bounds(household_id)
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM budgets WHERE household_id=? AND period_type=? AND period_key=?",
            (household_id, period_type, period_key),
        )
        conn.execute(
            "INSERT INTO budgets (household_id, period_type, amount, period_key, created_at) VALUES (?, ?, ?, ?, ?)",
            (household_id, period_type, amount, period_key, datetime.utcnow().isoformat()),
        )


def get_budget(household_id):
    start, end, period_key, period_type = get_current_period_bounds(household_id)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT amount FROM budgets WHERE household_id=? AND period_type=? AND period_key=? ORDER BY id DESC LIMIT 1",
            (household_id, period_type, period_key),
        ).fetchone()
        return row["amount"] if row else None


def set_category_budget(household_id, category, amount):
    """بودجه بازه جاری رو برای یک دسته مشخص تنظیم می‌کنه. amount<=0 یعنی حذف بودجه اون دسته."""
    start, end, period_key, period_type = get_current_period_bounds(household_id)
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM category_budgets WHERE household_id=? AND category=? AND period_type=? AND period_key=?",
            (household_id, category, period_type, period_key),
        )
        if amount and amount > 0:
            conn.execute(
                """INSERT INTO category_budgets (household_id, category, period_type, amount, period_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (household_id, category, period_type, amount, period_key, datetime.utcnow().isoformat()),
            )


def get_category_budget(household_id, category):
    start, end, period_key, period_type = get_current_period_bounds(household_id)
    with get_conn() as conn:
        row = conn.execute(
            """SELECT amount FROM category_budgets
               WHERE household_id=? AND category=? AND period_type=? AND period_key=?
               ORDER BY id DESC LIMIT 1""",
            (household_id, category, period_type, period_key),
        ).fetchone()
        return row["amount"] if row else None


def get_category_budgets_with_spent(household_id):
    """همه‌ی بودجه‌های دسته‌ایِ تنظیم‌شده برای بازه جاری، به‌همراه مبلغ خرج‌شده‌ی همون دسته تا الان
    (فقط هزینه‌های داخل بودجه، هزینه‌های جانبی حساب نمی‌شن — مشابه بودجه کلی)."""
    start, end, period_key, period_type = get_current_period_bounds(household_id)
    with get_conn() as conn:
        budgets = conn.execute(
            "SELECT category, amount FROM category_budgets WHERE household_id=? AND period_type=? AND period_key=? ORDER BY id",
            (household_id, period_type, period_key),
        ).fetchall()
        result = []
        for b in budgets:
            spent_row = conn.execute(
                """SELECT COALESCE(SUM(amount),0) s FROM transactions
                   WHERE household_id=? AND type='expense' AND category=? AND in_budget=1
                     AND tx_date BETWEEN ? AND ?""",
                (household_id, b["category"], start.isoformat(), end.isoformat()),
            ).fetchone()
            spent = spent_row["s"]
            result.append({
                "category": b["category"], "budget": b["amount"], "spent": spent,
                "remaining": b["amount"] - spent,
            })
        return result


# ---------- اتصال ایمیل برای خوندن خودکار فاکتور ----------

def set_email_account(household_id, email_address, app_password, imap_host="imap.gmail.com",
                       imap_port=993, sender_filter="mercadona"):
    with get_conn() as conn:
        conn.execute("DELETE FROM email_accounts WHERE household_id=?", (household_id,))
        conn.execute(
            """INSERT INTO email_accounts
               (household_id, email_address, app_password, imap_host, imap_port, sender_filter, last_uid, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?)""",
            (household_id, email_address, app_password, imap_host, imap_port, sender_filter,
             datetime.utcnow().isoformat()),
        )


def get_email_account(household_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM email_accounts WHERE household_id=?", (household_id,)).fetchone()
        return dict(row) if row else None


def get_all_email_accounts():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM email_accounts").fetchall()
        return [dict(r) for r in rows]


def update_email_last_uid(household_id, uid):
    with get_conn() as conn:
        conn.execute("UPDATE email_accounts SET last_uid=? WHERE household_id=?", (uid, household_id))


def delete_email_account(household_id):
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM email_accounts WHERE household_id=?", (household_id,))
        return cur.rowcount


# ---------- قبض‌های تکرارشونده ----------

def add_or_update_recurring_bill(household_id, name, amount, category=None):
    """اگه قبضی با همین اسم قبلاً تعریف شده، مبلغش رو آپدیت می‌کنه؛ وگرنه یکی جدید می‌سازه."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM recurring_bills WHERE household_id=? AND name=?",
            (household_id, name),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE recurring_bills SET amount=?, category=? WHERE id=?",
                (amount, category, existing["id"]),
            )
            return existing["id"]
        cur = conn.execute(
            "INSERT INTO recurring_bills (household_id, name, amount, category, created_at) VALUES (?, ?, ?, ?, ?)",
            (household_id, name, amount, category, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def get_recurring_bills(household_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM recurring_bills WHERE household_id=? ORDER BY id",
            (household_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_recurring_bill(bill_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM recurring_bills WHERE id=?", (bill_id,)).fetchone()
        return dict(row) if row else None


def delete_recurring_bill(household_id, bill_id):
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM recurring_bills WHERE id=? AND household_id=?",
            (bill_id, household_id),
        )
        return cur.rowcount


def add_transaction(household_id, user_id, tx_type, amount, category=None, description=None,
                     source="manual", tx_date=None, store=None, receipt_id=None, in_budget=1):
    tx_date = tx_date or date.today().isoformat()
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO transactions
               (household_id, user_id, type, amount, category, description, store, source, receipt_id, tx_date,
                in_budget, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (household_id, user_id, tx_type, amount, category, description, store, source, receipt_id, tx_date,
             1 if in_budget else 0, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def get_transaction(tx_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
        return dict(row) if row else None


def get_recent_transactions(household_id, limit=10):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE household_id=? ORDER BY id DESC LIMIT ?",
            (household_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_last_transaction(household_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM transactions WHERE household_id=? ORDER BY id DESC LIMIT 1",
            (household_id,),
        ).fetchone()
        return dict(row) if row else None


def delete_transaction(tx_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM transactions WHERE id=?", (tx_id,))


def get_receipt_transactions(household_id, receipt_id):
    """همه ردیف‌های ثبت‌شده از یک فاکتور (عکس/PDF) را برمی‌گرداند."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM transactions WHERE household_id=? AND receipt_id=? ORDER BY id",
            (household_id, receipt_id),
        ).fetchall()
        return [dict(r) for r in rows]


def find_similar_receipt(household_id, store, total_amount, near_date, tolerance_days=3):
    """قبل از ثبت یه فاکتور جدید (پیش‌نویس)، دنبال یه فاکتور قبلاً ثبت‌شده می‌گرده که جمعش نزدیک
    همین مبلغه و توی چند روز نزدیک همین تاریخ ثبت شده (و اگه فروشگاه مشخص باشه، فروشگاهش هم یکی باشه)
    — برای هشدار «شبیه یه فاکتور قبلی‌ه» قبل از تایید نهایی. اگه پیدا نشه None برمی‌گردونه."""
    with get_conn() as conn:
        start = (near_date - timedelta(days=tolerance_days)).isoformat()
        end = (near_date + timedelta(days=tolerance_days)).isoformat()
        rows = conn.execute(
            """SELECT receipt_id, store, tx_date, SUM(amount) as total
               FROM transactions
               WHERE household_id=? AND type='expense' AND receipt_id IS NOT NULL
                 AND tx_date BETWEEN ? AND ?
               GROUP BY receipt_id""",
            (household_id, start, end),
        ).fetchall()
        tolerance = max(total_amount * 0.02, 0.01)
        for r in rows:
            if store and r["store"] and r["store"].lower() != store.lower():
                continue
            if abs(r["total"] - total_amount) <= tolerance:
                return dict(r)
    return None


def delete_receipt(household_id, receipt_id):
    """همه ردیف‌های یک فاکتور را یک‌جا حذف می‌کند؛ تعداد ردیف‌های حذف‌شده را برمی‌گرداند."""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM transactions WHERE household_id=? AND receipt_id=?",
            (household_id, receipt_id),
        )
        return cur.rowcount


def update_transaction(tx_id, amount=None, description=None, category=None, store=None, tx_date=None):
    """به‌روزرسانی جزئی یک تراکنش؛ فقط فیلدهایی که مقدار غیر None دارند تغییر می‌کنند."""
    fields, values = [], []
    for col, val in (("amount", amount), ("description", description), ("category", category),
                      ("store", store), ("tx_date", tx_date)):
        if val is not None:
            fields.append(f"{col}=?")
            values.append(val)
    if not fields:
        return
    values.append(tx_id)
    with get_conn() as conn:
        conn.execute(f"UPDATE transactions SET {', '.join(fields)} WHERE id=?", values)


def adjust_budget(household_id, delta):
    """مبلغ بودجه بازه جاری را به‌اندازه delta زیاد/کم می‌کند (delta می‌تواند منفی باشد) و مقدار جدید را برمی‌گرداند."""
    current = get_budget(household_id) or 0.0
    new_amount = current + delta
    set_budget(household_id, new_amount)
    return new_amount


def _sum(conn, household_id, tx_type, start, end, in_budget_only=False):
    query = "SELECT COALESCE(SUM(amount),0) s FROM transactions WHERE household_id=? AND type=? AND tx_date BETWEEN ? AND ?"
    params = [household_id, tx_type, start, end]
    if in_budget_only:
        query += " AND in_budget=1"
    row = conn.execute(query, params).fetchone()
    return row["s"]


def get_balance(household_id):
    """بودجه بازه جاری (ماهانه یا هفتگیِ تنظیم‌شده) منهای هزینه‌ها به‌علاوه درآمدهای همان بازه.
    هزینه‌های جانبی (in_budget=0، مثل قبض) اینجا حساب نمی‌شن؛ فقط تو گزارش دیده می‌شن."""
    today = date.today()
    start, end, period_key, period_type = get_current_period_bounds(household_id, today)

    budget = get_budget(household_id) or 0.0
    with get_conn() as conn:
        period_expense = _sum(conn, household_id, "expense", start.isoformat(), end.isoformat(), in_budget_only=True)
        period_income = _sum(conn, household_id, "income", start.isoformat(), end.isoformat())
        day_expense = _sum(conn, household_id, "expense", today.isoformat(), today.isoformat(), in_budget_only=True)

    remaining = budget + period_income - period_expense
    days_left = (end - today).days + 1  # شامل امروز
    return {
        "period_type": period_type,
        "period_start": start,
        "period_end": end,
        "budget": budget,
        "period_expense": period_expense,
        "period_income": period_income,
        "remaining": remaining,
        "day_expense": day_expense,
        "days_left_in_period": days_left,
        "avg_daily_allowance": (remaining / days_left) if days_left > 0 else remaining,
        # نگهداری نام‌های قدیمی برای سازگاری با کدهای قبلی
        "month_expense": period_expense,
        "month_income": period_income,
        "days_left_in_month": days_left,
    }


def _report_period_bounds(household_id, period):
    """بازه تاریخ (start, end به‌صورت رشته YYYY-MM-DD) برای یک برچسب گزارش ('day'|'week'|'month'|'period')
    را برمی‌گرداند. برای 'week' از همان روز شروع هفته‌ای استفاده می‌شود که در تنظیمات بازه بودجه انتخاب
    شده (week_start_weekday)، تا جمع گزارش هفتگی همیشه با محاسبه /balance یکی باشد."""
    today = date.today()
    if period in ("day", "روز"):
        return today.isoformat(), today.isoformat()
    if period in ("week", "هفته"):
        p = get_budget_period(household_id)
        wd = p["week_start_weekday"] if p["week_start_weekday"] is not None else 0
        days_since_start = (today.weekday() - wd) % 7
        start = (today - timedelta(days=days_since_start)).isoformat()
        return start, today.isoformat()
    if period in ("period", "بازه"):
        p_start, p_end, _, _ = get_current_period_bounds(household_id, today)
        return p_start.isoformat(), today.isoformat()
    # month
    return today.replace(day=1).isoformat(), today.isoformat()


def get_category_totals(household_id, period):
    """جمع هزینه‌های داخل بودجه (in_budget=1) به تفکیک دسته، برای یک بازه گزارش — برای نمودار."""
    start, end = _report_period_bounds(household_id, period)
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT COALESCE(category, 'متفرقه') as category, SUM(amount) as total FROM transactions
               WHERE household_id=? AND type='expense' AND in_budget=1 AND tx_date BETWEEN ? AND ?
               GROUP BY category ORDER BY total DESC""",
            (household_id, start, end),
        ).fetchall()
        return [{"category": r["category"], "total": r["total"]} for r in rows]


def get_report(household_id, period):
    """
    گزارش ساده: هر فاکتور (همه ردیف‌های یک عکس/PDF که receipt_id مشترک دارند) یک ردیف واحد
    می‌شود (با جمع مبلغش)، و هر هزینه دستی هم یک ردیف جدا؛ فقط تاریخ، یک برچسب کوتاه، و مبلغ —
    بدون جزئیات ردیف‌به‌ردیف فاکتور. به‌علاوه جمع کل بازه و جمع امروز.
    """
    start, end = _report_period_bounds(household_id, period)
    today = date.today()

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT tx_date, amount, category, description, receipt_id, in_budget FROM transactions
               WHERE household_id=? AND type='expense' AND tx_date BETWEEN ? AND ?
               ORDER BY tx_date, id""",
            (household_id, start, end),
        ).fetchall()
        today_total = _sum(conn, household_id, "expense", today.isoformat(), today.isoformat(), in_budget_only=True)

    groups = []
    receipts_by_id = {}
    for r in rows:
        rid = r["receipt_id"]
        in_budget = 1 if r["in_budget"] is None else r["in_budget"]
        if rid:
            g = receipts_by_id.get(rid)
            if g is None:
                g = {
                    "tx_date": r["tx_date"], "amount": 0.0, "categories": [], "count": 0,
                    "receipt_id": rid, "in_budget": in_budget,
                }
                receipts_by_id[rid] = g
                groups.append(g)
            g["amount"] += r["amount"]
            g["categories"].append(r["category"] or "متفرقه")
            g["count"] += 1
        else:
            groups.append({
                "tx_date": r["tx_date"], "amount": r["amount"], "count": 1, "receipt_id": None,
                "label": r["description"] or r["category"] or "هزینه", "in_budget": in_budget,
            })

    for g in groups:
        if g.get("receipt_id"):
            if g["count"] == 1:
                g["label"] = g["categories"][0]
            else:
                cat_name, cat_count = Counter(g["categories"]).most_common(1)[0]
                g["label"] = f"{cat_name} ({g['count']} قلم)" if cat_count / g["count"] >= 0.5 else f"خرید ({g['count']} قلم)"
            g.pop("categories", None)

    # هزینه‌های جانبی (قبض و غیره) جدا از جمع اصلی بودجه نگه داشته می‌شن، ولی توی همون لیست groups می‌مونن
    total = sum(g["amount"] for g in groups if g.get("in_budget", 1))
    side_total = sum(g["amount"] for g in groups if not g.get("in_budget", 1))
    return {
        "start": start, "end": end, "groups": groups,
        "total": total, "side_total": side_total, "today_total": today_total,
    }


def get_categories(household_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name, keywords FROM categories WHERE household_id IS NULL OR household_id=? ORDER BY id",
            (household_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def add_category(household_id, name, keywords=""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO categories (household_id, name, keywords) VALUES (?, ?, ?)",
            (household_id, name, keywords),
        )


def get_household_only_categories(household_id):
    """فقط دسته‌های اختصاصی همین خانواده (بدون دسته‌های سراسری پیش‌فرض) — این‌ها قابل حذفن."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, keywords FROM categories WHERE household_id=? ORDER BY id",
            (household_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_category(household_id, category_id):
    """فقط دسته‌های اختصاصی همون خانواده حذف می‌شن؛ دسته‌های سراسری پیش‌فرض (household_id IS NULL) دست‌نخورده می‌مونن."""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM categories WHERE id=? AND household_id=?",
            (category_id, household_id),
        )
        return cur.rowcount


# ---------- لیست خرید ----------

def create_shopping_list(household_id, name="لیست خرید"):
    with get_conn() as conn:
        conn.execute(
            "UPDATE shopping_lists SET status='done' WHERE household_id=? AND status='active'",
            (household_id,),
        )
        cur = conn.execute(
            "INSERT INTO shopping_lists (household_id, name, status, created_at) VALUES (?, ?, 'active', ?)",
            (household_id, name, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def get_active_list(household_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM shopping_lists WHERE household_id=? AND status='active' ORDER BY id DESC LIMIT 1",
            (household_id,),
        ).fetchone()
        return dict(row) if row else None


def get_list_by_id(list_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM shopping_lists WHERE id=?", (list_id,)
        ).fetchone()
        return dict(row) if row else None


def add_list_items(list_id, item_names):
    with get_conn() as conn:
        for item in item_names:
            item = item.strip()
            if item:
                conn.execute(
                    "INSERT INTO shopping_list_items (list_id, item_name) VALUES (?, ?)",
                    (list_id, item),
                )


def get_list_items(list_id):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM shopping_list_items WHERE list_id=? ORDER BY id", (list_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def mark_item_bought(item_id, bought=True, price=None):
    with get_conn() as conn:
        conn.execute(
            "UPDATE shopping_list_items SET bought=?, matched_price=COALESCE(?, matched_price) WHERE id=?",
            (1 if bought else 0, price, item_id),
        )


def delete_list_item(item_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM shopping_list_items WHERE id=?", (item_id,))


def delete_shopping_list(list_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM shopping_list_items WHERE list_id=?", (list_id,))
        conn.execute("DELETE FROM shopping_lists WHERE id=?", (list_id,))


def close_list(list_id):
    with get_conn() as conn:
        conn.execute("UPDATE shopping_lists SET status='done' WHERE id=?", (list_id,))
