"""
ربات تلگرام دخل‌وخرج خانواده
اجرا: python bot.py   (نیاز به متغیر محیطی BOT_TOKEN)
"""
import logging
import os
import uuid
from datetime import date, timedelta

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    BotCommand,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

import db
import categorize
import ocr

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("budget-bot")

BOT_TOKEN = os.environ.get("BOT_TOKEN")


# واحدهایی که سنتاً بدون اعشار نوشته می‌شوند (چون ارزش اسمی‌شون خیلی بزرگه و اعشار بی‌معنیه)
NO_DECIMAL_CURRENCIES = {"تومان", "ریال"}


def fmt(n, currency=None):
    if currency in NO_DECIMAL_CURRENCIES:
        s = f"{n:,.0f}"
    else:
        # برای یورو/دلار/پوند و بقیه واحدها همیشه با ۲ رقم اعشار (مثل قیمت واقعی روی فاکتور)
        s = f"{n:,.2f}"
    return f"{s} {currency}" if currency else s


def require_household(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        household_id = db.get_user_household(update.effective_user.id)
        if household_id is None:
            await update.message.reply_text(
                "اول باید /start رو بزنی تا حساب خانواده‌ت ساخته بشه."
            )
            return
        context.chat_data["household_id"] = household_id
        return await func(update, context, household_id)
    return wrapper


# ---------------- منوی اصلی (کیبورد پایین صفحه) ----------------

BTN_BALANCE = "💰 موجودی"
BTN_EXPENSE = "➖ ثبت هزینه"
BTN_INCOME = "➕ ثبت درآمد"
BTN_LIST = "🛒 لیست خرید"
BTN_REPORT = "📊 گزارش"
BTN_SETTINGS = "⚙️ تنظیمات"
BTN_HELP = "❓ راهنما"

MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    [
        [BTN_BALANCE, BTN_EXPENSE],
        [BTN_INCOME, BTN_LIST],
        [BTN_REPORT, BTN_SETTINGS],
        [BTN_HELP],
    ],
    resize_keyboard=True,
    is_persistent=True,
)


def _settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 تنظیم بودجه", callback_data="m:budget")],
        [InlineKeyboardButton("📅 بازه بودجه (هفتگی/ماهانه)", callback_data="m:period")],
        [InlineKeyboardButton("💱 تغییر واحد پول", callback_data="m:currency")],
        [InlineKeyboardButton("📁 دسته‌بندی‌ها", callback_data="m:categories")],
        [InlineKeyboardButton("🧾 تراکنش‌های اخیر (حذف/ویرایش)", callback_data="tx:list")],
        [InlineKeyboardButton("🔄 محاسبه مجدد بودجه و هزینه‌ها", callback_data="m:recalc")],
        [InlineKeyboardButton("🔗 کد دعوت خانواده", callback_data="m:invite")],
    ])


def _period_choice_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("ماهانه", callback_data="m:period:monthly"),
        InlineKeyboardButton("هفتگی", callback_data="m:period:weekly"),
    ]])


def _period_label(household_id):
    """برچسب فارسیِ بازه بودجه فعلی، مثل 'این ماه' یا 'این هفته (از دوشنبه)'."""
    p = db.get_budget_period(household_id)
    if p["period_type"] == "weekly":
        wd = p["week_start_weekday"]
        day_name = db.WEEKDAY_FA_NAMES.get(wd, "دوشنبه") if wd is not None else "دوشنبه"
        return f"این هفته (از {day_name})"
    return "این ماه"


def _report_period_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("امروز", callback_data="m:report:day"),
        InlineKeyboardButton("این هفته", callback_data="m:report:week"),
        InlineKeyboardButton("این ماه", callback_data="m:report:month"),
    ]])


# ---------------- دستورات پایه ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    existing = db.get_user_household(user.id)
    if existing:
        code = db.get_invite_code(existing)
        await update.message.reply_text(
            f"سلام {user.first_name}! قبلاً ثبت‌نامی. کد دعوت خانواده‌ت: {code}\n"
            "از منوی پایین صفحه استفاده کن یا /help رو بزن.",
            reply_markup=MAIN_MENU_KEYBOARD,
        )
        return
    household_id, code = db.create_household_and_user(user.id, user.first_name)
    currency = db.get_currency(household_id)
    await update.message.reply_text(
        f"سلام {user.first_name}! خوش اومدی 👋\n\n"
        f"یک حساب خانواده برات ساختم. کد دعوت: `{code}`\n"
        "این کد رو به بقیه اعضای خانواده بده تا با دستور زیر بهت ملحق بشن:\n"
        f"`/join {code}`\n\n"
        f"واحد پول فعلاً روی «{currency}» تنظیمه. از منوی ⚙️ تنظیمات می‌تونی عوضش کنی.\n\n"
        "از منوی پایین صفحه استفاده کن، یا برای شروع بودجه ماه رو بزن:\n"
        "`/budget 5000000`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=MAIN_MENU_KEYBOARD,
    )


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.chat_data.pop("awaiting", None)
    await update.message.reply_text("منوی اصلی 👇", reply_markup=MAIN_MENU_KEYBOARD)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 دستورات:\n\n"
        "/menu — باز کردن منوی اصلی (دکمه‌ای)\n"
        "/budget <مبلغ> — تنظیم بودجه بازه جاری (هفتگی یا ماهانه)\n"
        "/budget +<مبلغ> یا /budget -<مبلغ> — افزایش/کاهش بودجه فعلی\n"
        "/period monthly یا /period weekly <روز> — انتخاب بازه بودجه\n"
        "/income <مبلغ> [توضیح] — ثبت درآمد\n"
        "/expense <مبلغ> [توضیح] — ثبت هزینه (یا فقط بنویس: نان 50000 فروشگاه رفاه تاریخ 2026-07-10)\n"
        "/balance — باقیمانده بودجه\n"
        "/recalc — محاسبه مجدد بودجه و هزینه‌ها از صفر (برای اطمینان از درستی عددها)\n"
        "/report day|week|month — لیست هزینه‌ها (تاریخ و مبلغ) و جمع بازه\n"
        "/transactions — نمایش تراکنش‌های اخیر برای حذف یا ویرایش\n"
        "/newlist — شروع لیست خرید جدید (بعدش هر آیتم رو یک خط بفرست)\n"
        "/donelist — پایان وارد کردن آیتم‌های لیست\n"
        "/list — نمایش وضعیت لیست خرید فعلی\n"
        "/categories — نمایش دسته‌بندی‌ها\n"
        "/currency <واحد> — تغییر واحد پول (مثلاً EUR، یورو، تومان، $)\n"
        "/invite — گرفتن کد دعوت خانواده\n"
        "/join <کد> — پیوستن به خانواده‌ای دیگر\n"
        "/backup — دریافت نسخه پشتیبان کامل دیتابیس (بودجه، تراکنش‌ها، لیست خرید)\n\n"
        "📷 عکس فاکتور یا 📄 فایل PDF فاکتور بفرست تا خودکار با لیست خریدت تطبیق داده بشه و هزینه‌ها ثبت بشن.",
        reply_markup=MAIN_MENU_KEYBOARD,
    )


async def invite_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    household_id = db.get_user_household(update.effective_user.id)
    if not household_id:
        await update.message.reply_text("اول /start رو بزن.")
        return
    code = db.get_invite_code(household_id)
    await update.message.reply_text(f"کد دعوت خانواده: `{code}`", parse_mode=ParseMode.MARKDOWN)


@require_household
async def backup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id):
    """کل فایل دیتابیس (بودجه، تراکنش‌ها، لیست خرید همه خانواده‌ها) رو به همین چت می‌فرسته
    تا یه نسخه پشتیبان دستی داشته باشی — مستقل از اینکه روی Railway ولوم درست وصل شده یا نه."""
    try:
        with open(db.DB_PATH, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"budget-bot-backup-{date.today().isoformat()}.db",
                caption=(
                    "📦 نسخه پشتیبان کامل دیتابیس. این فایل رو یه جای امن نگه دار.\n"
                    "برای بازگردانی: همین فایل رو به‌جای data/bot.db روی سرور بذار."
                ),
            )
    except FileNotFoundError:
        await update.message.reply_text("فایل دیتابیس پیدا نشد.")


async def join_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("فرمت درست: /join CODE123")
        return
    code = context.args[0]
    user = update.effective_user
    household_id = db.join_household(user.id, user.first_name, code)
    if household_id is None:
        await update.message.reply_text("کد دعوت پیدا نشد. دوباره چک کن.")
        return
    await update.message.reply_text("به خانواده پیوستی! ✅ حالا بودجه و لیست خرید مشترکه.", reply_markup=MAIN_MENU_KEYBOARD)


