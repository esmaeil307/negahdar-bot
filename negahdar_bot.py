import os
import logging
import sqlite3
from datetime import datetime
from asyncio import sleep
from typing import Optional, Tuple, Any
from telethon import TelegramClient, events, errors
from telethon.tl.custom.message import Message

try:
    API_ID = int(os.environ.get("BOT_API_ID", os.environ.get("API_ID", 0)))
    API_HASH = os.environ.get("BOT_API_HASH", os.environ.get("API_HASH", ""))
    BOT_TOKEN = os.environ.get("BOT_TOKEN", os.environ.get("BOT_TOKEN_VALUE", ""))
    ADMIN_ID = int(os.environ.get("ADMIN_ID", os.environ.get("OWNER_ID", "0")))
    SOURCE_CHANNEL = os.environ.get("SOURCE_CHANNEL", "@asdfasdgfsdg")
except Exception:
    raise SystemExit("لطفاً متغیرهای محیطی BOT_API_ID, BOT_API_HASH, BOT_TOKEN, ADMIN_ID را تنظیم کنید.")

# محل ذخیره دیتابیس — /tmp برای محیط‌های host مناسب است
DB_NAME = os.environ.get("DB_NAME", "/tmp/negahdar.db")

BOT_NAME = os.environ.get("BOT_NAME", "NegahdarBot")
START_MESSAGE = (
    "سلام 👋 — خوش اومدی به نگهدار!\n"
    "برای دیدن یک پست، کد عددی اون رو اینجا بفرست یا روی لینک deep-link کلیک کن.\n"
    "هر پست به‌صورت موقت (۲۰ ثانیه) برایت فرستاده می‌شود — سریع ذخیره کن."
)

# -------------------------
# ===== Logging ===========
# -------------------------
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
log = logging.getLogger("negahdar")

