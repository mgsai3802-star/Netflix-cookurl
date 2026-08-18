"""Combined Telegram bot for Bulk ZIP Cookie Token Generation, Storage Management, and User Blocking."""

from __future__ import annotations

import telebot
from telebot import types
import subprocess
import sys
import os
import re
import io
import zipfile
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

# Blocked Users Set
banned_users: set[str] = set()

# Storage & Folders Configs
BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(BASE_DIR / "link_bot.sqlite3")))
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "3"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))) # 20MB for ZIP
COOKIES_DIR = BASE_DIR / "cookie_pool"
os.makedirs(COOKIES_DIR, exist_ok=True)

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

# Admin Upload Tracking
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

def is_banned(user_id: int | str) -> bool:
    return str(user_id) in banned_users

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

def get_available_cookies_count() -> int:
    return len([f for f in os.listdir(COOKIES_DIR) if f.lower().endswith('.txt')])

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
    keyboard.add(types.InlineKeyboardButton("Link ရယူရန် 🔗", callback_data="claim_link"))
    keyboard.add(types.InlineKeyboardButton("ကျွန်ုပ်၏ Quota 📊", callback_data="my_quota"))
    if is_admin(user_id):
        keyboard.add(types.InlineKeyboardButton("ZIP ဖိုင် တင်ရန် 📤", callback_data="admin_upload"))
        keyboard.add(types.InlineKeyboardButton("လက်ကျန်စာရင်း 📋", callback_data="admin_stats"))
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
    if is_banned(message.chat.id):
        bot.reply_to(message, "🚫 သင့်ကို Bot အသုံးပြုခွင့် ပိတ်ထားပါသည် (Blocked)။")
        return
    log_user(message)
    bot.reply_to(
        message,
        "မင်္ဂလာပါ ဝေ့ -Netflix Cookie ပါတဲ့ .txtဖိုင် ဖြစ်ဖြစ် textဖြစ်ဖြစ် ပို့လိုက်ကွာ",
        reply_markup=get_main_menu()
    )
    bot.send_message(
        message.chat.id,
        "အောက်က ခလုတ်‌တွေကိုနှိပ်ပြီး Admin တင်ပေးထားတဲ့ အသင့်သုံး link‌ တွေထုတ်ကွာ",
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
        status = " (🚫 Blocked)" if uid in banned_users else ""
        user_list_text += f"▪️ {uname} (ID: <code>{uid}</code>){status}\n"
    bot.reply_to(message, user_list_text, parse_mode="HTML")

# --- Block / Unblock Commands ---
@bot.message_handler(commands=['ban', 'block'])
def ban_user(message):
    if message.chat.id != ADMIN_ID:
        return
    parts = message.text.strip().split()
    if len(parts) < 2:
        bot.reply_to(message, "အသုံးပြုပုံ: <code>/ban &lt;user_id&gt;</code>\nဥပမာ: <code>/ban 5786095389</code>", parse_mode="HTML")
        return
    target_id = parts[1].strip()
    banned_users.add(target_id)
    bot.reply_to(message, f"🚫 User ID <code>{target_id}</code> ကို Block လိုက်ပါပြီ။", parse_mode="HTML")

@bot.message_handler(commands=['unban', 'unblock'])
def unban_user(message):
    if message.chat.id != ADMIN_ID:
        return
    parts = message.text.strip().split()
    if len(parts) < 2:
        bot.reply_to(message, "အသုံးပြုပုံ: <code>/unban &lt;user_id&gt;</code>", parse_mode="HTML")
        return
    target_id = parts[1].strip()
    banned_users.discard(target_id)
    bot.reply_to(message, f"✅ User ID <code>{target_id}</code> ကို Unblock လုပ်ပေးလိုက်ပါပြီ။", parse_mode="HTML")

@bot.message_handler(commands=['banned', 'blocklist'])
def list_banned_users(message):
    if message.chat.id != ADMIN_ID:
        return
    if not banned_users:
        bot.reply_to(message, "Block ထားသော User မရှိသေးပါ။")
        return
    text = f"🚫 Block ထားသော User များ ({len(banned_users)} ဦး):\n\n"
    for uid in banned_users:
        text += f"▪️ <code>{uid}</code>\n"
    bot.reply_to(message, text, parse_mode="HTML")
# --------------------------------

# --- Clear Cookie Pool Command (Admin Only) ---
@bot.message_handler(commands=['clearpool', 'clearcookies'])
def clear_cookie_pool(message):
    if message.chat.id != ADMIN_ID:
        return
    
    count = 0
    for f in os.listdir(COOKIES_DIR):
        if f.lower().endswith('.txt'):
            try:
                os.remove(os.path.join(COOKIES_DIR, f))
                count += 1
            except Exception as e:
                logger.error(f"Error deleting file {f}: {e}")
                
    bot.reply_to(message, f"🗑 <b>Cookie အဟောင်းများ ရှင်းလင်းခြင်း ပြီးစီးပါပြီ။</b>\n\nဖျက်လိုက်သော ဖိုင်အရေအတွက်: <b>{count}</b> ခု", parse_mode="HTML")
# ----------------------------------------------
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
    bot.send_message(message.chat.id, "ဖိုင်တင်ခြင်းကို ရပ်ဆိုင်းလိုက်ပါပြီ။")

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
# CALLBACK QUERIES & AUTO TOKEN GENERATOR
# ==========================================

def execute_token_generation(content_bytes: bytes, user_id: str, chat_id: int):
    """Executes nf-token-generator.py and returns clean URL or None."""
    input_path = "input.txt"
    try:
        fixed_content = normalize_cookie_text(content_bytes)
        with open(input_path, "wb") as f:
            f.write(fixed_content)

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
            return None
        finally:
            running_process.pop(user_id, None)

        match = re.search(r'(https://netflix\.com/\?nftoken=[^\s]+)', stdout or "")
        if match:
            return match.group(1)
        return None
    except Exception as e:
        logger.error(f"Execution error: {e}")
        return None
    finally:
        if os.path.exists(input_path):
            os.remove(input_path)


@bot.callback_query_handler(func=lambda call: call.data in {"claim_link", "my_quota", "admin_upload", "admin_stats"})
def handle_callback(call: types.CallbackQuery) -> None:
    if call.from_user is None or call.message is None:
        return

    user_id = call.from_user.id
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)

    if is_banned(user_id):
        bot.send_message(chat_id, "🚫 သင့်ကို Bot အသုံးပြုခွင့် ပိတ်ထားပါသည် (Blocked)။")
        return

    if call.data == "my_quota":
        if is_admin(user_id):
            bot.send_message(chat_id, "👑 သင်ဟာ Admin ဖြစ်တဲ့အတွက် Quota အကန့်အသတ်မရှိ (Unlimited) သုံးနိုင်ပါတယ်။")
        else:
            used, remaining = store.usage(user_id, current_date(), DAILY_LIMIT)
            bot.send_message(chat_id, f"ဒီနေ့ Quota: <b>{used}/{DAILY_LIMIT}</b> ခု သုံးထားတယ် — <b>{remaining}</b> ခု ကျန်ပါသေးတယ်ကွ")
        return

    if call.data == "claim_link":
        user_limit = 999999 if is_admin(user_id) else DAILY_LIMIT
        # Check user quota first
        used, remaining = store.usage(user_id, current_date(), user_limit)
        if used >= user_limit:
            bot.send_message(chat_id, f"ဒီနေ့အတွက် သတ်မှတ်ထားတဲ့ <b>{DAILY_LIMIT}</b> ခု ပြည့်သွားပြီကွ။ ညသန်းခေါင်ယံမှာ Quota ပြန်လည်စတင်မယ်ကွ")
            return

        def process_claim_task():
            acquired = file_lock.acquire(timeout=90)
            if not acquired:
                bot.send_message(chat_id, "ငါအလုပ်များနေပါတယ်ဟ၊ ခဏနေမှ ထပ်ကြိုးစားပေး")
                return

            wait_msg = bot.send_message(chat_id, "⏳ Cookie ကို စစ်ဆေးပြီး Token ထုတ်နေပါပြီ ခဏစောင့်ကွာ...")
            try:
                clean_url = None
                
                # Auto-Loop: Active Cookie တွေ့တဲ့အထိ ပျက်နေတဲ့/Restart ဖြစ်နေတဲ့ Cookie တွေကို ကျော်စစ်ဆေးခြင်း
                while True:
                    cookie_files = [f for f in os.listdir(COOKIES_DIR) if f.lower().endswith('.txt')]
                    if not cookie_files:
                        break

                    target_file = os.path.join(COOKIES_DIR, cookie_files[0])
                    try:
                        with open(target_file, "rb") as f:
                            content_bytes = f.read()
                    except Exception:
                        if os.path.exists(target_file):
                            os.remove(target_file)
                        continue

                    # Try token generation (nf-token-generator.py)
                    url_result = execute_token_generation(content_bytes, str(user_id), chat_id)
                    
                    # Delete the evaluated cookie file so it won't be reused
                    if os.path.exists(target_file):
                        os.remove(target_file)

                    if url_result:
                        clean_url = url_result
                        break
                    # If failed / Restart membership, loop will automatically pick the next file

                if clean_url:
                    store.add_links([clean_url], "pool_claimed")
                    result = store.claim_link(user_id, current_date(), user_limit)
                    
                    safe_url = html.escape(clean_url, quote=True)
                    quota_info = "👑 <b>Admin Account (Unlimited)</b>" if is_admin(user_id) else f"ယနေ့ <b>{result.used}/{DAILY_LIMIT}</b> ခု သုံးထားတယ်ကွာ — <b>{result.remaining}</b> ခု ကျန်သေးတယ်ကွာ"

                    bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=wait_msg.message_id,
                        text=(
                            f"ရပြီဝေ့:\n\n{safe_url}\n\n"
                            f"⚠️ <b>သတိထား</b> - ဒီလင့်ခ်က 15 minutes လောက်ပဲရမှာနော်\n\n"
                            f"{quota_info}"
                        ),
                        disable_web_page_preview=True)
                    bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=wait_msg.message_id,
                        text=(
                            f"ရပြီဝေ့:\n\n{safe_url}\n\n"
                            f"⚠️ <b>သတိထား</b> - ဒီလင့်ခ်က 15 minutes လောက်ပဲရမှာနော်\n\n"
                            f"ယနေ့ <b>{result.used}/{DAILY_LIMIT}</b> ခု သုံးထားတယ်ကွာ — <b>{result.remaining}</b> ခု ကျန်သေးတယ်ကွာ"
                        ),
                        disable_web_page_preview=True
                    )
                else:
                    bot.edit_message_text(
                        chat_id=chat_id, 
                        message_id=wait_msg.message_id, 
                        text="လောလောဆယ် အဆင်ပြေသော Cookie များ ကုန်နေပါသည်ကွာ။ Admin တင်ပေးတာကို စောင့်ပါဦးကွာ။"
                    )
            except Exception as e:
                bot.edit_message_text(chat_id=chat_id, message_id=wait_msg.message_id, text=f"Error တက်ကုန်ပြီဟ: {e}")
            finally:
                file_lock.release()

        Thread(target=process_claim_task).start()
        return

    if not is_admin(user_id):
        bot.send_message(chat_id, "ဒီလုပ်ဆောင်ချက်က Admin အတွက်သာ ဖြစ်ပါတယ်။")
        return

    if call.data == "admin_upload":
        with _pending_lock:
            _pending_upload_admins.add(user_id)
        bot.send_message(
            chat_id,
            "📦 <b>.zip ဖိုင်တစ်ခုကို ပို့ပေးပါ။</b>\n(Zip ထဲတွင် Netflix Cookie <code>.txt</code> ဖိုင်များ ပါဝင်ရပါမည်)\nမတင်လိုပါက /cancel ကို နှိပ်ပါ။",
        )
        return

    if call.data == "admin_stats":
        available_pool = get_available_cookies_count()
        bot.send_message(
            chat_id,
            "📋 <b>လက်ကျန်စာရင်း အခြေအနေ</b>\n\n"
            f"Pool ထဲတွင်ရှိသော Cookie ဖိုင်အရေအတွက်: <b>{available_pool}</b> ခု",
        )