# ---------------- بودجه و تراکنش‌ها ----------------

def _budget_followup_prompt(household_id, context):
    """بعد از تنظیم بازه بودجه، بلافاصله (همون‌جا، بدون نیاز به رفتن سراغ منوی جدا) مبلغ بودجه رو هم می‌پرسه."""
    context.chat_data["awaiting"] = "budget"
    cur = db.get_currency(household_id)
    current = db.get_budget(household_id)
    label = _period_label(household_id)
    current_line = f"(بودجه فعلی {label}: {fmt(current, cur)})\n" if current else ""
    return (
        f"{current_line}حالا مبلغ بودجه {label} رو بفرست (مثلاً 5000000)"
        " — یا اگه فعلاً نمی‌خوای عوضش کنی، بنویس «-»."
    )


@require_household
async def period_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id):
    if not context.args:
        p = db.get_budget_period(household_id)
        label = _period_label(household_id)
        await update.message.reply_text(
            f"بازه بودجه فعلی: {label}\n\n"
            "برای تغییر:\n"
            "/period monthly — بازه ماهانه\n"
            "/period weekly دوشنبه — بازه هفتگی از یک روز مشخص (یا به‌جای اسم روز، یک تاریخ میلادی بفرست)"
        )
        return
    mode = context.args[0].lower()
    if mode in ("monthly", "ماهانه", "ماه"):
        db.set_budget_period(household_id, "monthly")
        prompt = _budget_followup_prompt(household_id, context)
        await update.message.reply_text(f"✅ بازه بودجه روی «ماهانه» تنظیم شد.\n\n{prompt}")
        return
    if mode in ("weekly", "هفتگی", "هفته"):
        rest = " ".join(context.args[1:])
        if not rest:
            context.chat_data["awaiting"] = "week_start"
            await update.message.reply_text(
                "هفته از چه روزی شروع بشه؟ اسم روز رو بفرست (مثلاً «دوشنبه») یا یک تاریخ میلادی مثل 2026-07-13."
            )
            return
        weekday_idx = categorize.parse_week_start_input(rest)
        if weekday_idx is None:
            await update.message.reply_text("متوجه روز/تاریخ نشدم. مثال: /period weekly دوشنبه")
            return
        db.set_budget_period(household_id, "weekly", week_start_weekday=weekday_idx)
        day_name = db.WEEKDAY_FA_NAMES[weekday_idx]
        prompt = _budget_followup_prompt(household_id, context)
        await update.message.reply_text(
            f"✅ بازه بودجه روی «هفتگی» تنظیم شد؛ هر هفته از روز {day_name} شروع می‌شه.\n\n{prompt}"
        )
        return
    await update.message.reply_text("فرمت درست: /period monthly  یا  /period weekly دوشنبه")


@require_household
async def currency_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id):
    if not context.args:
        cur = db.get_currency(household_id)
        await update.message.reply_text(
            f"واحد پول فعلی: {cur}\n\n"
            "برای تغییر: /currency EUR  یا  /currency یورو  یا هر نماد/نام دلخواه دیگه‌ای مثل £, CHF, ₺"
        )
        return
    raw = " ".join(context.args)
    new_currency = db.set_currency(household_id, raw)
    await update.message.reply_text(f"✅ واحد پول روی «{new_currency}» تنظیم شد.")


def _set_budget_and_reply_text(household_id, amount, cur):
    old = db.get_budget(household_id)
    db.set_budget(household_id, amount)
    label = _period_label(household_id)
    if old:
        return (
            f"✅ بودجه {label} از {fmt(old, cur)} به {fmt(amount, cur)} تغییر کرد "
            "(عدد قبلی به‌طور کامل جایگزین شد، اضافه نشد)."
        )
    return f"✅ بودجه {label} روی {fmt(amount, cur)} تنظیم شد."


def _apply_budget_input_and_reply_text(household_id, raw_text, cur):
    """
    ورودی بودجه را تفسیر می‌کند: عدد ساده = تنظیم مطلق (مثل 5000000)،
    یا با علامت + / - در ابتدا = افزایش/کاهش نسبت به بودجه فعلی (مثل +500000 یا -200000).
    خروجی: متن پاسخ، یا None اگر ورودی نامعتبر بود.
    """
    raw_text = raw_text.strip()
    label = _period_label(household_id)
    if raw_text and raw_text[0] in "+-":
        sign = 1 if raw_text[0] == "+" else -1
        amount, _ = categorize.extract_amount(raw_text[1:])
        if amount is None:
            return None
        new_amount = db.adjust_budget(household_id, sign * amount)
        verb = "اضافه شد" if sign > 0 else "کم شد"
        return (
            f"✅ {fmt(amount, cur)} به بودجه {label} {verb}.\n"
            f"بودجه جدید: {fmt(new_amount, cur)}"
        )
    amount, _ = categorize.extract_amount(raw_text)
    if amount is None:
        return None
    return _set_budget_and_reply_text(household_id, amount, cur)


@require_household
async def budget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id):
    cur = db.get_currency(household_id)
    if not context.args:
        b = db.get_budget(household_id)
        label = _period_label(household_id)
        msg = f"بودجه {label}: {fmt(b, cur)}" if b else "هنوز بودجه‌ای تنظیم نشده."
        await update.message.reply_text(
            msg + "\n\n⚠️ /budget 5000000 → کل بودجه رو با این عدد جایگزین می‌کنه (نه اضافه)."
            "\n/budget +500000 یا /budget -200000 → فقط به بودجه فعلی اضافه/کم می‌کنه."
        )
        return
    raw = " ".join(context.args)
    reply = _apply_budget_input_and_reply_text(household_id, raw, cur)
    if reply is None:
        await update.message.reply_text("مبلغ نامعتبره. مثال: /budget 5000000 یا /budget +500000")
        return
    await update.message.reply_text(reply)


@require_household
async def income_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id):
    text = " ".join(context.args)
    await _register_income(update, household_id, text)


async def _register_income(update, household_id, text):
    cur = db.get_currency(household_id)
    amount, desc = categorize.extract_amount(text)
    if amount is None:
        await update.message.reply_text("فرمت درست: 2000000 حقوق")
        return None
    db.add_transaction(household_id, update.effective_user.id, "income", amount, description=desc or None)
    await update.message.reply_text(f"✅ درآمد {fmt(amount, cur)} ثبت شد.")
    return amount


@require_household
async def expense_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id):
    text = " ".join(context.args)
    await _register_expense(update, household_id, text, source="manual")


async def _register_expense(update, household_id, text, source="manual"):
    cur = db.get_currency(household_id)
    amount, desc, cat, store, tx_date = categorize.parse_free_text_expense_detailed(text, household_id)
    if amount is None:
        await update.message.reply_text(
            "متوجه مبلغ نشدم. مثال: نان 50000  یا  نان 50000 فروشگاه رفاه تاریخ 2026-07-10"
        )
        return None
    tx_id = db.add_transaction(
        household_id, update.effective_user.id, "expense", amount,
        category=cat, description=desc or None, source=source, store=store, tx_date=tx_date,
    )
    bal = db.get_balance(household_id)
    extra = []
    if store:
        extra.append(f"فروشگاه: {store}")
    if tx_date:
        extra.append(f"تاریخ: {tx_date}")
    extra_str = (" — " + " — ".join(extra)) if extra else ""
    await update.message.reply_text(
        f"✅ هزینه ثبت شد: {fmt(amount, cur)} — {desc or '—'} (دسته: {cat}){extra_str}\n"
        f"باقیمانده بودجه {_period_label(household_id)}: {fmt(bal['remaining'], cur)}"
    )
    return tx_id


# ---------------- ثبت هزینه مرحله‌به‌مرحله (وقتی متن آزاد قابل اعتماد نیست) ----------------

SKIP_WORDS = {"-", "--", "رد", "رد کن", "skip", "ندارد", "هیچی"}


def _date_choice_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("امروز", callback_data="exp:date:today"),
            InlineKeyboardButton("دیروز", callback_data="exp:date:yesterday"),
        ],
        [InlineKeyboardButton("📅 تاریخ دیگه (بنویس)", callback_data="exp:date:manual")],
    ])


