"""
ساخت فایل PDF از گزارش هزینه‌ها، با متن فارسی درست‌چین (اتصال حروف + راست‌به‌چپ).

برای نمایش درست فارسی/عربی تو PDF دو چیز لازمه:
۱) یه فونت TrueType که حروف فارسی/عربی داشته باشه (فونت‌های پیش‌فرض ReportLab فقط لاتین‌اند).
۲) اتصال حروف به هم (reshaping) و چیدمان راست‌به‌چپ (bidi) قبل از رسم متن — چون ReportLab خودش
   این کار رو انجام نمی‌ده.

فونت از پکیج دبیان/اوبونتوی fonts-noto-naskh-arabic خونده می‌شه (تو Dockerfile نصب می‌شه). اگه به
هر دلیلی روی سرور پیدا نشه، به‌جای کرش کردن، بدون شکل‌دهی فارسی (فقط با فونت لاتین) کار می‌کنه —
عددها و واحد پول درست می‌مونن، فقط حروف فارسی درست نمایش داده نمی‌شن.
"""
import io
import os
import glob

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.enums import TA_RIGHT, TA_CENTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

import arabic_reshaper
from bidi.algorithm import get_display

# اولویت با فونت وزیر (Vazirmatn) — تو Dockerfile مستقیم از jsDelivr دانلود می‌شه. اگه نبود (مثلاً
# دانلودش موقع build شکست خورده)، Noto Sans Arabic (از پکیج دبیان fonts-noto-core) به‌عنوان
# fallback مطمئن استفاده می‌شه — حروف فارسی رو داره، فقط ظاهرش شبیه وزیر نیست.
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansArabic-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf",
    "/usr/share/fonts/opentype/noto/NotoNaskhArabic-Regular.ttf",
]
_BOLD_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/vazirmatn/Vazirmatn-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansArabic-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoNaskhArabic-Bold.ttf",
    "/usr/share/fonts/opentype/noto/NotoNaskhArabic-Bold.ttf",
]

FONT_NAME = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
FARSI_FONT_OK = False


def _find_font(candidates):
    for path in candidates:
        if os.path.exists(path):
            return path
    # اگه مسیر دقیق پیدا نشد (مثلاً به‌خاطر ورژن متفاوت پکیج)، دنبال هر فونتی با اسم مشابه بگرد
    for pattern in ("/usr/share/fonts/**/*.ttf", "/usr/share/fonts/**/*.ttc"):
        for path in glob.glob(pattern, recursive=True):
            name = os.path.basename(path).lower()
            if "vazir" in name or "naskh" in name or "arabic" in name:
                return path
    return None


def _setup_fonts():
    global FONT_NAME, FONT_BOLD, FARSI_FONT_OK
    regular = _find_font(_FONT_CANDIDATES)
    if not regular:
        return
    bold = _find_font(_BOLD_FONT_CANDIDATES) or regular
    try:
        pdfmetrics.registerFont(TTFont("Farsi", regular))
        pdfmetrics.registerFont(TTFont("Farsi-Bold", bold))
        FONT_NAME = "Farsi"
        FONT_BOLD = "Farsi-Bold"
        FARSI_FONT_OK = True
    except Exception:
        pass


_setup_fonts()

NO_DECIMAL_CURRENCIES = {"تومان", "ریال"}


def _fmt(n, currency=None):
    if currency in NO_DECIMAL_CURRENCIES:
        s = f"{n:,.0f}"
    else:
        s = f"{n:,.2f}"
    return f"{s} {currency}" if currency else s


def rtl(text):
    """متن فارسی رو برای نمایش درست تو PDF آماده می‌کنه: حروف رو به هم می‌چسبونه (reshape) و
    ترتیب نمایش راست‌به‌چپ رو درست می‌کنه (bidi) — بدون این کار حروف جدا از هم و برعکس نشون داده
    می‌شن. اعداد و متن انگلیسی داخل رشته دست‌نخورده و چپ‌به‌راست می‌مونن."""
    text = "" if text is None else str(text)
    if not FARSI_FONT_OK or not text:
        return text
    try:
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def _styles():
    base = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "FarsiTitle", parent=base["Title"], fontName=FONT_BOLD, alignment=TA_CENTER, fontSize=16,
    )
    heading_style = ParagraphStyle(
        "FarsiHeading", parent=base["Heading2"], fontName=FONT_BOLD, alignment=TA_RIGHT, fontSize=12,
        spaceBefore=10, spaceAfter=4,
    )
    normal_style = ParagraphStyle(
        "FarsiNormal", parent=base["Normal"], fontName=FONT_NAME, alignment=TA_RIGHT, fontSize=10,
    )
    cell_style = ParagraphStyle(
        "FarsiCell", parent=base["Normal"], fontName=FONT_NAME, alignment=TA_RIGHT, fontSize=9, leading=12,
    )
    total_style = ParagraphStyle(
        "FarsiTotal", parent=base["Normal"], fontName=FONT_BOLD, alignment=TA_RIGHT, fontSize=11,
        spaceBefore=6,
    )
    return title_style, heading_style, normal_style, cell_style, total_style