# ==========================================
# FILE & MESSAGE HANDLERS
# ==========================================

def run_generator_task(chat_id, user_id, content_bytes, progress_msg_id=None):
    """Fallback manual token generation for normal user direct TXT upload"""
    acquired = file_lock.acquire(timeout=90)
    if not acquired:
        bot.send_message(chat_id, "ငါအလုပ်များနေပါတယ်ဟ၊ ခဏနေမှ ထပ်ကြိုးစားပေး", reply_markup=get_main_menu())
        return

    try:
        if progress_msg_id:
            bot.edit_message_text(chat_id=chat_id, message_id=progress_msg_id, text="TXT ရပြီ Token ပြန်ပေးမယ် စောင့်နေ.")

        clean_url = execute_token_generation(content_bytes, user_id, chat_id)
        if clean_url:
            reply = (
                f"ရပြီဝေ့:\n\n{clean_url}\n\n"
                "⚠️ <b>သတိထား</b> - ဒီလင့်ခ်က 15 minutes လောက်ပဲရမှာနော်"
            )
            bot.send_message(chat_id, reply, reply_markup=get_main_menu())
        else:
            bot.send_message(chat_id, "Token မတွေ့ဘူး (သို့မဟုတ် အကောင့်ပျက်နေသည်) နောက်တစ်ခုစမ်း", reply_markup=get_main_menu())
            bot.send_message(ADMIN_ID, f"⚠️ Token မတွေ့ဘူး (user {user_id})")

    except Exception as e:
        bot.send_message(chat_id, f"Error တက်ကုန်ပြီဟ: {e}", reply_markup=get_main_menu())
    finally:
        file_lock.release()