def _category_choice_keyboard(context, household_id, callback_prefix):
    """دکمه‌های همه دسته‌بندی‌ها (خانواده + سراسری، از جمله فروشگاه‌های اضافه‌شده) به‌علاوه یک گزینه
    «تشخیص خودکار». لیست نام دسته‌ها رو تو chat_data نگه می‌داره تا callback_data فقط ایندکس بفرسته."""
    names = [c["name"] for c in db.get_categories(household_id)]
    context.chat_data["expense_categories_choice"] = names
    buttons = [[InlineKeyboardButton("🤖 تشخیص خودکار", callback_data=f"{callback_prefix}:auto")]]
    row = []
    for i, name in enumerate(names):
        row.append(InlineKeyboardButton(name, callback_data=f"{callback_prefix}:{i}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


async def _ask_expense_category(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id, via_callback: bool):
    kb = _category_choice_keyboard(context, household_id, "excat")
    text = "🏷 دسته‌بندی این هزینه رو انتخاب کن (یا بذار خودش از روی توضیح تشخیص بده):"
    if via_callback:
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)


async def _commit_expense_draft(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id, via_callback: bool):
    draft = context.chat_data.pop("expense_draft", {})
    context.chat_data.pop("expense_categories_choice", None)
    context.chat_data.pop("awaiting", None)
    cur = db.get_currency(household_id)
    amount = draft.get("amount")
    desc = draft.get("description")
    store = draft.get("store")
    tx_date = draft.get("tx_date") or date.today().isoformat()
    cat = draft.get("category") or (categorize.categorize(desc, household_id) if desc else "متفرقه")
    user_id = update.effective_user.id

    db.add_transaction(
        household_id, user_id, "expense", amount,
        category=cat, description=desc, store=store, tx_date=tx_date, source="manual",
    )
    bal = db.get_balance(household_id)
    extra = []
    if store:
        extra.append(f"فروشگاه: {store}")
    extra.append(f"تاریخ: {tx_date}")
    extra_str = " — " + " — ".join(extra)
    text = (
        f"✅ هزینه ثبت شد: {fmt(amount, cur)} — {desc or '—'} (دسته: {cat}){extra_str}\n"
        f"باقیمانده بودجه {_period_label(household_id)}: {fmt(bal['remaining'], cur)}"
    )
    if via_callback:
        await update.callback_query.edit_message_text(text)
    else:
        await update.message.reply_text(text)


async def exp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    household_id = db.get_user_household(update.effective_user.id)
    if household_id is None:
        await query.edit_message_text("اول /start رو بزن.")
        return

    parts = query.data.split(":")  # exp:action[:sub]
    action = parts[1] if len(parts) > 1 else ""

    if action == "quickinfo":
        await query.edit_message_text(
            "بنویس: مبلغ و توضیح، مثلاً: نان 50000\n"
            "اگه بخوای فروشگاه/تاریخ هم بگی: نان 50000 فروشگاه رفاه تاریخ 2026-07-10"
        )

    elif action == "start":
        context.chat_data["expense_draft"] = {}
        context.chat_data["awaiting"] = "expense_amount"
        await query.edit_message_text("مبلغ هزینه رو بفرست (فقط عدد، مثلاً 50000 یا 9.97):")

    elif action == "date" and len(parts) > 2:
        choice = parts[2]
        draft = context.chat_data.setdefault("expense_draft", {})
        if "amount" not in draft:
            await query.edit_message_text("این فرآیند منقضی شده. دوباره از ➖ ثبت هزینه شروع کن.")
            return
        if choice == "today":
            draft["tx_date"] = date.today().isoformat()
            await _ask_expense_category(update, context, household_id, via_callback=True)
        elif choice == "yesterday":
            draft["tx_date"] = (date.today() - timedelta(days=1)).isoformat()
            await _ask_expense_category(update, context, household_id, via_callback=True)
        elif choice == "manual":
            context.chat_data["awaiting"] = "expense_date_manual"
            await query.edit_message_text("تاریخ خرید رو بفرست، مثلاً 2026-07-15 یا 15-07-2026:")


async def excat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """انتخاب دسته‌بندی از دکمه‌ها، آخرین قدم ثبت هزینه مرحله‌به‌مرحله (بعد از تاریخ)."""
    query = update.callback_query
    await query.answer()
    household_id = db.get_user_household(update.effective_user.id)
    if household_id is None:
        await query.edit_message_text("اول /start رو بزن.")
        return

    draft = context.chat_data.get("expense_draft")
    if not draft or "amount" not in draft:
        await query.edit_message_text("این فرآیند منقضی شده. دوباره از ➖ ثبت هزینه شروع کن.")
        return

    choice = query.data.split(":", 1)[1] if ":" in query.data else ""
    if choice != "auto":
        names = context.chat_data.get("expense_categories_choice", [])
        try:
            idx = int(choice)
        except ValueError:
            idx = -1
        if 0 <= idx < len(names):
            draft["category"] = names[idx]

    await _commit_expense_draft(update, context, household_id, via_callback=True)


def _balance_text(household_id):
    cur = db.get_currency(household_id)
    b = db.get_balance(household_id)
    if not b["budget"]:
        return "هنوز بودجه‌ای تنظیم نشده. از ⚙️ تنظیمات یا /budget بودجه رو تنظیم کن."
    label = _period_label(household_id)
    period_end_str = b["period_end"].strftime("%Y-%m-%d")
    return (
        f"💰 وضعیت بودجه {label}\n\n"
        f"بودجه: {fmt(b['budget'], cur)}\n"
        f"درآمد اضافه‌شده: {fmt(b['period_income'], cur)}\n"
        f"هزینه تا الان: {fmt(b['period_expense'], cur)}\n"
        f"باقیمانده: {fmt(b['remaining'], cur)}\n\n"
        f"هزینه امروز: {fmt(b['day_expense'], cur)}\n\n"
        f"📅 {b['days_left_in_period']} روز تا پایان این بازه ({period_end_str})"
    )


@require_household
async def balance_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id):
    await update.message.reply_text(_balance_text(household_id))


def _recalc_text(household_id):
    """
    محاسبه دوباره از صفر: بودجه، درآمد و همه هزینه‌های بازه جاری مستقیم از دیتابیس جمع زده می‌شوند
    (چیزی کش/ذخیره‌شده جداگونه‌ای برای «باقیمانده» وجود نداره که خراب بشه — هر بار از نو ساخته می‌شه).
    این گزینه برای اطمینان‌خاطر است، برای وقتی حس می‌کنی عددها به‌هم ریخته.
    """
    return "🔄 محاسبه مجدد انجام شد — همه هزینه‌ها و درآمدهای بازه جاری از نو جمع زده شدند:\n\n" + _balance_text(household_id)


@require_household
async def recalc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id):
    await update.message.reply_text(_recalc_text(household_id))


def _report_text(household_id, period):
    """گزارش ساده: هر فاکتور/هزینه یک ردیف شماره‌دار (تاریخ، برچسب کوتاه، مبلغ) — بدون جزئیات
    ردیف‌به‌ردیف فاکتور و بدون درصد — به‌علاوه جمع بازه (و جمع امروز)."""
    cur = db.get_currency(household_id)
    period_map = {"day": "day", "روز": "day", "week": "week", "هفته": "week", "month": "month", "ماه": "month"}
    period = period_map.get(period, "month")
    r = db.get_report(household_id, period)
    title = {"day": "امروز", "week": "این هفته", "month": "این ماه"}[period]
    if not r["groups"]:
        return f"هیچ هزینه‌ای برای {title} ثبت نشده."

    lines = [f"🧾 لیست هزینه‌ها — {title}\n"]
    if period == "day":
        for i, g in enumerate(r["groups"], 1):
            lines.append(f"{i}. {g['label']} — {fmt(g['amount'], cur)}")
    else:
        current_date, idx = None, 0
        for g in r["groups"]:
            if g["tx_date"] != current_date:
                if current_date is not None:
                    lines.append("")
                lines.append(f"📅 {g['tx_date']}")
                current_date, idx = g["tx_date"], 0
            idx += 1
            lines.append(f"  {idx}. {g['label']} — {fmt(g['amount'], cur)}")

    lines.append(f"\nجمع {title}: {fmt(r['total'], cur)}")
    if period != "day":
        lines.append(f"هزینه امروز: {fmt(r['today_total'], cur)}")
    return "\n".join(lines)


@require_household
async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id):
    period = context.args[0] if context.args else "month"
    await update.message.reply_text(_report_text(household_id, period))


def _categories_text(household_id):
    cats = db.get_categories(household_id)
    lines = ["📁 دسته‌بندی‌ها:\n"]
    for c in cats:
        lines.append(f"• {c['name']}")
    lines.append("\nبرای اضافه‌کردن دسته جدید با کلمات کلیدی، به پشتیبان بگو یا کد رو ویرایش کن.")
    return "\n".join(lines)