def _groups_table(groups, cur, cell_style, header_style):
    header = [rtl("مبلغ"), rtl("شرح"), rtl("تاریخ"), "#"]
    data = [header]
    for i, g in enumerate(groups, 1):
        data.append([
            Paragraph(_fmt(g["amount"], cur), cell_style),
            Paragraph(rtl(g.get("label", "")), cell_style),
            Paragraph(g.get("tx_date", ""), cell_style),
            Paragraph(str(i), cell_style),
        ])
    table = Table(data, colWidths=[35 * mm, 75 * mm, 30 * mm, 12 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), header_style.fontName),
        ("FONTNAME", (0, 1), (-1, -1), FONT_NAME),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f3b52")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f6f8")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def _render_expense_section(story, groups, total, side_total, currency, styles, show_budget_heading=True):
    """یه بخش گزارش (جدول هزینه‌های داخل بودجه + جدول هزینه‌های جانبی، با جمع‌هاشون) رو به story
    اضافه می‌کنه. برای استفاده مشترک بین گزارش تخت و هر بازه‌ی گزارش تفکیک‌شده بر اساس بازه بودجه."""
    heading_style, normal_style, cell_style, total_style = styles
    budget_groups = [g for g in groups if g.get("in_budget", 1)]
    side_groups = [g for g in groups if not g.get("in_budget", 1)]

    if budget_groups:
        if show_budget_heading:
            story.append(Paragraph(rtl("هزینه‌های داخل بودجه"), heading_style))
        story.append(_groups_table(budget_groups, currency, cell_style, heading_style))
    else:
        story.append(Paragraph(rtl("هزینه‌ای داخل بودجه ثبت نشده."), normal_style))
    story.append(Paragraph(rtl(f"جمع (داخل بودجه): {_fmt(total, currency)}"), total_style))

    if side_groups:
        story.append(Paragraph(rtl("📎 هزینه‌های جانبی (خارج از بودجه)"), heading_style))
        story.append(_groups_table(side_groups, currency, cell_style, heading_style))
        story.append(Paragraph(rtl(f"جمع هزینه‌های جانبی: {_fmt(side_total, currency)}"), total_style))


def build_report_pdf(title, report, currency):
    """
    گزارش رو به یه فایل PDF تبدیل می‌کنه. دو نوع ورودی پشتیبانی می‌شه:
      ۱) خروجی db.get_report / db.get_report_by_dates: dict با کلیدهای groups/total/side_total —
         یه لیست تخت (بدون تفکیک بر اساس بازه بودجه).
      ۲) خروجی db.get_report_by_periods: dict با کلید periods (لیستی از بخش‌ها، هرکدوم با
         start/end/groups/total/side_total خودش) — چون بودجه ممکنه هفتگی باشه، یه گزارش ماهانه چند
         بازه بودجه مختلف رو در بر می‌گیره و هرکدوم با زیرعنوان و جمع خودش جدا نشون داده می‌شه.
    title: عنوان بازه (مثلاً 'این ماه' یا '2026-06-01 تا 2026-06-30').
    currency: واحد پول برای فرمت‌کردن مبلغ‌ها (مثل fmt تو bot.py).
    خروجی: io.BytesIO آماده برای ارسال به‌عنوان فایل.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=18 * mm, bottomMargin=15 * mm, leftMargin=15 * mm, rightMargin=15 * mm,
        title=title,
    )
    title_style, heading_style, normal_style, cell_style, total_style = _styles()
    styles = (heading_style, normal_style, cell_style, total_style)

    story = [
        Paragraph(rtl(f"🧾 گزارش هزینه‌ها — {title}"), title_style),
        Spacer(1, 6 * mm),
    ]

    if "periods" in report:
        non_empty = [p for p in report["periods"] if p["groups"]]
        multi = len(report["periods"]) > 1
        if not non_empty:
            story.append(Paragraph(rtl("هزینه‌ای برای این بازه ثبت نشده."), normal_style))
        for p in report["periods"]:
            if not p["groups"]:
                continue
            if multi:
                p_title = p["start"] if p["start"] == p["end"] else f"{p['start']} تا {p['end']}"
                story.append(Paragraph(rtl(f"📆 بازه بودجه {p_title}"), heading_style))
            _render_expense_section(
                story, p["groups"], p["total"], p["side_total"], currency, styles,
                show_budget_heading=not multi,
            )
            story.append(Spacer(1, 5 * mm))
        if multi:
            story.append(Paragraph(rtl(f"جمع کل (داخل بودجه): {_fmt(report.get('total', 0), currency)}"), total_style))
            if report.get("side_total"):
                story.append(Paragraph(
                    rtl(f"جمع کل هزینه‌های جانبی: {_fmt(report.get('side_total', 0), currency)}"), total_style,
                ))
    else:
        _render_expense_section(
            story, report.get("groups", []), report.get("total", 0), report.get("side_total", 0),
            currency, styles,
        )

    doc.build(story)
    buf.seek(0)
    return buf