@bot.message_handler(content_types=["document"])
def process_document_merged(message: types.Message):
    if message.from_user is None or message.document is None:
        return
    user_id = message.chat.id
    str_user_id = str(user_id)

    if is_banned(user_id):
        bot.reply_to(message, "🚫 သင့်ကို Bot အသုံးပြုခွင့် ပိတ်ထားပါသည် (Blocked)။")
        return

    log_user(message)

    # Check if admin is uploading ZIP
    with _pending_lock:
        upload_expected = user_id in _pending_upload_admins

    if upload_expected and is_admin(user_id):
        document = message.document
        filename = document.file_name or "cookies.zip"
        
        if not filename.lower().endswith(".zip"):
            bot.reply_to(message, "❌ .zip ဖိုင်အမျိုးအစားသာ လက်ခံပါသည်။ ဖိုင်တင်ရန် စောင့်ဆိုင်းနေဆဲဖြစ်ပါသည်။")
            return
        if document.file_size and document.file_size > MAX_UPLOAD_BYTES:
            bot.reply_to(message, f"❌ ဖိုင်ဆိုဒ် ကြီးလွန်းနေပါသည်။ အများဆုံး {MAX_UPLOAD_BYTES // (1024*1024)} MB သာ လက်ခံပါသည်။")
            return

        progress = bot.reply_to(message, "📦 ZIP ဖိုင်ကို ဒေါင်းလုဒ်ဆွဲပြီး ဖြည်နေပါသည်...")

        try:
            file_info = bot.get_file(document.file_id)
            raw_data = bot.download_file(file_info.file_path)
            
            # Unzip TXT files into COOKIES_DIR
            extracted_count = 0
            with zipfile.ZipFile(io.BytesIO(raw_data)) as z:
                for file_info_z in z.infolist():
                    if file_info_z.filename.lower().endswith('.txt') and not file_info_z.is_dir():
                        extracted_filename = f"cookie_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{os.path.basename(file_info_z.filename)}"
                        extracted_path = os.path.join(COOKIES_DIR, extracted_filename)
                        with open(extracted_path, 'wb') as f_out:
                            f_out.write(z.read(file_info_z.filename))
                        extracted_count += 1

            with _pending_lock:
                _pending_upload_admins.discard(user_id)

            total_pool = get_available_cookies_count()
            bot.edit_message_text(
                chat_id=user_id,
                message_id=progress.message_id,
                text=(
                    f"✅ <b>ZIP ဖိုင် ဖြေပြီးပါပြီ။</b>\n\n"
                    f"▪️ ယခုထည့်သွင်းလိုက်သော Cookie အရေအတွက်: <b>{extracted_count}</b> ခု\n"
                    f"▪️ စုစုပေါင်း အသင့်ရှိသော Cookie အရေအတွက်: <b>{total_pool}</b> ခု"
                )
            )
            return
        except zipfile.BadZipFile:
            bot.edit_message_text(chat_id=user_id, message_id=progress.message_id, text="❌ ZIP ဖိုင် ပျက်နေပါသည်။ ကျေးဇူးပြု၍ ပြန်စစ်ဆေးပေးပါ။")
            return
        except Exception as e:
            logger.exception("ZIP extract failed")
            bot.edit_message_text(chat_id=user_id, message_id=progress.message_id, text=f"❌ Error ဖြစ်သွားပါသည်: {e}")
            return

    # If NOT an admin upload state, process as normal individual Netflix Cookie file
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

    chat_id = message.chat.id
    user_id = str(chat_id)

    if is_banned(user_id):
        bot.reply_to(message, "🚫 သင့်ကို Bot အသုံးပြုခွင့် ပိတ်ထားပါသည် (Blocked)။")
        return

    log_user(message)

    # Check for Admin pending upload state warning
    with _pending_lock:
        upload_expected = chat_id in _pending_upload_admins
    if upload_expected and is_admin(chat_id):
        bot.reply_to(message, "ကျေးဇူးပြု၍ .zip ဖိုင်ကို ပို့ပေးပါ သို့မဟုတ် /cancel ကိုနှိပ်ပါ။")
        return

    # Check for Admin Broadcast
    if chat_id == ADMIN_ID and awaiting_broadcast.get(user_id):
        awaiting_broadcast[user_id] = False
        broadcast_text = message.text
        sent, failed = 0, 0
        for uid in active_users.keys():
            if uid in banned_users:
                continue
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
    logger.info("Bot စတင် အလုပ်လုပ်နေပါပြီ (Queue စနစ်, ZIP Pool & Block စနစ် ဖြင့်)...")
    bot.infinity_polling(skip_pending=False, timeout=30, long_polling_timeout=30)