@require_household
async def categories_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id):
    await update.message.reply_text(_categories_text(household_id))


# ---------------- لیست خرید ----------------

@require_household
async def newlist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id):
    list_id = db.create_shopping_list(household_id)
    context.chat_data["collecting_list_id"] = list_id
    await update.message.reply_text(
        "🛒 لیست خرید جدید شروع شد.\n"
        "هر آیتم رو تو یک خط بفرست (می‌تونی چند خط با هم هم بفرستی).\n"
        "وقتی تموم شد، /donelist رو بزن."
    )


@require_household
async def donelist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id):
    list_id = context.chat_data.pop("collecting_list_id", None)
    if not list_id:
        await update.message.reply_text("لیستی در حال تکمیل نیست. با /newlist شروع کن.")
        return
    items = db.get_list_items(list_id)
    await update.message.reply_text(
        f"✅ لیست با {len(items)} آیتم ثبت شد.\n"
        "هر وقت خرید کردی، عکس فاکتور رو بفرست یا با /list آیتم‌ها رو دستی تیک بزن."
    )


def _list_keyboard(list_id, items):
    buttons = []
    for it in items:
        mark = "✅" if it["bought"] else "◻️"
        buttons.append([InlineKeyboardButton(f"{mark} {it['item_name']}", callback_data=f"toggle:{list_id}:{it['id']}")])
    buttons.append([InlineKeyboardButton("➕ لیست جدید", callback_data="m:list:new")])
    return InlineKeyboardMarkup(buttons)


@require_household
async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id):
    active = db.get_active_list(household_id)
    if not active:
        await update.message.reply_text(
            "لیست فعالی وجود نداره.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ لیست جدید", callback_data="m:list:new")]]),
        )
        return
    items = db.get_list_items(active["id"])
    if not items:
        await update.message.reply_text("این لیست هنوز آیتمی نداره.")
        return
    remaining = [i for i in items if not i["bought"]]
    text = f"🛒 {active['name']} — {len(remaining)} مورد باقی‌مانده از {len(items)}\nروی هرکدوم بزن تا وضعیتش عوض بشه:"
    await update.message.reply_text(text, reply_markup=_list_keyboard(active["id"], items))


async def toggle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, list_id, item_id = query.data.split(":")
    list_id, item_id = int(list_id), int(item_id)
    items = db.get_list_items(list_id)
    item = next((i for i in items if i["id"] == item_id), None)
    if not item:
        return
    db.mark_item_bought(item_id, bought=not item["bought"])
    items = db.get_list_items(list_id)
    remaining = [i for i in items if not i["bought"]]
    active = db.get_active_list(db.get_user_household(update.effective_user.id))
    name = active["name"] if active else "لیست خرید"
    text = f"🛒 {name} — {len(remaining)} مورد باقی‌مانده از {len(items)}\nروی هرکدوم بزن تا وضعیتش عوض بشه:"
    await query.edit_message_text(text, reply_markup=_list_keyboard(list_id, items))


# ---------------- مدیریت تراکنش‌ها (حذف/ویرایش هزینه یا درآمد) ----------------

def _tx_summary(tx, cur):
    type_icon = "➖" if tx["type"] == "expense" else "➕"
    desc = tx["description"] or "—"
    parts = [f"{type_icon} {fmt(tx['amount'], cur)} — {desc}"]
    if tx.get("store"):
        parts.append(f"({tx['store']})")
    return " ".join(parts)


def _tx_list_text_and_keyboard(household_id):
    cur = db.get_currency(household_id)
    txs = db.get_recent_transactions(household_id, limit=10)
    if not txs:
        return "هنوز هیچ تراکنشی ثبت نشده.", None
    buttons = []
    for tx in txs:
        label = f"{tx['tx_date']} — {_tx_summary(tx, cur)}"
        if len(label) > 60:
            label = label[:57] + "..."
        buttons.append([InlineKeyboardButton(label, callback_data=f"tx:menu:{tx['id']}")])
    text = "🧾 ۱۰ تراکنش اخیر — روی هرکدوم بزن تا حذف/ویرایش کنی:"
    return text, InlineKeyboardMarkup(buttons)


def _tx_action_keyboard(tx):
    tx_id = tx["id"]
    rows = [[InlineKeyboardButton("✏️ ویرایش مبلغ", callback_data=f"tx:editamt:{tx_id}")]]
    if tx.get("receipt_id"):
        rows.append([InlineKeyboardButton("🗑 حذف فقط این آیتم", callback_data=f"tx:del:{tx_id}")])
        rows.append([InlineKeyboardButton("☑️ حذف چندتایی از این فاکتور", callback_data=f"tx:bulkdel:{tx['receipt_id']}")])
        rows.append([InlineKeyboardButton("🗑🧾 حذف کل این فاکتور (همه اقلام)", callback_data=f"tx:delreceipt:{tx['receipt_id']}")])
        rows.append([InlineKeyboardButton("🏪 تغییر/افزودن فروشگاه این فاکتور", callback_data=f"tx:setstore:{tx['receipt_id']}")])
    else:
        rows.append([InlineKeyboardButton("🗑 حذف این تراکنش", callback_data=f"tx:del:{tx_id}")])
    rows.append([InlineKeyboardButton("🔙 بازگشت به لیست", callback_data="tx:list")])
    return InlineKeyboardMarkup(rows)


def _bulk_delete_text_and_keyboard(items, selected, cur):
    """چک‌لیست تیک‌زدنی برای انتخاب چند آیتم از یک فاکتور جهت حذف هم‌زمان (نه کل فاکتور، نه فقط یک آیتم)."""
    receipt_id = items[0]["receipt_id"]
    buttons = []
    for it in items:
        mark = "☑️" if it["id"] in selected else "◻️"
        label = f"{mark} {it['description'] or '—'} — {fmt(it['amount'], cur)}"
        if len(label) > 60:
            label = label[:57] + "..."
        buttons.append([InlineKeyboardButton(label, callback_data=f"tx:bulktoggle:{receipt_id}:{it['id']}")])
    buttons.append([InlineKeyboardButton(f"🗑 حذف انتخاب‌شده‌ها ({len(selected)})", callback_data=f"tx:bulkgo:{receipt_id}")])
    buttons.append([InlineKeyboardButton("🔙 بازگشت", callback_data=f"tx:menu:{items[0]['id']}")])
    text = "☑️ آیتم‌هایی که می‌خوای حذف کنی رو تیک بزن (می‌تونی چندتا انتخاب کنی)، بعد «حذف انتخاب‌شده‌ها» رو بزن:"
    return text, InlineKeyboardMarkup(buttons)


def _tx_receipt_store_keyboard(receipt_id):
    rows = [[InlineKeyboardButton(f"🏪 {name}", callback_data=f"tx:setstoreto:{receipt_id}:{name}")] for name in RECEIPT_STORE_OPTIONS]
    rows.append([InlineKeyboardButton("🔙 انصراف", callback_data="tx:list")])
    return InlineKeyboardMarkup(rows)


def _tx_confirm_delete_keyboard(tx_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"tx:delconfirm:{tx_id}"),
        InlineKeyboardButton("❌ انصراف", callback_data=f"tx:menu:{tx_id}"),
    ]])


def _receipt_confirm_delete_keyboard(receipt_id):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ بله، کل فاکتور حذف بشه", callback_data=f"tx:delreceiptconfirm:{receipt_id}"),
        InlineKeyboardButton("❌ انصراف", callback_data="tx:list"),
    ]])


@require_household
async def transactions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id):
    text, kb = _tx_list_text_and_keyboard(household_id)
    await update.message.reply_text(text, reply_markup=kb)


