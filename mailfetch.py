"""
اتصال به ایمیل (IMAP) برای خوندن خودکار فاکتورهای پیوست‌شده (مثلاً از Mercadona) و استخراج
پیوست‌های PDF‌شون، تا با همون موتور OCR/PDF که برای فاکتور دستی استفاده می‌شه پردازش بشن.

نکته امنیتی: این ماژول با App Password کار می‌کنه (نه رمز اصلی حساب گوگل)، که از تنظیمات
حساب گوگل (myaccount.google.com/apppasswords) ساخته می‌شه و قابل لغو جداگانه‌ست.
"""
import imaplib
import email
from email.header import decode_header


def _decode(value):
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="ignore"))
        else:
            out.append(text)
    return "".join(out)


def fetch_new_receipt_pdfs(email_address, app_password, imap_host, imap_port, sender_filter, last_uid):
    """
    به ایمیل وصل می‌شه، ایمیل‌های جدیدتر از last_uid که فرستنده‌شون شامل sender_filter باشه رو
    پیدا می‌کنه، و برای هرکدوم پیوست‌های PDF رو استخراج می‌کنه. این تابع synchronous/blocking‌ه؛
    باید از یه executor/thread جدا صدا زده بشه تا event loop ربات رو قفل نکنه.

    خروجی: (results, new_last_uid)
    results: [{"uid": int, "subject": str, "from": str, "pdfs": [bytes, ...]}]
    """
    results = []
    max_uid = last_uid
    conn = imaplib.IMAP4_SSL(imap_host, imap_port)
    try:
        conn.login(email_address, app_password)
        conn.select("INBOX")
        search_criteria = f'(FROM "{sender_filter}" UID {last_uid + 1}:*)'
        status, data = conn.uid("search", None, search_criteria)
        if status != "OK" or not data or not data[0]:
            return results, max_uid
        uids = sorted({int(u) for u in data[0].split()})
        for uid in uids:
            if uid <= last_uid:
                continue
            status, msg_data = conn.uid("fetch", str(uid), "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                max_uid = max(max_uid, uid)
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            subject = _decode(msg.get("Subject"))
            from_ = _decode(msg.get("From"))
            pdfs = []
            for part in msg.walk():
                content_type = part.get_content_type()
                filename = part.get_filename()
                if content_type == "application/pdf" or (filename and filename.lower().endswith(".pdf")):
                    payload = part.get_payload(decode=True)
                    if payload:
                        pdfs.append(payload)
            if pdfs:
                results.append({"uid": uid, "subject": subject, "from": from_, "pdfs": pdfs})
            max_uid = max(max_uid, uid)
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return results, max_uid