# -------------------------
# ====== DB Helpers =======
# -------------------------
def init_db(db_path: str = DB_NAME) -> None:
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS posts
                 (post_id INTEGER PRIMARY KEY, channel_ref TEXT, channel_id INTEGER, message_id INTEGER, timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS sequence
                 (id INTEGER PRIMARY KEY CHECK(id = 1), next_id INTEGER DEFAULT 1)''')
    c.execute('INSERT OR IGNORE INTO sequence (id, next_id) VALUES (1, 1)')
    conn.commit()
    conn.close()
    log.info("Database initialized: %s", db_path)


def get_and_increment_next_id(db_path: str = DB_NAME) -> int:
    """
    Atomically read+increment next_id to avoid race.
    Uses BEGIN IMMEDIATE to acquire write lock in sqlite.
    """
    conn = sqlite3.connect(db_path, timeout=10, isolation_level=None)
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
    except Exception as e:
        conn.rollback()
        log.exception("Error in get_and_increment_next_id: %s", e)
        raise
    finally:
        conn.close()


def save_post(post_id: int, channel_ref: str, channel_id: Optional[int], message_id: int, timestamp: str) -> None:
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute(
        'INSERT OR REPLACE INTO posts (post_id, channel_ref, channel_id, message_id, timestamp) VALUES (?, ?, ?, ?, ?)',
        (post_id, channel_ref, channel_id, message_id, timestamp)
    )
    conn.commit()
    conn.close()


def get_post(post_id: int) -> Optional[Tuple[Optional[str], Optional[int], Optional[int], Optional[str]]]:
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('SELECT channel_ref, channel_id, message_id, timestamp FROM posts WHERE post_id = ?', (post_id,))
    res = c.fetchone()
    conn.close()
    return res  # (channel_ref, channel_id, message_id, timestamp) or None

# -------------------------
# ===== Telethon Client ===
# -------------------------
client = TelegramClient('negahdar_session', API_ID, API_HASH)
client._cached_bot_username = None

# helper: get bot username cached
async def get_bot_username() -> str:
    if getattr(client, "_cached_bot_username", None):
        return client._cached_bot_username
    try:
        me = await client.get_me()
        client._cached_bot_username = getattr(me, "username", None) or BOT_NAME
    except Exception:
        client._cached_bot_username = BOT_NAME
    return client._cached_bot_username

# -------------------------
# ===== Monitor handler ===
# -------------------------
@client.on(events.NewMessage(chats=SOURCE_CHANNEL))
async def monitor_posts(event: Message) -> None:
    # ignore service messages
    if getattr(event.message, "action", None):
        return

    try:
        post_id = get_and_increment_next_id()
    except Exception as e:
        log.error("Failed to allocate post_id: %s", e)
        return

    ts = datetime.utcnow().isoformat()
    channel_ref = SOURCE_CHANNEL
    channel_id = getattr(event, 'chat_id', None)
    save_post(post_id, channel_ref, channel_id, event.message.id, ts)
    bot_user = await get_bot_username()
    deep = f"https://t.me/{bot_user}?start={post_id}"
    link_text = f"پست جدید #{post_id} ذخیره شد!\nلینک: {deep}"
    try:
        if ADMIN_ID:
            await client.send_message(ADMIN_ID, link_text)
            # برای ادمین هنوز می‌تونیم کپی ارسال کنیم یا فوروارد — اینجا کپی می‌کنیم (بدون نشان دادن منبع)
            # get original message and send its content to admin
            try:
                orig = await client.get_messages(channel_id if channel_id is not None else channel_ref, ids=event.message.id)
                if orig is None:
                    # fallback forward if get_messages failed
                    await client.forward_messages(ADMIN_ID, event.message)
                else:
                    if orig.media:
                        await client.send_file(ADMIN_ID, orig.media, caption=orig.message)
                    else:
                        await client.send_message(ADMIN_ID, orig.message)
            except Exception:
                # last-resort forward
                try:
                    await client.forward_messages(ADMIN_ID, event.message)
                except Exception as e:
                    log.warning("Could not notify admin by copying or forwarding: %s", e)
    except Exception as e:
        log.error("Failed to notify admin: %s", e)


# -------------------------
# ===== User handlers =====
# -------------------------
@client.on(events.NewMessage(pattern=r'/start(?:\s(\d+))?'))
async def start_handler(event: Message) -> None:
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
            # delete our prompt
            try:
                await client.delete_messages(event.chat_id, [msg.id])
            except Exception:
                pass
        except Exception:
            pass


@client.on(events.NewMessage())
async def manual_code_handler(event: Message) -> None:
    # ignore messages without text
    if not getattr(event.message, "text", None):
        return
    text = event.message.text.strip()
    if text.isdigit():
        post_id = int(text)
        await fetch_and_deliver(event, post_id)


# -------------------------
# ===== Delivery logic ====
# -------------------------
async def fetch_and_deliver(event: Message, post_id: int) -> None:
    """
    Read the saved post and send its content to the user WITHOUT showing forwarding info.
    Then delete sent items after 20 seconds.
    """
    record = get_post(post_id)
    if not record:
        try:
            await event.reply("❌ کد مورد نظر یافت نشد. لطفا از ادمین بررسی کنید.")
        except Exception:
            pass
        return

    channel_ref, channel_id, message_id, timestamp = record
    from_peer = channel_id if channel_id is not None else channel_ref

    try:
        # get original message object
        orig = await client.get_messages(from_peer, ids=message_id)
        if not orig:
            await event.reply("خطا: پیام اصلی یافت نشد.")
            return

        # send content as a new message (no forward)
        if orig.media:
            # send_file handles photo, video, document, voice, sticker, etc.
            sent = await client.send_file(event.chat_id, orig.media, caption=orig.message)
        else:
            sent = await client.send_message(event.chat_id, orig.message or "")

        # send helper / promo messages
        note = await event.reply("✅ پیام ارسال شد — تا ۲۰ ثانیه حذف خواهد شد. سریع ذخیره کنید.")
        promo = await event.reply("📌 عضو کانال شوید تا محتواهای جدید را از دست ندهید: https://t.me/+vCSljlQ15BkzMzE0")

        # wait, then delete the messages we just sent
        await sleep(20)

        ids_to_delete = []
        # normalize sent to list
        if isinstance(sent, list):
            ids_to_delete.extend([m.id for m in sent if getattr(m, "id", None)])
        else:
            if getattr(sent, "id", None):
                ids_to_delete.append(sent.id)
        if getattr(note, "id", None):
            ids_to_delete.append(note.id)
        if getattr(promo, "id", None):
            ids_to_delete.append(promo.id)

        if ids_to_delete:
            try:
                await client.delete_messages(event.chat_id, ids_to_delete)
            except errors.rpcerrorlist.BadRequestError as e:
                log.warning("Failed to delete some messages: %s", e)
    except Exception as e:
        log.exception("Error delivering post %s: %s", post_id, e)
        try:
            await event.reply(f"خطا در ارسال پست: {e}")
        except Exception:
            pass

# -------------------------
# ===== Import helper =====
# -------------------------
def import_json_to_db(json_path: str = "data.json") -> None:
    """
    If you have old JSON data (like the one you showed), this function imports it.
    JSON format:
    {
      "next_id": 6,
      "posts": {
        "1": {"channel": "@xxx", "message_id": 47, "timestamp": "..."},
        ...
      }
    }
    """
    import json
    if not os.path.exists(json_path):
        log.info("No data.json found to import.")
        return
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    posts = data.get("posts", {})
    next_id = data.get("next_id", 1)
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    for k, v in posts.items():
        try:
            pid = int(k)
        except Exception:
            continue
        channel = v.get("channel")
        message_id = v.get("message_id")
        ts = v.get("timestamp")
        c.execute('INSERT OR REPLACE INTO posts (post_id, channel_ref, channel_id, message_id, timestamp) VALUES (?, ?, ?, ?, ?)',
                  (pid, channel, None, message_id, ts))
    c.execute('UPDATE sequence SET next_id = ? WHERE id = 1', (next_id,))
    conn.commit()
    conn.close()
    log.info("Imported JSON to DB (if any).")

# -------------------------
# ===== Entrypoint ========
# -------------------------
if __name__ == "__main__":
    init_db()
    # optionally import data.json if present
    import_json_to_db("data.json")

    log.info(f"{BOT_NAME} is starting...")
    try:
        client.start(bot_token=BOT_TOKEN)
        log.info("Bot started and running. Press Ctrl+C to stop.")
        client.run_until_disconnected()
    except Exception as e:
        log.exception("Failed to start bot: %s", e)