async def tx_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    household_id = db.get_user_household(update.effective_user.id)
    if household_id is None:
        await query.edit_message_text("اول /start رو بزن.")
        return

    parts = query.data.split(":")  # tx:action[:id]
    action = parts[1] if len(parts) > 1 else ""
    cur = db.get_currency(household_id)

    if action == "list":
        text, kb = _tx_list_text_and_keyboard(household_id)
        await query.edit_message_text(text, reply_markup=kb)
        return

    if action == "delreceipt":
        receipt_id = parts[2] if len(parts) > 2 else None
        items = db.get_receipt_transactions(household_id, receipt_id) if receipt_id else []
        if not items:
            await query.edit_message_text("این فاکتور دیگه پیدا نشد (شاید قبلاً حذف شده).")
            return
        total = sum(i["amount"] for i in items)
        lines = "\n".join(f"• {i['description'] or '—'} — {fmt(i['amount'], cur)}" for i in items)
        text = f"❌ کل این فاکتور ({len(items)} آیتم، جمعاً {fmt(total, cur)}) حذف بشه؟\n\n{lines}"
        await query.edit_message_text(text, reply_markup=_receipt_confirm_delete_keyboard(receipt_id))
        return

    if action == "delreceiptconfirm":
        receipt_id = parts[2] if len(parts) > 2 else None
        count = db.delete_receipt(household_id, receipt_id) if receipt_id else 0
        await query.edit_message_text(f"🗑 کل فاکتور حذف شد ({count} آیتم).")
        text, kb = _tx_list_text_and_keyboard(household_id)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=kb)
        return

    if action == "bulkdel":
        receipt_id = parts[2] if len(parts) > 2 else None
        items = db.get_receipt_transactions(household_id, receipt_id) if receipt_id else []
        if not items:
            await query.edit_message_text("این فاکتور دیگه پیدا نشد (شاید قبلاً حذف شده).")
            return
        selected = context.chat_data.setdefault("bulk_delete_selected", {}).setdefault(receipt_id, set())
        text, kb = _bulk_delete_text_and_keyboard(items, selected, cur)
        await query.edit_message_text(text, reply_markup=kb)
        return

    if action == "bulktoggle":
        receipt_id = parts[2] if len(parts) > 2 else None
        item_tx_id = int(parts[3]) if len(parts) > 3 else None
        items = db.get_receipt_transactions(household_id, receipt_id) if receipt_id else []
        if not items:
            await query.edit_message_text("این فاکتور دیگه پیدا نشد (شاید قبلاً حذف شده).")
            return
        selected = context.chat_data.setdefault("bulk_delete_selected", {}).setdefault(receipt_id, set())
        if item_tx_id in selected:
            selected.discard(item_tx_id)
        else:
            selected.add(item_tx_id)
        text, kb = _bulk_delete_text_and_keyboard(items, selected, cur)
        await query.edit_message_text(text, reply_markup=kb)
        return

    if action == "bulkgo":
        receipt_id = parts[2] if len(parts) > 2 else None
        items = db.get_receipt_transactions(household_id, receipt_id) if receipt_id else []
        selected = context.chat_data.get("bulk_delete_selected", {}).get(receipt_id, set())
        chosen = [i for i in items if i["id"] in selected]
        if not chosen:
            await query.answer("چیزی انتخاب نشده.", show_alert=True)
            return
        total = sum(i["amount"] for i in chosen)
        lines = "\n".join(f"• {i['description'] or '—'} — {fmt(i['amount'], cur)}" for i in chosen)
        text = f"❌ این {len(chosen)} آیتم (جمعاً {fmt(total, cur)}) حذف بشن؟\n\n{lines}"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"tx:bulkconfirm:{receipt_id}"),
            InlineKeyboardButton("❌ انصراف", callback_data=f"tx:bulkdel:{receipt_id}"),
        ]])
        await query.edit_message_text(text, reply_markup=kb)
        return

    if action == "bulkconfirm":
        receipt_id = parts[2] if len(parts) > 2 else None
        selected = context.chat_data.get("bulk_delete_selected", {}).pop(receipt_id, set())
        count = 0
        for del_tx_id in selected:
            db.delete_transaction(del_tx_id)
            count += 1
        await query.edit_message_text(f"🗑 {count} آیتم از این فاکتور حذف شد.")
        text, kb = _tx_list_text_and_keyboard(household_id)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=kb)
        return

    if action == "setstore":
        receipt_id = parts[2] if len(parts) > 2 else None
        items = db.get_receipt_transactions(household_id, receipt_id) if receipt_id else []
        if not items:
            await query.edit_message_text("این فاکتور دیگه پیدا نشد (شاید قبلاً حذف شده).")
            return
        current_store = items[0].get("store")
        note = f"\nفروشگاه فعلی: {current_store}" if current_store else "\nفروشگاهی برای این فاکتور ثبت نشده."
        await query.edit_message_text(
            f"این فاکتور ({len(items)} آیتم) رو به کدوم فروشگاه نسبت بدم؟{note}",
            reply_markup=_tx_receipt_store_keyboard(receipt_id),
        )
        return

    if action == "setstoreto":
        receipt_id = parts[2] if len(parts) > 2 else None
        store_name = parts[3] if len(parts) > 3 else None
        items = db.get_receipt_transactions(household_id, receipt_id) if receipt_id else []
        if not items or not store_name:
            await query.edit_message_text("این فاکتور دیگه پیدا نشد (شاید قبلاً حذف شده).")
            return
        new_cat = db.RECEIPT_STORE_CATEGORY.get(store_name, store_name)
        for it in items:
            db.update_transaction(it["id"], store=store_name, category=new_cat)
        await query.edit_message_text(
            f"✅ فروشگاه این فاکتور ({len(items)} آیتم) روی «{store_name}» تنظیم شد؛ دسته‌شون هم به «{new_cat}» تغییر کرد."
        )
        text, kb = _tx_list_text_and_keyboard(household_id)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=kb)
        return

    tx_id = int(parts[2]) if len(parts) > 2 else None
    tx = db.get_transaction(tx_id) if tx_id else None
    if not tx or tx["household_id"] != household_id:
        await query.edit_message_text("این تراکنش دیگه پیدا نشد (شاید قبلاً حذف شده).")
        return

    if action == "menu":
        note = "\n(این آیتم بخشی از یک فاکتور اسکن‌شده‌ست — می‌تونی فقط همینو حذف کنی یا کل فاکتور رو.)" if tx.get("receipt_id") else ""
        text = f"{tx['tx_date']} — {_tx_summary(tx, cur)}\nدسته: {tx.get('category') or '—'}{note}"
        await query.edit_message_text(text, reply_markup=_tx_action_keyboard(tx))

    elif action == "del":
        text = f"❌ حذف بشه؟\n\n{tx['tx_date']} — {_tx_summary(tx, cur)}"
        await query.edit_message_text(text, reply_markup=_tx_confirm_delete_keyboard(tx_id))

    elif action == "delconfirm":
        db.delete_transaction(tx_id)
        await query.edit_message_text("🗑 حذف شد.")
        text, kb = _tx_list_text_and_keyboard(household_id)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=text, reply_markup=kb)

    elif action == "editamt":
        context.chat_data["awaiting"] = "edit_tx_amount"
        context.chat_data["edit_tx_id"] = tx_id
        await query.edit_message_text(
            f"مبلغ جدید رو بفرست (مبلغ فعلی: {fmt(tx['amount'], cur)}):"
        )


# ---------------- منوی دکمه‌ای (کیبورد پایین + دکمه‌های شیشه‌ای) ----------------

MAIN_MENU_LABELS = {BTN_BALANCE, BTN_EXPENSE, BTN_INCOME, BTN_LIST, BTN_REPORT, BTN_SETTINGS, BTN_HELP}


