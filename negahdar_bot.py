# negahdar_bot.py
\"\"\"NegahdarBot - Telethon bot configured to run on Railway (or any hosting that supports env vars)
Usage: set environment variables (BOT_API_ID, BOT_API_HASH, BOT_TOKEN, ADMIN_ID, SOURCE_CHANNEL)
Then run: python3 negahdar_bot.py
\"\"\"
import os
import logging
import sqlite3
from datetime import datetime
from asyncio import sleep
from telethon import TelegramClient, events, errors
from telethon.tl.custom.message import Message

# Logging
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
log = logging.getLogger("negahdar")

# Read config from environment (required by hosted platforms)
try:
    API_ID = int(os.environ["BOT_API_ID"])
    API_HASH = os.environ["BOT_API_HASH"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    ADMIN_ID = int(os.environ["ADMIN_ID"])
    SOURCE_CHANNEL = os.environ.get("SOURCE_CHANNEL", "@asdfasdgfsdg")
except KeyError as e:
    log.error("Missing required environment variable: %s", e)
    raise SystemExit("Set BOT_API_ID, BOT_API_HASH, BOT_TOKEN, ADMIN_ID in environment and restart.")

DB_NAME = os.environ.get("DB_NAME", "negahdar.db")
BOT_NAME = os.environ.get("BOT_NAME", "NegahdarBot")
START_MESSAGE = ("سلام 👋 — خوش اومدی به نگهدار!\\n"
                 "برای دیدن یک پست، کد عددی اون رو اینجا بفرست یا روی لینک deep-link کلیک کن.\\n"
                 "هر پست به‌صورت موقت (۲۰ ثانیه) برایت فرستاده می‌شود — سریع ذخیره کن.")

# DB helpers
def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS posts
                 (post_id INTEGER PRIMARY KEY, channel_ref TEXT, channel_id INTEGER, message_id INTEGER, timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS sequence
                 (id INTEGER PRIMARY KEY CHECK(id = 1), next_id INTEGER DEFAULT 1)''')
    c.execute('INSERT OR IGNORE INTO sequence (id, next_id) VALUES (1, 1)')
    conn.commit()
    conn.close()
    log.info("Database initialized: %s", DB_NAME)

def get_and_increment_next_id():
    conn = sqlite3.connect(DB_NAME, timeout=10, isolation_level=None)
    c = conn.cursor()
    try:
        c.execute('BEGIN IMMEDIATE')
        c.execute('SELECT next_id FROM sequence WHERE id = 1')
        row = c.fetchone()
        if not row:
            nid = 1
            c.execute('INSERT OR REPLACE INTO sequence (id, next_id) VALUES (1, 2)')
        else:
            nid = row[0]
            c.execute('UPDATE sequence SET next_id = ? WHERE id = 1', (nid + 1,))
        conn.commit()
        return nid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def save_post(post_id, channel_ref, channel_id, message_id, timestamp):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO posts (post_id, channel_ref, channel_id, message_id, timestamp) VALUES (?, ?, ?, ?, ?)',
              (post_id, channel_ref, channel_id, message_id, timestamp))
    conn.commit()
    conn.close()

def get_post(post_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT channel_ref, channel_id, message_id, timestamp FROM posts WHERE post_id = ?', (post_id,))
    res = c.fetchone()
    conn.close()
    return res

# Telethon client
client = TelegramClient('negahdar_session', API_ID, API_HASH)
client._cached_bot_username = None

@client.on(events.NewMessage(chats=SOURCE_CHANNEL))
async def monitor_posts(event: Message):
    if getattr(event.message, 'action', None):
        return
    try:
        post_id = get_and_increment_next_id()
    except Exception as e:
        log.error("DB next_id error: %s", e)
        return
    ts = datetime.utcnow().isoformat()
    channel_ref = SOURCE_CHANNEL
    channel_id = getattr(event, 'chat_id', None)
    save_post(post_id, channel_ref, channel_id, event.message.id, ts)
    if not getattr(client, "_cached_bot_username", None):
        try:
            me = await client.get_me()
            client._cached_bot_username = me.username
        except Exception:
            client._cached_bot_username = None
    bot_user = client._cached_bot_username or BOT_NAME
    deep = f"https://t.me/{bot_user}?start={post_id}"
    link_text = f"پست جدید #{post_id} ذخیره شد!\\nلینک: {deep}"
    try:
        await client.send_message(ADMIN_ID, link_text)
        await client.forward_messages(ADMIN_ID, event.message)
    except Exception as e:
        log.error("Failed to notify admin: %s", e)

@client.on(events.NewMessage(pattern=r'/start(?:\\s(\\d+))?'))
async def start_handler(event: Message):
    match = None
    try:
        match = event.pattern_match.group(1)
    except Exception:
        match = None
    if match and match.isdigit():
        post_id = int(match)
        await fetch_and_deliver(event, post_id)
    else:
        try:
            msg = await event.reply(START_MESSAGE)
            await sleep(9)
            await client.delete_messages(event.chat_id, [msg.id])
        except Exception:
            pass

@client.on(events.NewMessage())
async def manual_code_handler(event: Message):
    if not getattr(event.message, 'text', None):
        return
    text = event.message.text.strip()
    if text.isdigit():
        post_id = int(text)
        await fetch_and_deliver(event, post_id)

async def fetch_and_deliver(event: Message, post_id: int):
    record = get_post(post_id)
    if not record:
        await event.reply("❌ کد مورد نظر یافت نشد. لطفا از ادمین بررسی کنید.")
        return
    channel_ref, channel_id, message_id, timestamp = record
    from_peer = channel_id if channel_id is not None else channel_ref
    try:
        forwarded = await client.forward_messages(entity=event.chat_id, messages=message_id, from_peer=from_peer, drop_author=True)
        forwarded_list = forwarded if isinstance(forwarded, list) else [forwarded]
        note = await event.reply("✅ پیام ارسال شد — تا ۲۰ ثانیه حذف خواهد شد. سریع ذخیره کنید.")
        promo = await event.reply("📌 عضو کانال شوید تا محتواهای جدید را از دست ندهید: https://t.me/+vCSljlQ15BkzMzE0")
        await sleep(20)
        ids_to_delete = [m.id for m in forwarded_list if getattr(m, 'id', None)]
        if getattr(note, 'id', None): ids_to_delete.append(note.id)
        if getattr(promo, 'id', None): ids_to_delete.append(promo.id)
        if ids_to_delete:
            try:
                await client.delete_messages(event.chat_id, ids_to_delete)
            except errors.rpcerrorlist.BadRequestError as e:
                log.warning("Failed to delete some messages: %s", e)
    except Exception as e:
        log.error("Error forwarding post %s: %s", post_id, e)
        try:
            await event.reply(f"خطا در ارسال پست: {e}")
        except Exception:
            pass

if __name__ == '__main__':
    init_db()
    log.info("NegahdarBot starting...")
    client.start(bot_token=BOT_TOKEN)
    client.run_until_disconnected()