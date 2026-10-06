import html
import os
import re
import time

import feedparser
import requests
import trafilatura
from groq import Groq

# ---------- الإعدادات ----------
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

RSS_FEEDS = [
    "https://tech3arabi.com/feed/",
    "https://techcrunch.com/feed/",
    "https://www.theverge.com/rss/index.xml",
]

MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"]  # الثاني احتياطي
PUBLISHED_FILE = "published_urls.txt"
MAX_PER_RUN = 3          # أقصى عدد منشورات في كل تشغيل
MAX_TEXT_CHARS = 4000    # أقصى طول نص يُرسل للذكاء الاصطناعي
MAX_STORED_URLS = 1000   # حتى لا يكبر الملف

client = Groq(api_key=GROQ_API_KEY)


# ---------- منع التكرار ----------
def load_published():
    if not os.path.exists(PUBLISHED_FILE):
        return []
    with open(PUBLISHED_FILE, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def save_urls(urls):
    with open(PUBLISHED_FILE, "a", encoding="utf-8") as f:
        for u in urls:
            f.write(u + "\n")


def trim_file():
    urls = load_published()
    if len(urls) > MAX_STORED_URLS:
        with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
            f.write("\n".join(urls[-MAX_STORED_URLS:]) + "\n")


# ---------- جلب النص ----------
def clean_html(raw):
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def get_article_text(entry):
    raw = ""
    if entry.get("content"):
        raw = entry["content"][0].get("value", "")
    if not raw:
        raw = entry.get("summary", "")
    text = clean_html(raw)

    # إذا كان نص الـ feed قصيراً، نحاول استخراج المقال الكامل
    if len(text) < 600:
        try:
            downloaded = trafilatura.fetch_url(entry.link)
            extracted = trafilatura.extract(downloaded) if downloaded else None
            if extracted and len(extracted) > len(text):
                text = extracted
        except Exception as e:
            print(f"Extraction failed for {entry.link}: {e}")
    return text[:MAX_TEXT_CHARS]


# ---------- التلخيص ----------
def summarize(title, text):
    prompt = (
        "أنت محرر أخبار تقنية. لخّص الخبر التالي بالعربية الفصحى المبسطة "
        "في 3 نقاط قصيرة وواضحة، كل نقطة في سطر تبدأ بالرمز •. "
        "اكتب نصاً عادياً بدون Markdown أو عناوين، ولا تضف معلومات غير موجودة في النص.\n\n"
        f"العنوان: {title}\n\nالنص: {text}"
    )
    for model in MODELS:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            print(f"Groq error with {model}: {e}")
            time.sleep(2)
    return None


# ---------- النشر ----------
def send_to_telegram(title, summary, link):
    safe_title = html.escape(title)
    safe_summary = html.escape(summary)[:3500]
    safe_link = html.escape(link, quote=True)
    message = f"<b>{safe_title}</b>\n\n{safe_summary}\n\n<a href=\"{safe_link}\">المصدر</a>"
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=30,
    )
    if not r.ok:
        print(f"Telegram error {r.status_code}: {r.text}")
    return r.ok


# ---------- المنطق الرئيسي ----------
def main():
    first_run = not os.path.exists(PUBLISHED_FILE)
    published = set(load_published())

    new_entries = []
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"Feed error {feed_url}: {e}")
            continue
        for entry in feed.entries:
            link = entry.get("link")
            if link and link not in published:
                new_entries.append(entry)

    # أول تشغيل: نسجّل الروابط الحالية فقط بدون نشر
    if first_run:
        save_urls([e.link for e in new_entries])
        print(f"First run: recorded {len(new_entries)} links, nothing posted.")
        return

    posted = 0
    for entry in new_entries:
        if posted >= MAX_PER_RUN:
            break
        try:
            text = get_article_text(entry)
            if len(text) < 200:
                save_urls([entry.link])  # قصير جداً، نتجاهله نهائياً
                continue
            summary = summarize(entry.title, text)
            if not summary:
                continue  # نعيد المحاولة في التشغيل القادم
            if send_to_telegram(entry.title, summary, entry.link):
                save_urls([entry.link])
                posted += 1
                print(f"Published: {entry.title}")
                time.sleep(3)
        except Exception as e:
            print(f"Error processing {entry.get('link')}: {e}")

    trim_file()
    print(f"Done. Posted {posted} item(s).")


if __name__ == "__main__":
    main()