async def menu_button_router(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id, text):
    """پیام‌هایی که دقیقاً متن یکی از دکمه‌های منوی اصلی هستند را مسیریابی می‌کند.
    اگر متن هیچ‌کدام از دکمه‌ها نباشد (مثلاً پاسخ کاربر به یک سؤال قبلی)، دست‌نخورده False برمی‌گرداند
    تا حالت awaiting (مثل انتظار برای مبلغ درآمد) خراب نشود."""
    if text not in MAIN_MENU_LABELS:
        return False

    context.chat_data.pop("awaiting", None)

    if text == BTN_BALANCE:
        await update.message.reply_text(_balance_text(household_id))
        return True

    if text == BTN_EXPENSE:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔤 ثبت سریع (یک خط)", callback_data="exp:quickinfo")],
            [InlineKeyboardButton("📝 ثبت با جزئیات (مرحله‌به‌مرحله)", callback_data="exp:start")],
        ])
        await update.message.reply_text("چطوری می‌خوای هزینه رو ثبت کنی؟", reply_markup=kb)
        return True

    if text == BTN_INCOME:
        context.chat_data["awaiting"] = "income"
        await update.message.reply_text("مبلغ و توضیح درآمد رو بفرست، مثلاً: 2000000 حقوق")
        return True

    if text == BTN_LIST:
        active = db.get_active_list(household_id)
        if not active:
            await update.message.reply_text(
                "لیست فعالی وجود نداره.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ لیست جدید", callback_data="m:list:new")]]),
            )
            return True
        items = db.get_list_items(active["id"])
        if not items:
            await update.message.reply_text("این لیست هنوز آیتمی نداره.")
            return True
        remaining = [i for i in items if not i["bought"]]
        msg = f"🛒 {active['name']} — {len(remaining)} مورد باقی‌مانده از {len(items)}\nروی هرکدوم بزن تا وضعیتش عوض بشه:"
        await update.message.reply_text(msg, reply_markup=_list_keyboard(active["id"], items))
        return True

    if text == BTN_REPORT:
        await update.message.reply_text("گزارش کدوم بازه رو می‌خوای؟", reply_markup=_report_period_keyboard())
        return True

    if text == BTN_SETTINGS:
        cur = db.get_currency(household_id)
        label = _period_label(household_id)
        await update.message.reply_text(
            f"⚙️ تنظیمات\nواحد پول: {cur}\nبازه بودجه: {label}",
            reply_markup=_settings_keyboard(),
        )
        return True

    if text == BTN_HELP:
        await help_cmd(update, context)
        return True

    return False


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    household_id = db.get_user_household(update.effective_user.id)
    if household_id is None:
        await query.edit_message_text("اول /start رو بزن.")
        return

    data = query.data  # مثل m:budget یا m:report:month
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "budget":
        context.chat_data["awaiting"] = "budget"
        label = _period_label(household_id)
        cur = db.get_currency(household_id)
        current = db.get_budget(household_id)
        current_line = f"بودجه فعلی {label}: {fmt(current, cur)}\n\n" if current else ""
        await query.edit_message_text(
            f"{current_line}"
            f"⚠️ اگه یه عدد ساده بفرستی (مثلاً 5000000)، بودجه {label} کاملاً با همون عدد جایگزین می‌شه (نه اضافه).\n"
            "اگه فقط می‌خوای به بودجه فعلی اضافه/کم کنی، با علامت +/- بفرست (مثلاً +500000 یا -200000)."
        )

    elif action == "period" and len(parts) == 2:
        p = db.get_budget_period(household_id)
        current = "ماهانه" if p["period_type"] == "monthly" else "هفتگی"
        await query.edit_message_text(
            f"بازه بودجه فعلی: {current}\nکدوم رو می‌خوای؟",
            reply_markup=_period_choice_keyboard(),
        )

    elif action == "period" and len(parts) > 2 and parts[2] == "monthly":
        db.set_budget_period(household_id, "monthly")
        prompt = _budget_followup_prompt(household_id, context)
        await query.edit_message_text(f"✅ بازه بودجه روی «ماهانه» تنظیم شد.\n\n{prompt}")

    elif action == "period" and len(parts) > 2 and parts[2] == "weekly":
        context.chat_data["awaiting"] = "week_start"
        await query.edit_message_text(
            "هفته از چه روزی شروع بشه؟\n"
            "ساده‌ترین راه: اسم روز رو بفرست، مثلاً «دوشنبه».\n"
            "یا اگه می‌خوای با تاریخ مشخص کنی، یک تاریخ میلادی از همون روز هفته بفرست "
            "(فرمت: 2026-07-13) تا روز هفته‌اش رو خودم پیدا کنم."
        )

    elif action == "currency":
        context.chat_data["awaiting"] = "currency"
        await query.edit_message_text("واحد پول جدید رو بفرست، مثلاً: EUR یا یورو یا $")

    elif action == "categories":
        await query.edit_message_text(_categories_text(household_id))

    elif action == "invite":
        code = db.get_invite_code(household_id)
        await query.edit_message_text(f"کد دعوت خانواده: {code}")

    elif action == "recalc":
        await query.edit_message_text(_recalc_text(household_id), reply_markup=_settings_keyboard())

    elif action == "report" and len(parts) > 2:
        period = parts[2]
        await query.edit_message_text(_report_text(household_id, period), reply_markup=_report_period_keyboard())

    elif action == "list" and len(parts) > 2 and parts[2] == "new":
        list_id = db.create_shopping_list(household_id)
        context.chat_data["collecting_list_id"] = list_id
        await query.edit_message_text(
            "🛒 لیست خرید جدید شروع شد.\n"
            "هر آیتم رو تو یک خط بفرست. وقتی تموم شد، /donelist رو بزن."
        )


# ---------------- پیام آزاد و عکس فاکتور ----------------

@require_household
async def free_text(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id):
    text = update.message.text.strip()

    # حالت ۱: در حال تکمیل یک لیست خرید هستیم
    list_id = context.chat_data.get("collecting_list_id")
    if list_id:
        lines = [l for l in text.splitlines() if l.strip()]
        db.add_list_items(list_id, lines)
        await update.message.reply_text(f"➕ {len(lines)} آیتم به لیست اضافه شد. بازم بفرست یا /donelist بزن.")
        return

    # حالت ۲: یکی از دکمه‌های منوی اصلی زده شده
    if await menu_button_router(update, context, household_id, text):
        return

    # حالت ۳: منتظر یک مقدار خاص هستیم (بعد از زدن دکمه‌ای از تنظیمات/منو)
    awaiting = context.chat_data.pop("awaiting", None)
    if awaiting == "income":
        await _register_income(update, household_id, text)
        return
    if awaiting == "budget":
        if text.strip().lower() in SKIP_WORDS:
            await update.message.reply_text("باشه، بودجه فعلی دست‌نخورده موند.")
            return
        cur = db.get_currency(household_id)
        reply = _apply_budget_input_and_reply_text(household_id, text, cur)
        if reply is None:
            await update.message.reply_text(
                "مبلغ نامعتبره. یه عدد بفرست (مثلاً 5000000) یا با علامت +/- برای افزایش/کاهش (مثلاً +500000)."
            )
            context.chat_data["awaiting"] = "budget"
            return
        await update.message.reply_text(reply)
        return
    if awaiting == "currency":
        new_currency = db.set_currency(household_id, text)
        await update.message.reply_text(f"✅ واحد پول روی «{new_currency}» تنظیم شد.")
        return
    if awaiting == "week_start":
        weekday_idx = categorize.parse_week_start_input(text)
        if weekday_idx is None:
            await update.message.reply_text(
                "متوجه نشدم. اسم روز هفته رو بفرست (مثلاً «دوشنبه») یا یک تاریخ میلادی مثل 2026-07-13."
            )
            context.chat_data["awaiting"] = "week_start"
            return
        db.set_budget_period(household_id, "weekly", week_start_weekday=weekday_idx)
        day_name = db.WEEKDAY_FA_NAMES[weekday_idx]
        prompt = _budget_followup_prompt(household_id, context)
        await update.message.reply_text(
            f"✅ بازه بودجه روی «هفتگی» تنظیم شد؛ هر هفته از روز {day_name} شروع می‌شه.\n\n{prompt}"
        )
        return
    if awaiting == "edit_tx_amount":
        tx_id = context.chat_data.pop("edit_tx_id", None)
        cur = db.get_currency(household_id)
        amount, _ = categorize.extract_amount(text)
        if amount is None or not tx_id:
            await update.message.reply_text("مبلغ نامعتبره. یه عدد بفرست، مثلاً: 60000")
            if tx_id:
                context.chat_data["awaiting"] = "edit_tx_amount"
                context.chat_data["edit_tx_id"] = tx_id
            return
        db.update_transaction(tx_id, amount=amount)
        await update.message.reply_text(f"✅ مبلغ تراکنش به {fmt(amount, cur)} به‌روزرسانی شد.")
        return
    if awaiting == "expense_amount":
        amount, _ = categorize.extract_amount(text)
        if amount is None:
            await update.message.reply_text("مبلغ نامعتبره. یه عدد بفرست، مثلاً 50000 یا 9.97")
            context.chat_data["awaiting"] = "expense_amount"
            return
        context.chat_data.setdefault("expense_draft", {})["amount"] = amount
        context.chat_data["awaiting"] = "expense_desc"
        await update.message.reply_text("توضیح کوتاه (مثلاً اسم کالا) رو بفرست، یا بنویس - برای رد کردن:")
        return
    if awaiting == "expense_desc":
        draft = context.chat_data.setdefault("expense_draft", {})
        draft["description"] = None if text.strip().lower() in SKIP_WORDS else text.strip()
        context.chat_data["awaiting"] = "expense_store"
        await update.message.reply_text("اسم فروشگاه رو بفرست، یا بنویس - برای رد کردن:")
        return
    if awaiting == "expense_store":
        draft = context.chat_data.setdefault("expense_draft", {})
        draft["store"] = None if text.strip().lower() in SKIP_WORDS else text.strip()
        # هم دکمه می‌ذاریم، هم اگه خودش مستقیم یه تاریخ تایپ کنه قبول می‌کنیم
        context.chat_data["awaiting"] = "expense_date_manual"
        await update.message.reply_text(
            "تاریخ خرید کِی بوده؟ دکمه بزن، یا خودت یه تاریخ بنویس (مثلاً 2026-07-15 یا 15-07-2026):",
            reply_markup=_date_choice_keyboard(),
        )
        return
    if awaiting == "expense_date_manual":
        draft = context.chat_data.get("expense_draft")
        if not draft or "amount" not in draft:
            await update.message.reply_text("این فرآیند منقضی شده. دوباره از ➖ ثبت هزینه شروع کن.")
            return
        tx_date = categorize.parse_simple_date(text)
        if tx_date is None:
            await update.message.reply_text("تاریخ نامعتبره. مثال درست: 2026-07-15 یا 15-07-2026")
            context.chat_data["awaiting"] = "expense_date_manual"
            return
        draft["tx_date"] = tx_date
        await _ask_expense_category(update, context, household_id, via_callback=False)
        return

    # حالت ۴ (پیش‌فرض): پیام را به‌عنوان هزینه ثبت کن
    await _register_expense(update, household_id, text, source="manual")


