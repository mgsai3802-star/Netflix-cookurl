"""Telegram Netflix Cookie Bot (Anonymous VIP Mode & Clean Admin)"""

from __future__ import annotations

import telebot
from telebot import types
import subprocess
import sys
import os
import re
import io
import zipfile as zip_lib
import threading
import html
import logging
import requests
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from flask import Flask
from threading import Thread
from supabase import create_client, Client

# ==========================================
# CONFIGURATION & INITIALIZATION
# ==========================================

BOT_TOKEN = os.environ.get('BOT_TOKEN')
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# သတ်မှတ်ထားသော သီးသန့် Group နှင့် Topic (Thread) ID
ALLOWED_GROUP_ID = -1004495699928
ALLOWED_THREAD_ID = 782

if not BOT_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: BOT_TOKEN, SUPABASE_URL, and SUPABASE_KEY must be set in Environment Variables.")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Specific Admin ID (အက်မင်က Private Chat မှာ /admin နဲ့ ZIP တင်ရန်အတွက်သာ)
ADMIN_ID = 1847021130
ADMIN_IDS = {ADMIN_ID}

banned_users: set[str] = set()
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))

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
logger = logging.getLogger("netflix_bot")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)
app = Flask(__name__)

file_lock = threading.Lock()
running_process = {}
stop_flags = {}
_pending_upload_admins: dict[int, str] = {} 
_pending_lock = threading.Lock()

STOP_BTN = "⏹ ဟိုးစတော့"

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

def load_cached_data():
    try:
        ban_res = supabase.table('banned_users').select('user_id').execute()
        for b in ban_res.data:
            banned_users.add(b['user_id'])
        logger.info("Loaded Banned users from Supabase.")
    except Exception as e:
        logger.error(f"Error loading cached data: {e}")

def get_stats() -> int:
    n_count = 0
    try:
        n_res = supabase.table('cookies').select('id', count='exact').execute()
        n_count = n_res.count if n_res.count is not None else (len(n_res.data) if n_res.data else 0)
    except Exception as e:
        logger.error(f"Count cookies error: {e}")
    return n_count

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def is_banned(user_id: int | str) -> bool:
    return str(user_id) in banned_users

def is_chat_allowed(chat_id: int, user_id: int, thread_id: int = None) -> bool:
    # Admin က Private Chat ထဲမှာ /admin သုံးတာနဲ့ ZIP တင်တာတွေ လုပ်လို့ရရန်
    if user_id in ADMIN_IDS and chat_id == user_id:
        return True
    # သတ်မှတ်ထားသော Group နှင့် Topic ထဲတွင်သာ အလုပ်လုပ်မည်
    if chat_id == ALLOWED_GROUP_ID:
        return thread_id == ALLOWED_THREAD_ID
    return False

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

def check_cookie_active(content_bytes: bytes) -> bool:
    try:
        text = content_bytes.decode('utf-8', errors='ignore')
        cookie_dict = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith('#'): continue
            m = COOKIE_LINE_RE.match(stripped)
            if m: cookie_dict[m.group('name')] = m.group('value')
        
        if "NetflixId" not in cookie_dict:
            m = re.search(r'NetflixId=([^;,\s]+)', text)
            if m: cookie_dict["NetflixId"] = m.group(1)
        if "SecureNetflixId" not in cookie_dict:
            m = re.search(r'SecureNetflixId=([^;,\s]+)', text)
            if m: cookie_dict["SecureNetflixId"] = m.group(1)

        netflix_id = cookie_dict.get("NetflixId")
        secure_netflix_id = cookie_dict.get("SecureNetflixId", "")

        if not netflix_id: return False

        res = requests.get(
            "https://www.netflix.com/browse",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Cookie": f"NetflixId={netflix_id}; SecureNetflixId={secure_netflix_id}"
            },
            allow_redirects=True, timeout=15
        )

        url_lower = res.url.lower()
        text_lower = res.text.lower()
        if any(k in url_lower for k in ["youraccount", "signup", "finishsignup"]) or \
           any(k in text_lower for k in ["restart your membership", "finish sign up", "finish your sign-up", "step 1 of"]):
            return False
        return True
    except Exception as e:
        logger.error(f"Netflix validation error: {e}")
        return False

