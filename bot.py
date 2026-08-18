"""Combined Telegram bot for Netflix Token Generation and Authorized Link Distribution."""

from __future__ import annotations

import telebot
from telebot import types
import subprocess
import sys
import os
import re
import threading
import html
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from flask import Flask
from threading import Thread
from storage import LinkStore

# ==========================================
# CONFIGURATION & INITIALIZATION
# ==========================================

BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    print("Error: BOT_TOKEN ကို Environment Variable မှာ ထည့်သွင်းရသေးပါ ခင်ဗျာ။")
    exit(1)

# Specific Admin ID & User List as requested
ADMIN_ID = 1847021130
ADMIN_IDS = {ADMIN_ID}

active_users = {
    "1847021130": "Ren2512",
    "5786095389": "thureinlinlinn",
    "6609444194": "luke65214",
    "1833851827": "Aung",
    "6050862261": "khajhar",
    "1240231180": "VPNetwork25",
    "5555183383": "Sa Nay Maung",
    "1510379959": "Khine",
    "8029459862": "digitalworldmyanmar1212",
    "6445480256": "NyeinCHANAUNG7",
    "7814624012": "aeiou690",
    "8577702613": "Reno366",
    "7378715486": "NyeinChaNAungW",
    "5604493826": "Akai888",
    "5272159743": "phetkyam",
    "5389816539": "Hiza2026",
}

# Storage Link Bot Configs
BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "link_bot.sqlite3")))
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "3"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(2 * 1024 * 1024)))

def configured_timezone() -> ZoneInfo:
    timezone_name = os.getenv("APP_TIMEZONE", "Asia/Yangon")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise RuntimeError(f"APP_TIMEZONE is invalid: {timezone_name}") from error

TIMEZONE = configured_timezone()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("combined_bot")

# Initialize Bot, Flask App & Database
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)
app = Flask(__name__)
store = LinkStore(DATABASE_PATH)
store.initialize()

# State Tracking Variables
file_lock = threading.Lock()
running_process = {}
stop_flags = {}
awaiting_broadcast = {}

# Link Upload Tracking
_pending_upload_admins: set[int] = set()
_pending_lock = threading.Lock()

# Constants
STOP_BTN = "⏹ ဟိုးစတော့"
BROADCAST_CANCEL_BTN = "❌ Broadcast ပယ်ဖျက်"

COOKIE_LINE_RE = re.compile(
    r'^(?P<domain>\S+)\s+'
    r'(?P<flag1>TRUE|FALSE)\s+'
    r'(?P<path>/\S*?)\s*(?=TRUE|FALSE)'
    r'(?P<secure>TRUE|FALSE)\s+'
    r'(?P<expiry>\d+)\s+'
    r'(?P<name>\S+)\s+'
    r'(?P<value>.*)$'
)

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def current_date() -> str:
    return datetime.now(TIMEZONE).date().isoformat()

def log_user(message):
    user_id = str(message.chat.id)
    username = message.from_user.username or message.from_user.first_name or "Unknown"
    if user_id not in active_users:
        active_users[user_id] = username

def normalize_cookie_text(raw_bytes: bytes) -> bytes:
    text = raw_bytes.decode('utf-8', errors='ignore')
    fixed_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            fixed_lines.append(line)
            continue
        m = COOKIE_LINE_RE.match(stripped)
        if m:
            fixed_lines.append('\t'.join([
                m.group('domain'), m.group('flag1'), m.group('path'),
                m.group('secure'), m.group('expiry'),
                m.group('name'), m.group('value')
            ]))
        else:
            fixed_lines.append(line)
    return ('\n'.join(fixed_lines) + '\n').encode('utf-8')

# Keyboards
def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        types.KeyboardButton("/start 🔄"),
        types.KeyboardButton(STOP_BTN)
    )
    return markup

def get_broadcast_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(types.KeyboardButton(BROADCAST_CANCEL_BTN))
    return markup

def public_keyboard(user_id: int) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("Get ready link", callback_data="claim_link"))
    keyboard.add(types.InlineKeyboardButton("My quota", callback_data="my_quota"))
    if is_admin(user_id):
        keyboard.add(types.InlineKeyboardButton("Upload TXT links", callback_data="admin_upload"))
        keyboard.add(types.InlineKeyboardButton("Inventory status", callback_data="admin_stats"))
    return keyboard

# ==========================================
# WEB SERVER
# ==========================================

@app.route('/')
def alive():
    return "Bot is running online!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ==========================================
# COMMAND HANDLERS
# ==========================================