RECEIPT_STORE_OPTIONS = ["Mercadona", "Consum", "Lidl"]


def _receipt_store_keyboard():
    rows = [[InlineKeyboardButton(f"🏪 {name}", callback_data=f"rcpt:setstore:{name}")] for name in RECEIPT_STORE_OPTIONS]
    rows.append([InlineKeyboardButton("🔙 بازگشت (بدون تغییر)", callback_data="rcpt:back")])
    return InlineKeyboardMarkup(rows)


def _receipt_preview_text_and_keyboard(cur, lines, note=None, store=None):
    """پیش‌نمایش آیتم‌های استخراج‌شده از فاکتور، قبل از ثبت نهایی — با دکمه حذف روی هر آیتم اشتباه
    و یک دکمه برای انتخاب فروشگاه (که دسته‌بندی کل فاکتور رو هم مشخص می‌کنه)."""
    body = ["🧾 آیتم‌های تشخیص داده‌شده از فاکتور — قبل از ثبت بررسی کن:"]
    if note:
        body.append(note)
    body.append(f"🏪 فروشگاه: {store}" if store else "🏪 فروشگاه: انتخاب نشده")
    body.append("")
    total = 0.0
    buttons = []
    for idx, rl in enumerate(lines):
        body.append(f"• {rl['name']} — {fmt(rl['amount'], cur)}")
        total += rl["amount"]
        label = f"❌ {rl['name'][:28]} — {fmt(rl['amount'], cur)}"
        buttons.append([InlineKeyboardButton(label, callback_data=f"rcpt:rm:{idx}")])
    body.append(f"\nجمع فعلی: {fmt(total, cur)}")
    body.append("\nاگه ردیفی اشتباه تشخیص داده شده، با دکمه‌ش حذفش کن؛ بعد «تایید و ثبت» رو بزن.")
    buttons.append([InlineKeyboardButton("🏪 انتخاب/تغییر فروشگاه", callback_data="rcpt:store")])
    buttons.append([
        InlineKeyboardButton("✅ تایید و ثبت", callback_data="rcpt:confirm"),
        InlineKeyboardButton("🚫 لغو کامل", callback_data="rcpt:cancel"),
    ])
    return "\n".join(body), InlineKeyboardMarkup(buttons)


async def _process_receipt_lines(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id, receipt_lines, note: str = None):
    """
    یک لیست از پیش استخراج‌شده از آیتم‌های فاکتور را (چه از OCR، چه از لایه متن PDF) به‌صورت پیش‌نویس
    نگه می‌دارد و برای بررسی/تایید نهایی به کاربر نشان می‌دهد — چیزی هنوز ثبت نمی‌شود تا وقتی
    کاربر «تایید» را بزند (چون OCR همیشه ۱۰۰٪ دقیق نیست و فرستادن دوباره یک فاکتور نباید
    باعث ثبت تکراری بشه).
    """
    if not receipt_lines:
        await update.message.reply_text(
            "هیچ ردیف قیمت‌داری تشخیص داده نشد. فایل واضح‌تر بفرست یا دستی وارد کن."
        )
        return

    cur = db.get_currency(household_id)
    context.chat_data["receipt_draft"] = {"lines": receipt_lines, "note": note, "store": None}
    text, kb = _receipt_preview_text_and_keyboard(cur, receipt_lines, note)
    await update.message.reply_text(text, reply_markup=kb)


async def _finalize_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id, lines, note, store=None):
    """بعد از تایید کاربر، آیتم‌های باقی‌مانده در پیش‌نویس را واقعاً ثبت می‌کند، با لیست خرید تطبیق می‌دهد،
    و همه با یک receipt_id مشترک ذخیره می‌شوند تا بعداً بشه کل فاکتور را یک‌جا حذف کرد.
    اگه کاربر فروشگاه رو دستی انتخاب کرده باشه، همه ردیف‌ها با اون فروشگاه + دسته متناظرش ثبت می‌شن
    (به‌جای تشخیص خودکار دسته از روی نام هر کالا)."""
    cur = db.get_currency(household_id)
    active = db.get_active_list(household_id)
    receipt_id = uuid.uuid4().hex[:10]
    forced_category = db.RECEIPT_STORE_CATEGORY.get(store) if store else None
    reply_lines = ["🧾 ثبت شد:"]
    if note:
        reply_lines.append(note)
    if store:
        reply_lines.append(f"🏪 فروشگاه: {store}")
    reply_lines.append("")
    total = 0.0
    for rl in lines:
        reply_lines.append(f"• {rl['name']} — {fmt(rl['amount'], cur)}")
        total += rl["amount"]
        cat = forced_category or categorize.categorize(rl["name"], household_id)
        db.add_transaction(household_id, update.effective_user.id, "expense", rl["amount"],
                            category=cat, description=rl["name"], source="ocr", receipt_id=receipt_id, store=store)
    reply_lines.append(f"\nجمع: {fmt(total, cur)} — همه به‌عنوان هزینه ثبت شدند.")
    reply_lines.append("(اگه بعداً دیدی اشتباه بوده، از ⚙️ تنظیمات → تراکنش‌های اخیر می‌تونی کل این فاکتور رو یک‌جا حذف کنی.)")

    if active:
        items = db.get_list_items(active["id"])
        matches, unmatched = ocr.match_against_list(lines, items)
        for m in matches:
            db.mark_item_bought(m["list_item"]["id"], bought=True, price=m["receipt_line"]["amount"])
        items_after = db.get_list_items(active["id"])
        remaining = [i for i in items_after if not i["bought"]]
        if matches:
            reply_lines.append(f"\n✅ {len(matches)} مورد از لیست خریدت به‌صورت خودکار تیک خورد.")
        if remaining:
            reply_lines.append("\n🛒 هنوز باقی مونده از لیست:")
            for it in remaining:
                reply_lines.append(f"  ◻️ {it['item_name']}")
        else:
            reply_lines.append("\n🎉 کل لیست خرید تکمیل شد!")

    bal = db.get_balance(household_id)
    reply_lines.append(f"\n💰 باقیمانده بودجه {_period_label(household_id)}: {fmt(bal['remaining'], cur)}")
    return "\n".join(reply_lines)


async def rcpt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    household_id = db.get_user_household(update.effective_user.id)
    if household_id is None:
        await query.edit_message_text("اول /start رو بزن.")
        return

    draft = context.chat_data.get("receipt_draft")
    if not draft:
        await query.edit_message_text("این پیش‌نویس فاکتور دیگه معتبر نیست. دوباره عکس/PDF رو بفرست.")
        return

    parts = query.data.split(":")  # rcpt:action[:idx]
    action = parts[1] if len(parts) > 1 else ""
    cur = db.get_currency(household_id)

    if action == "rm" and len(parts) > 2:
        idx = int(parts[2])
        lines = draft["lines"]
        if 0 <= idx < len(lines):
            lines.pop(idx)
        if not lines:
            context.chat_data.pop("receipt_draft", None)
            await query.edit_message_text("همه آیتم‌ها حذف شدن؛ چیزی ثبت نشد.")
            return
        text, kb = _receipt_preview_text_and_keyboard(cur, lines, draft.get("note"), draft.get("store"))
        await query.edit_message_text(text, reply_markup=kb)
        return

    if action == "store":
        await query.edit_message_text("کدوم فروشگاهه؟", reply_markup=_receipt_store_keyboard())
        return

    if action == "setstore" and len(parts) > 2:
        draft["store"] = parts[2]
        text, kb = _receipt_preview_text_and_keyboard(cur, draft["lines"], draft.get("note"), draft.get("store"))
        await query.edit_message_text(text, reply_markup=kb)
        return

    if action == "back":
        text, kb = _receipt_preview_text_and_keyboard(cur, draft["lines"], draft.get("note"), draft.get("store"))
        await query.edit_message_text(text, reply_markup=kb)
        return

    if action == "cancel":
        context.chat_data.pop("receipt_draft", None)
        await query.edit_message_text("🚫 لغو شد؛ چیزی ثبت نشد.")
        return

    if action == "confirm":
        lines = draft["lines"]
        store = draft.get("store")
        context.chat_data.pop("receipt_draft", None)
        if not lines:
            await query.edit_message_text("آیتمی برای ثبت نمونده بود.")
            return
        text = await _finalize_receipt(update, context, household_id, lines, draft.get("note"), store)
        await query.edit_message_text(text)
        return