def execute_token_generation(content_bytes: bytes, user_id: str, chat_id: int):
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
        if match: return match.group(1)
        return None
    except Exception as e:
        logger.error(f"Execution error: {e}")
        return None
    finally:
        if os.path.exists(input_path):
            os.remove(input_path)

# ==========================================
# KEYBOARDS
# ==========================================

def public_keyboard() -> types.InlineKeyboardMarkup:
    """ဘယ်သူ့အတွက်မဆို သန့်ရှင်းသော User Keyboard သက်သက်"""
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    keyboard.add(types.InlineKeyboardButton("Netflix လင့်ခ် ထုတ်ရန် 🎬", callback_data="claim_netflix"))
    return keyboard

def admin_panel_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("Netflix ZIP တင်ရန် 📤", callback_data="upload_netflix"),
        types.InlineKeyboardButton("လက်ကျန်စာရင်း 📋", callback_data="admin_stats"),
        types.InlineKeyboardButton("🗑 Clear Pool", callback_data="panel_clear_cookies"),
    )
    return kb

# ==========================================
# WEB SERVER
# ==========================================

@app.route('/')
def alive():
    return "Netflix Bot is running online!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ==========================================
# COMMAND HANDLERS
# ==========================================

@bot.message_handler(commands=['start', 'menu'])
def send_welcome_and_menu(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    thread_id = getattr(message, 'message_thread_id', None)

    if not is_chat_allowed(chat_id, user_id, thread_id):
        return

    if is_banned(user_id):
        return

    bot.reply_to(message, "🎬 <b>Netflix Cookie Bot</b>\nအောက်ပါခလုတ်ကို နှိပ်၍ အကောင့်ထုတ်ယူနိုင်ပါသည်:", reply_markup=public_keyboard(), disable_web_page_preview=True)

@bot.message_handler(commands=['admin'])
def admin_panel_command(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    # Private chat ထဲမှာ Admin သီးသန့်သုံးရန်
    if user_id in ADMIN_IDS and chat_id == user_id:
        bot.reply_to(message, "⚙️ <b>Admin Management Panel</b>\nအောက်ပါ လုပ်ဆောင်ချက်များကို ရွေးချယ်ပါ:", reply_markup=admin_panel_keyboard())

# ==========================================
# CALLBACK QUERIES
# ==========================================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call: types.CallbackQuery) -> None:
    if call.from_user is None or call.message is None: return

    user_id = call.from_user.id
    chat_id = call.message.chat.id
    thread_id = getattr(call.message, 'message_thread_id', None)
    bot.answer_callback_query(call.id)

    if not is_chat_allowed(chat_id, user_id, thread_id):
        return

    if is_banned(user_id):
        return

    if call.data == "claim_netflix":
        def process_claim_task():
            acquired = file_lock.acquire(timeout=90)
            if not acquired:
                return

            wait_msg = bot.send_message(chat_id, "⏳ Netflix Cookie ကို စစ်ဆေးနေပါပြီ ခဏစောင့်ပါ...")
            try:
                final_result = None
                
                while True:
                    res = supabase.table('cookies').select('id, content').limit(1).execute()
                    if not res.data:
                        break

                    cookie_id = res.data[0]['id']
                    content_text = res.data[0]['content']
                    content_bytes = content_text.encode('utf-8')

                    supabase.table('cookies').delete().eq('id', cookie_id).execute()

                    if not check_cookie_active(content_bytes): continue
                    url_result = execute_token_generation(content_bytes, str(user_id), chat_id)
                    if url_result:
                        final_result = url_result
                        break

                if final_result:
                    safe_url = html.escape(final_result, quote=True)
                    reply_text = f"🎬 <b>Netflix အကောင့်ရပါပြီ:</b>\n\n{safe_url}\n\n⚠️ <i>(ဒီလင့်ခ်က 15 မိနစ်ခန့်သာ ခံပါမည်)</i>"
                    bot.edit_message_text(chat_id=chat_id, message_id=wait_msg.message_id, text=reply_text, disable_web_page_preview=True, parse_mode="HTML")
                else:
                    bot.edit_message_text(chat_id=chat_id, message_id=wait_msg.message_id, text="❌ လောလောဆယ် အဆင်ပြေသော Netflix Cookie များ ကုန်နေပါသည်၊ ခဏစောင့်ပါ။")
            except Exception as e:
                bot.edit_message_text(chat_id=chat_id, message_id=wait_msg.message_id, text=f"❌ Error: {e}")
            finally:
                file_lock.release()

        Thread(target=process_claim_task).start()
        return

    # Admin Panel Callbacks (Private Chat only for Admin)
    if not is_admin(user_id): return

    if call.data == "upload_netflix":
        with _pending_lock: _pending_upload_admins[user_id] = "netflix"
        bot.send_message(chat_id, "📦 <b>Netflix အတွက် .zip ဖိုင်ကို ပို့ပေးပါ။</b>", parse_mode="HTML")
        
    elif call.data == "admin_stats":
        n_count = get_stats()
        bot.send_message(chat_id, f"📋 <b>လက်ကျန်စာရင်း:</b> 🎬 Netflix Cookie: <b>{n_count}</b> ခု", parse_mode="HTML")

    elif call.data == "panel_clear_cookies":
        try:
            res = supabase.table('cookies').select('id', count='exact').execute()
            total = res.count if res.count is not None else 0
            if total > 0:
                supabase.table('cookies').delete().gt('id', -1).execute()
            bot.send_message(chat_id, f"🗑 <b>Netflix Cookie များ ရှင်းလင်းပြီးပါပြီ။</b> ဖျက်လိုက်သည့် အရေအတွက်: <b>{total}</b> ခု", parse_mode="HTML")
        except Exception as e:
            bot.send_message(chat_id, f"Error: {e}")

# ==========================================
# FILE UPLOAD HANDLERS (ADMIN)
# ==========================================

@bot.message_handler(content_types=["document"])
def process_document_merged(message: types.Message):
    if message.from_user is None or message.document is None: return
    chat_id = message.chat.id
    user_id = message.from_user.id
    thread_id = getattr(message, 'message_thread_id', None)

    if not is_chat_allowed(chat_id, user_id, thread_id):
        return

    with _pending_lock:
        upload_type = _pending_upload_admins.get(user_id)

    if upload_type == "netflix" and is_admin(user_id):
        document = message.document
        filename = document.file_name or "cookies.zip"
        
        if not filename.lower().endswith(".zip"): return

        progress = bot.reply_to(message, "📦 ZIP ဖိုင်ကို Database ထဲသို့ သွင်းနေပါသည်...")

        try:
            file_info = bot.get_file(document.file_id)
            raw_data = bot.download_file(file_info.file_path)
            
            cookies_to_insert = []
            with zip_lib.ZipFile(io.BytesIO(raw_data)) as z:
                for file_info_z in z.infolist():
                    if file_info_z.filename.lower().endswith('.txt') and not file_info_z.is_dir():
                        content = z.read(file_info_z.filename).decode('utf-8', errors='ignore')
                        cookies_to_insert.append({'content': content})

            extracted_count = len(cookies_to_insert)
            chunk_size = 500
            for i in range(0, extracted_count, chunk_size):
                chunk = cookies_to_insert[i:i + chunk_size]
                supabase.table('cookies').insert(chunk).execute()

            with _pending_lock: _pending_upload_admins.pop(user_id, None)
            total_pool = get_stats()

            bot.edit_message_text(chat_id=chat_id, message_id=progress.message_id, text=f"✅ ZIP ဖိုင် ထည့်သွင်းပြီးပါပြီ။ စုစုပေါင်း: {total_pool} ခု")
            return
        except Exception as e:
            bot.edit_message_text(chat_id=chat_id, message_id=progress.message_id, text=f"❌ Error: {e}")
            return

# ==========================================
# MAIN EXECUTION
# ==========================================

if __name__ == "__main__":
    load_cached_data()
    Thread(target=run_web, daemon=True).start()
    logger.info("Netflix Bot Started (Clean User View & Admin /admin command)...")
    bot.infinity_polling(skip_pending=False, timeout=30, long_polling_timeout=30)