@bot.message_handler(commands=['start', 'menu'])
def send_welcome_and_menu(message):
    log_user(message)
    # Token Generator Menu
    bot.reply_to(
        message,
        "မင်္ဂလာပါ ဝေ့ -Netflix Cookie ပါတဲ့ .txtဖိုင် ဖြစ်ဖြစ် textဖြစ်ဖြစ် ပို့လိုက်ကွာ",
        reply_markup=get_main_menu()
    )
    # Link Distributer Menu
    bot.send_message(
        message.chat.id,
        "Use the buttons below. Links must be content the administrator is authorized to distribute.",
        reply_markup=public_keyboard(message.from_user.id),
        disable_web_page_preview=True,
    )

@bot.message_handler(commands=['users'])
def show_users(message):
    if message.chat.id != ADMIN_ID:
        bot.reply_to(message, "ပိုင်ရှင်ရှိတယ်")
        return
    if not active_users:
        bot.reply_to(message, "လက်ရှိတွင် အသုံးပြုသူ စာရင်း မရှိသေးပါ။")
        return
    user_list_text = f"👥 စုစုပေါင်း အသုံးပြုသူ: {len(active_users)} ဦး\n\n"
    for uid, uname in active_users.items():
        user_list_text += f"▪️ {uname} (ID: <code>{uid}</code>)\n"
    bot.reply_to(message, user_list_text, parse_mode="HTML")

@bot.message_handler(commands=['broadcast'])
def start_broadcast(message):
    if message.chat.id != ADMIN_ID:
        bot.reply_to(message, "ပိုင်ရှင်ရှိတယ်")
        return
    awaiting_broadcast[str(message.chat.id)] = True
    bot.reply_to(
        message,
        "📢 Broadcast ပို့ချင်တဲ့ စာသားကို ရိုက်ပို့ပါ။\nမလုပ်တော့ဘူးဆိုရင် အောက်က ခလုတ်ကို နှိပ်ပါ။",
        reply_markup=get_broadcast_menu()
    )

@bot.message_handler(commands=["cancel"])
def cancel_upload(message: types.Message) -> None:
    if message.from_user is None or not is_admin(message.from_user.id):
        return
    with _pending_lock:
        _pending_upload_admins.discard(message.from_user.id)
    bot.send_message(message.chat.id, "The pending upload was cancelled.")

# ==========================================
# TEXT & BUTTON HANDLERS
# ==========================================

@bot.message_handler(func=lambda message: message.text == BROADCAST_CANCEL_BTN)
def cancel_broadcast(message):
    if message.chat.id != ADMIN_ID:
        return
    awaiting_broadcast[str(message.chat.id)] = False
    bot.reply_to(message, "❌ Broadcast ကို ပယ်ဖျက်လိုက်ပါပြီ။", reply_markup=get_main_menu())

@bot.message_handler(func=lambda message: message.text == "/start 🔄")
def refresh_bot(message):
    send_welcome_and_menu(message)

@bot.message_handler(func=lambda message: message.text == STOP_BTN)
def stop_process(message):
    user_id = str(message.chat.id)
    proc = running_process.get(user_id)
    if proc and proc.poll() is None:
        stop_flags[user_id] = True
        proc.terminate()
        bot.reply_to(message, "⏹ မလုပ်ပေးတော့ဘူးကွာ", reply_markup=get_main_menu())
    else:
        bot.reply_to(message, "ဘာပို့ထားလို့ ရပ်ခိုင်းနေတာလဲဟ", reply_markup=get_main_menu())

# ==========================================
# CALLBACK QUERIES (Link Storage)
# ==========================================