@require_household
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id):
    if not ocr.TESSERACT_AVAILABLE:
        await update.message.reply_text(
            "OCR روی این سرور فعال نیست (tesseract نصب نشده). می‌تونی مبلغ‌ها رو دستی با /expense وارد کنی."
        )
        return
    await update.message.reply_text("📷 در حال خوندن فاکتور...")
    photo = update.message.photo[-1]
    file = await photo.get_file()
    image_bytes = bytes(await file.download_as_bytearray())

    try:
        raw_text = ocr.extract_receipt_text(image_bytes)
        receipt_lines = ocr.parse_receipt_lines(raw_text)
    except Exception as e:
        logger.exception("OCR failed")
        await update.message.reply_text(f"نتونستم فاکتور رو بخونم: {e}")
        return

    await _process_receipt_lines(update, context, household_id, receipt_lines)


async def _handle_pdf_receipt(update: Update, household_id, pdf_bytes: bytes):
    """
    ابتدا سعی می‌کند متن PDF را مستقیم از لایه متنی آن بخواند (اگر PDF دیجیتالی باشد، دقت تقریباً ۱۰۰٪).
    فقط اگر PDF لایه متن نداشت (یعنی اسکن یک عکسه)، به OCR روی تصویر رندرشده متوسل می‌شود.
    """
    text_layer = ""
    if ocr.PDF_AVAILABLE:
        try:
            text_layer = ocr.extract_pdf_text_layer(pdf_bytes)
        except Exception:
            logger.exception("PDF text-layer extraction failed")
            text_layer = ""

    if len(text_layer.strip()) > 20:
        receipt_lines = ocr.parse_receipt_lines_columnar(text_layer)
        note = "📄 متن این PDF مستقیم و دقیق خونده شد (بدون نیاز به OCR)."
        return receipt_lines, note

    if not ocr.TESSERACT_AVAILABLE:
        raise RuntimeError(
            "این PDF ظاهراً اسکن یک عکسه (لایه متن نداره) و OCR روی این سرور فعال نیست."
        )
    raw_text = ocr.extract_receipt_text_from_pdf(pdf_bytes)
    receipt_lines = ocr.parse_receipt_lines(raw_text)
    note = "📄 این PDF لایه متن نداشت (احتمالاً اسکن یک عکسه)، با OCR خونده شد."
    return receipt_lines, note


@require_household
async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE, household_id):
    """فایل PDF فاکتور (فرستاده‌شده به‌صورت 'File'، نه عکس) یا عکسی که به‌صورت File فرستاده شده را پردازش می‌کند."""
    doc = update.message.document
    mime = (doc.mime_type or "").lower()
    name = (doc.file_name or "").lower()

    if mime == "application/pdf" or name.endswith(".pdf"):
        if not ocr.PDF_AVAILABLE:
            await update.message.reply_text(
                "پردازش PDF روی این سرور فعال نیست (PyMuPDF نصب نشده). "
                "می‌تونی به‌جاش عکس فاکتور رو بفرستی، یا مبلغ‌ها رو دستی با /expense وارد کنی."
            )
            return
        await update.message.reply_text("📄 در حال خوندن PDF فاکتور...")
        file = await doc.get_file()
        pdf_bytes = bytes(await file.download_as_bytearray())
        try:
            receipt_lines, note = await _handle_pdf_receipt(update, household_id, pdf_bytes)
        except Exception as e:
            logger.exception("PDF processing failed")
            await update.message.reply_text(f"نتونستم PDF رو بخونم: {e}")
            return
        await _process_receipt_lines(update, context, household_id, receipt_lines, note=note)
        return

    if mime.startswith("image/"):
        if not ocr.TESSERACT_AVAILABLE:
            await update.message.reply_text(
                "OCR روی این سرور فعال نیست (tesseract نصب نشده). می‌تونی مبلغ‌ها رو دستی با /expense وارد کنی."
            )
            return
        await update.message.reply_text("📷 در حال خوندن فاکتور...")
        file = await doc.get_file()
        image_bytes = bytes(await file.download_as_bytearray())
        try:
            raw_text = ocr.extract_receipt_text(image_bytes)
            receipt_lines = ocr.parse_receipt_lines(raw_text)
        except Exception as e:
            logger.exception("OCR failed")
            await update.message.reply_text(f"نتونستم فاکتور رو بخونم: {e}")
            return
        await _process_receipt_lines(update, context, household_id, receipt_lines)
        return

    await update.message.reply_text("این نوع فایل رو نمی‌شناسم. عکس یا PDF فاکتور بفرست.")


# ---------------- راه‌اندازی ----------------

async def _post_init(application: Application):
    """لیست دستورات را برای دکمه منوی داخلی تلگرام (کنار جعبه پیام) ثبت می‌کند."""
    await application.bot.set_my_commands([
        BotCommand("menu", "باز کردن منوی اصلی"),
        BotCommand("start", "شروع / ساخت حساب خانواده"),
        BotCommand("balance", "باقیمانده بودجه"),
        BotCommand("recalc", "محاسبه مجدد بودجه و هزینه‌ها"),
        BotCommand("budget", "تنظیم بودجه بازه جاری"),
        BotCommand("period", "بازه بودجه: هفتگی یا ماهانه"),
        BotCommand("expense", "ثبت هزینه"),
        BotCommand("income", "ثبت درآمد"),
        BotCommand("report", "گزارش دسته‌بندی‌شده"),
        BotCommand("transactions", "تراکنش‌های اخیر (حذف/ویرایش)"),
        BotCommand("newlist", "لیست خرید جدید"),
        BotCommand("list", "نمایش لیست خرید"),
        BotCommand("currency", "تغییر واحد پول"),
        BotCommand("categories", "دسته‌بندی‌ها"),
        BotCommand("invite", "کد دعوت خانواده"),
        BotCommand("join", "پیوستن به خانواده‌ای دیگر"),
        BotCommand("backup", "دریافت نسخه پشتیبان دیتابیس"),
        BotCommand("help", "راهنما"),
    ])


def main():
    if not BOT_TOKEN:
        raise SystemExit("متغیر محیطی BOT_TOKEN تنظیم نشده. در README توضیح داده شده.")

    db.init_db()

    app = Application.builder().token(BOT_TOKEN).post_init(_post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("invite", invite_cmd))
    app.add_handler(CommandHandler("join", join_cmd))
    app.add_handler(CommandHandler("backup", backup_cmd))
    app.add_handler(CommandHandler("budget", budget_cmd))
    app.add_handler(CommandHandler("period", period_cmd))
    app.add_handler(CommandHandler("income", income_cmd))
    app.add_handler(CommandHandler("expense", expense_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("recalc", recalc_cmd))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("transactions", transactions_cmd))
    app.add_handler(CommandHandler("categories", categories_cmd))
    app.add_handler(CommandHandler("currency", currency_cmd))
    app.add_handler(CommandHandler("newlist", newlist_cmd))
    app.add_handler(CommandHandler("donelist", donelist_cmd))
    app.add_handler(CommandHandler("list", list_cmd))

    app.add_handler(CallbackQueryHandler(toggle_callback, pattern=r"^toggle:"))
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^m:"))
    app.add_handler(CallbackQueryHandler(tx_callback, pattern=r"^tx:"))
    app.add_handler(CallbackQueryHandler(exp_callback, pattern=r"^exp:"))
    app.add_handler(CallbackQueryHandler(excat_callback, pattern=r"^excat:"))
    app.add_handler(CallbackQueryHandler(rcpt_callback, pattern=r"^rcpt:"))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text))

    logger.info("Bot starting (polling)...")
    app.run_polling()


if __name__ == "__main__":
    main()