@bot.callback_query_handler(func=lambda call: call.data in {"claim_link", "my_quota", "admin_upload", "admin_stats"})
def handle_callback(call: types.CallbackQuery) -> None:
    if call.from_user is None or call.message is None:
        return

    user_id = call.from_user.id
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)

    if call.data == "my_quota":
        used, remaining = store.usage(user_id, current_date(), DAILY_LIMIT)
        bot.send_message(chat_id, f"Today’s quota: <b>{used}/{DAILY_LIMIT}</b> used — <b>{remaining}</b> remaining.")
        return

    if call.data == "claim_link":
        result = store.claim_link(user_id, current_date(), DAILY_LIMIT)
        if result.status == "claimed" and result.url:
            safe_url = html.escape(result.url, quote=True)
            bot.send_message(
                chat_id,
                "Your authorized link:\n"
                f"<code>{safe_url}</code>\n\n"
                f"Today: <b>{result.used}/{DAILY_LIMIT}</b> used — <b>{result.remaining}</b> remaining.",
                disable_web_page_preview=True,
            )
            return
        if result.status == "quota_reached":
            bot.send_message(chat_id, f"Your daily limit of <b>{DAILY_LIMIT}</b> links has been reached. It resets at midnight ({html.escape(str(TIMEZONE))}).")
            return
        if result.status == "inventory_empty":
            bot.send_message(chat_id, "No authorized links are currently available. Please try again later.")
            return
        bot.send_message(chat_id, "Please tap “Get ready link” again.")
        return

    if not is_admin(user_id):
        bot.send_message(chat_id, "This admin function is not available for your account.")
        return

    if call.data == "admin_upload":
        with _pending_lock:
            _pending_upload_admins.add(user_id)
        bot.send_message(
            chat_id,
            "Send one UTF-8 <code>.txt</code> file now. Put one authorized <code>http://</code> or <code>https://</code> URL on each line. "
            "Duplicate, invalid, and already-stored rows will be ignored. Send /cancel to stop.",
        )
        return

    if call.data == "admin_stats":
        stats = store.stats()
        bot.send_message(
            chat_id,
            "Inventory status\n\n"
            f"Available: <b>{stats['available']}</b>\n"
            f"Assigned: <b>{stats['assigned']}</b>\n"
            f"Total: <b>{stats['total']}</b>",
        )

# ==========================================
# CORE PROCESSING TASKS
# ==========================================

def run_generator_task(chat_id, user_id, content_bytes, progress_msg_id=None):
    acquired = file_lock.acquire(timeout=90)
    if not acquired:
        bot.send_message(chat_id, "ငါအလုပ်များနေပါတယ်ဟ၊ ခဏနေမှ ထပ်ကြိုးစားပေး", reply_markup=get_main_menu())
        return

    input_path = "input.txt"

    try:
        if stop_flags.get(user_id):
            if progress_msg_id:
                bot.edit_message_text(chat_id=chat_id, message_id=progress_msg_id, text="⏹ မလုပ်ပေးတော့ဘူးကွာ")
            return

        if progress_msg_id:
            bot.edit_message_text(chat_id=chat_id, message_id=progress_msg_id, text="TXT ရပြီ Token ပြန်ပေးမယ် စောင့်နေ.")

        fixed_content = normalize_cookie_text(content_bytes)
        with open(input_path, "wb") as f:
            f.write(fixed_content)

        if stop_flags.get(user_id):
            bot.send_message(chat_id, "⏹ မလုပ်ပေးတော့ဘူးကွ", reply_markup=get_main_menu())
            return

        proc = subprocess.Popen(
            [sys.executable, 'nf-token-generator.py'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        running_process[user_id] = proc

        try:
            stdout, stderr = proc.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            bot.send_message(chat_id, "⏱️ ကြာလွန်းလို့ ရပ်လိုက်ပြီ ထပ်ကြိုးစားပေးကွာ", reply_markup=get_main_menu())
            return
        finally:
            running_process.pop(user_id, None)

        if stop_flags.get(user_id):
            bot.send_message(chat_id, "⏹ မလုပ်ပေးတော့ဘူးကွ", reply_markup=get_main_menu())
            return

        match = re.search(r'(https://netflix\.com/\?nftoken=[^\s]+)', stdout or "")
        if match:
            clean_url = match.group(1)
            reply = (
                f"ရပြီဝေ့:\n\n{clean_url}\n\n"
                "⚠️ **သတိထား** - ဒီလင့်ခ်က 15 minutes လောက်ပဲရမှာနော်"
            )
            bot.send_message(chat_id, reply, parse_mode='Markdown', reply_markup=get_main_menu())
        else:
            err_snippet = (stderr or "Cookie ပျက်နေတာထင်တယ် နောက်တစ်ခုစမ်းကွာ")[:500]
            bot.send_message(chat_id, "Token မတွေ့ဘူး နောက်တစ်ခုစမ်း", reply_markup=get_main_menu())
            bot.send_message(ADMIN_ID, f"⚠️ Token မတွေ့ဘူး (user {user_id}):\n```\n{err_snippet}\n```", parse_mode="Markdown")

    except Exception as e:
        bot.send_message(chat_id, f"Error တက်ကုန်ပြီဟ: {e}", reply_markup=get_main_menu())
    finally:
        running_process.pop(user_id, None)
        if os.path.exists(input_path):
            os.remove(input_path)
        file_lock.release()

# ==========================================
# FILE & MESSAGE HANDLERS
# ==========================================

@bot.message_handler(content_types=["document"])
def process_document_merged(message: types.Message):
    if message.from_user is None or message.document is None:
        return
    log_user(message)
    user_id = message.chat.id
    str_user_id = str(user_id)

    # Check if admin is uploading Authorized Links
    with _pending_lock:
        upload_expected = user_id in _pending_upload_admins

    if upload_expected and is_admin(user_id):
        document = message.document
        filename = document.file_name or "uploaded-links.txt"
        valid_name = filename.lower().endswith(".txt")
        valid_mime = document.mime_type in {None, "text/plain"}
        
        if not valid_name or not valid_mime:
            bot.reply_to(message, "Only a plain-text .txt file is accepted. The upload is still pending.")
            return
        if document.file_size and document.file_size > MAX_UPLOAD_BYTES:
            bot.reply_to(message, f"The file is too large. Maximum allowed size is {MAX_UPLOAD_BYTES // 1024} KB.")
            return

        try:
            file_info = bot.get_file(document.file_id)
            raw_data = bot.download_file(file_info.file_path)
            text = raw_data.decode("utf-8-sig")
        except UnicodeDecodeError:
            bot.reply_to(message, "The file must be UTF-8 text. The upload is still pending.")
            return
        except Exception:
            logger.exception("Could not download an admin TXT file")
            bot.reply_to(message, "The file could not be read. Please try again.")
            return

        try:
            result = store.add_links(text.splitlines(), filename)
        except Exception:
            logger.exception("Could not import TXT links")
            bot.reply_to(message, "Import failed; no confirmation was made. Please try again.")
            return

        with _pending_lock:
            _pending_upload_admins.discard(user_id)

        stats = store.stats()
        bot.reply_to(
            message,
            "Import complete.\n\n"
            f"Added: <b>{result.added}</b>\n"
            f"Skipped as duplicate: <b>{result.duplicates}</b>\n"
            f"Skipped as invalid: <b>{result.invalid}</b>\n"
            f"Available inventory: <b>{stats['available']}</b>",
        )
        return

    # If NOT an admin upload state, process as Netflix Cookie file
    stop_flags[str_user_id] = False
    file_name = message.document.file_name.lower()

    if not file_name.endswith('.txt'):
        bot.reply_to(message, ".txt ဖိုင်ပဲပို့ဟ", reply_markup=get_main_menu())
        return

    progress_msg = bot.reply_to(message, "ဖိုင်ငါရပြီ - အစဉ်လိုက်ပဲသွားမယ်ကွ(Queue)...")

    def task():
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        run_generator_task(message.chat.id, str_user_id, downloaded_file, progress_msg.message_id)

    Thread(target=task).start()

@bot.message_handler(content_types=['text'])
def handle_text_merged(message: types.Message):
    if message.from_user is None or message.text.startswith('/'):
        return
    if message.text in ["/start 🔄", STOP_BTN, BROADCAST_CANCEL_BTN]:
        return

    log_user(message)
    chat_id = message.chat.id
    user_id = str(chat_id)

    # Check for Admin pending upload state warning
    with _pending_lock:
        upload_expected = chat_id in _pending_upload_admins
    if upload_expected and is_admin(chat_id):
        bot.reply_to(message, "Please send the expected .txt document, or use /cancel.")
        return

    # Check for Admin Broadcast
    if chat_id == ADMIN_ID and awaiting_broadcast.get(user_id):
        awaiting_broadcast[user_id] = False
        broadcast_text = message.text
        sent, failed = 0, 0
        for uid in active_users.keys():
            try:
                bot.send_message(int(uid), broadcast_text)
                sent += 1
            except Exception:
                failed += 1
        bot.send_message(
            chat_id,
            f"📢 Broadcast ပို့ပြီးပါပြီ。\n✅ အောင်မြင်: {sent}\n❌ မအောင်မြင်: {failed}",
            reply_markup=get_main_menu()
        )
        return

    # Normal user sending pasted cookie text
    stop_flags[user_id] = False
    progress_msg = bot.reply_to(message, "စာသားရပြီ အစဉ်လိုက်ပဲသွားမယ်ကွ(Queue)...")

    def task():
        run_generator_task(chat_id, user_id, message.text.encode('utf-8'), progress_msg.message_id)

    Thread(target=task).start()

# ==========================================
# MAIN EXECUTION
# ==========================================

if __name__ == "__main__":
    Thread(target=run_web, daemon=True).start()
    logger.info("Bot စတင် အလုပ်လုပ်နေပါပြီ (Queue စနစ် နှင့် Link Database ဖြင့်)...")
    bot.infinity_polling(skip_pending=False, timeout=30, long_polling_timeout=30)
