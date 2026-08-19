"""Combined Telegram bot with Users, VIPs, Bans, Cookies, and Settings fully stored in Supabase."""

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

if not BOT_TOKEN or not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: BOT_TOKEN, SUPABASE_URL, and SUPABASE_KEY must be set in Environment Variables.")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Specific Admin ID
ADMIN_ID = 1847021130
ADMIN_IDS = {ADMIN_ID}

# In-Memory Cache for fast lookups
active_users: dict[str, str] = {}
banned_users: set[str] = set()
vip_users: set[str] = set()

DEFAULT_DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "3"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))) # 20MB for ZIP

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

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)
app = Flask(__name__)

# State Tracking Variables
file_lock = threading.Lock()
running_process = {}
stop_flags = {}
awaiting_broadcast = {}
_pending_upload_admins: set[int] = set()
_pending_lock = threading.Lock()

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
# SUPABASE & HELPER FUNCTIONS
# ==========================================

def load_cached_data():
    """Load Users, VIPs, and Banned lists from Supabase on startup"""
    try:
        # Load all registered users
        users_res = supabase.table('users').select('user_id, username').execute()
        for u in users_res.data:
            active_users[u['user_id']] = u.get('username') or 'Unknown'

        # Load VIPs
        vip_res = supabase.table('vip_users').select('user_id').execute()
        for v in vip_res.data:
            vip_users.add(v['user_id'])
        
        # Load Banned
        ban_res = supabase.table('banned_users').select('user_id').execute()
        for b in ban_res.data:
            banned_users.add(b['user_id'])
            
        logger.info(f"Loaded {len(active_users)} Users, {len(vip_users)} VIPs, {len(banned_users)} Banned from Supabase.")
    except Exception as e:
        logger.error(f"Error loading cached data: {e}")

def get_daily_limit() -> int:
    """Fetch daily limit dynamically from Supabase bot_settings table"""
    try:
        res = supabase.table('bot_settings').select('value').eq('key', 'daily_limit').execute()
        if res.data:
            return int(res.data[0]['value'])
    except Exception as e:
        logger.error(f"Get daily limit error: {e}")
    return DEFAULT_DAILY_LIMIT

def get_quota(uid: str, date_str: str) -> int:
    record_id = f"{uid}_{date_str}"
    try:
        res = supabase.table('user_quotas').select('used_count').eq('id', record_id).execute()
        if res.data:
            return res.data[0]['used_count']
    except Exception as e:
        logger.error(f"Get quota error: {e}")
    return 0

def increment_quota(uid: str, date_str: str) -> int:
    record_id = f"{uid}_{date_str}"
    current = get_quota(uid, date_str)
    new_count = current + 1
    try:
        supabase.table('user_quotas').upsert({
            'id': record_id,
            'user_id': uid,
            'claim_date': date_str,
            'used_count': new_count
        }).execute()
    except Exception as e:
        logger.error(f"Increment quota error: {e}")
    return new_count

def get_available_cookies_count() -> int:
    try:
        res = supabase.table('cookies').select('id', count='exact').execute()
        if res.count is not None:
            return res.count
        return len(res.data) if res.data else 0
    except Exception as e:
        logger.error(f"Count cookies error: {e}")
        return 0

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def is_banned(user_id: int | str) -> bool:
    return str(user_id) in banned_users

def is_vip(user_id: int | str) -> bool:
    return str(user_id) in vip_users

def current_date() -> str:
    return datetime.now(TIMEZONE).date().isoformat()

def log_user(message):
    """Record active user into Supabase and In-memory dict"""
    user_id = str(message.chat.id)
    username = message.from_user.username or message.from_user.first_name or "Unknown"
    
    if user_id not in active_users or active_users[user_id] != username:
        active_users[user_id] = username
        try:
            supabase.table('users').upsert({
                'user_id': user_id,
                'username': username
            }).execute()
        except Exception as e:
            logger.error(f"Error upserting user to DB: {e}")

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
        logger.error(f"Cookie validation error: {e}")
        return False

# ==========================================
# KEYBOARDS
# ==========================================

def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton("/start 🔄"), types.KeyboardButton(STOP_BTN))
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
        keyboard.add(types.InlineKeyboardButton("Admin Panel ⚙️", callback_data="admin_panel"))
    else:
        keyboard.add(types.InlineKeyboardButton("🌟 Get VIP 🌟", url="https://t.me/Ren2512"))
    return keyboard

def admin_panel_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➕ Add VIP", callback_data="panel_add_vip"),
        types.InlineKeyboardButton("➖ Remove VIP", callback_data="panel_rm_vip"),
        types.InlineKeyboardButton("🌟 VIP List", callback_data="panel_list_vip"),
        types.InlineKeyboardButton("🚫 Ban User", callback_data="panel_ban"),
        types.InlineKeyboardButton("✅ Unban User", callback_data="panel_unban"),
        types.InlineKeyboardButton("📜 Banned List", callback_data="panel_list_banned"),
        types.InlineKeyboardButton("👥 All Users", callback_data="panel_list_users"),
        types.InlineKeyboardButton("🗑 Clear Cookie Pool", callback_data="panel_clear"),
        types.InlineKeyboardButton("📢 Broadcast", callback_data="panel_broadcast"),
    )
    return kb

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
    bot.reply_to(message, "မင်္ဂလာပါ ဝေ့ -Netflix Cookie ပါတဲ့ .txtဖိုင် ဖြစ်ဖြစ် textဖြစ်ဖြစ် ပို့လိုက်ကွာ", reply_markup=get_main_menu())
    bot.send_message(
        message.chat.id, "အောက်က ခလုတ်‌တွေကိုနှိပ်ပြီး Admin တင်ပေးထားတဲ့ အသင့်သုံး link‌ တွေထုတ်ကွာ",
        reply_markup=public_keyboard(message.from_user.id), disable_web_page_preview=True
    )

@bot.message_handler(func=lambda message: message.text == BROADCAST_CANCEL_BTN)
def cancel_broadcast(message):
    if message.chat.id != ADMIN_ID: return
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
# ADMIN NEXT STEP HANDLERS
# ==========================================

def process_add_vip(message):
    if message.text in ["/start 🔄", STOP_BTN, BROADCAST_CANCEL_BTN]: return
    uid = message.text.strip()
    vip_users.add(uid)
    try: supabase.table('vip_users').upsert({'user_id': uid}).execute()
    except Exception as e: logger.error(f"Add VIP Error: {e}")
    bot.send_message(message.chat.id, f"🌟 User ID <code>{uid}</code> ကို VIP အဖြစ် သတ်မှတ်လိုက်ပါပြီ။", parse_mode="HTML")

def process_rm_vip(message):
    if message.text in ["/start 🔄", STOP_BTN, BROADCAST_CANCEL_BTN]: return
    uid = message.text.strip()
    vip_users.discard(uid)
    try: supabase.table('vip_users').delete().eq('user_id', uid).execute()
    except Exception as e: logger.error(f"Remove VIP Error: {e}")
    bot.send_message(message.chat.id, f"❌ User ID <code>{uid}</code> ကို VIP မှ ပယ်ဖျက်လိုက်ပါပြီ။", parse_mode="HTML")

def process_ban(message):
    if message.text in ["/start 🔄", STOP_BTN, BROADCAST_CANCEL_BTN]: return
    uid = message.text.strip()
    banned_users.add(uid)
    try: supabase.table('banned_users').upsert({'user_id': uid}).execute()
    except Exception as e: logger.error(f"Ban Error: {e}")
    bot.send_message(message.chat.id, f"🚫 User ID <code>{uid}</code> ကို Block လိုက်ပါပြီ။", parse_mode="HTML")

def process_unban(message):
    if message.text in ["/start 🔄", STOP_BTN, BROADCAST_CANCEL_BTN]: return
    uid = message.text.strip()
    banned_users.discard(uid)
    try: supabase.table('banned_users').delete().eq('user_id', uid).execute()
    except Exception as e: logger.error(f"Unban Error: {e}")
    bot.send_message(message.chat.id, f"✅ User ID <code>{uid}</code> ကို Unblock လုပ်ပေးလိုက်ပါပြီ။", parse_mode="HTML")

# ==========================================
# CALLBACK QUERIES & AUTO TOKEN GENERATOR
# ==========================================

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


@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call: types.CallbackQuery) -> None:
    if call.from_user is None or call.message is None: return

    user_id = call.from_user.id
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)

    if is_banned(user_id):
        bot.send_message(chat_id, "🚫 သင့်ကို Bot အသုံးပြုခွင့် ပိတ်ထားပါသည် (Blocked)။")
        return

    # User Actions
    if call.data == "my_quota":
        if is_admin(user_id) or is_vip(user_id):
            bot.send_message(chat_id, "👑 သင်ဟာ Admin/VIP ဖြစ်တဲ့အတွက် Quota အကန့်အသတ်မရှိ (Unlimited) သုံးနိုင်ပါတယ်။")
        else:
            limit_val = get_daily_limit()
            used = get_quota(str(user_id), current_date())
            remaining = max(0, limit_val - used)
            bot.send_message(chat_id, f"ဒီနေ့ Quota: <b>{used}/{limit_val}</b> ခု သုံးထားတယ် — <b>{remaining}</b> ခု ကျန်ပါသေးတယ်ကွ")
        return

    elif call.data == "claim_link":
        limit_val = get_daily_limit()
        user_limit = 999999 if (is_admin(user_id) or is_vip(user_id)) else limit_val
        used = get_quota(str(user_id), current_date())
        
        if used >= user_limit:
            bot.send_message(chat_id, f"ဒီနေ့အတွက် သတ်မှတ်ထားတဲ့ <b>{limit_val}</b> ခု ပြည့်သွားပြီကွ။ ညသန်းခေါင်ယံမှာ Quota ပြန်လည်စတင်မယ်ကွ")
            return

        def process_claim_task():
            acquired = file_lock.acquire(timeout=90)
            if not acquired:
                bot.send_message(chat_id, "ငါအလုပ်များနေပါတယ်ဟ၊ ခဏနေမှ ထပ်ကြိုးစားပေး")
                return

            wait_msg = bot.send_message(chat_id, "⏳ Cookie ကို စစ်ဆေးပြီး Token ထုတ်နေပါပြီ ခဏစောင့်ကွာ...")
            try:
                clean_url = None
                
                while True:
                    # Fetch from Supabase directly
                    res = supabase.table('cookies').select('id, content').limit(1).execute()
                    if not res.data:
                        break # Pool is empty

                    cookie_id = res.data[0]['id']
                    content_text = res.data[0]['content']
                    content_bytes = content_text.encode('utf-8')

                    # Delete it immediately so others don't claim it
                    supabase.table('cookies').delete().eq('id', cookie_id).execute()

                    if not check_cookie_active(content_bytes):
                        continue

                    url_result = execute_token_generation(content_bytes, str(user_id), chat_id)
                    
                    if url_result:
                        clean_url = url_result
                        break

                if clean_url:
                    new_used = increment_quota(str(user_id), current_date())
                    safe_url = html.escape(clean_url, quote=True)
                    quota_info = "👑 <b>VIP/Admin Account (Unlimited)</b>" if (is_admin(user_id) or is_vip(user_id)) else f"ယနေ့ <b>{new_used}/{limit_val}</b> ခု သုံးထားတယ်ကွာ — <b>{max(0, limit_val - new_used)}</b> ခု ကျန်သေးတယ်ကွာ"

                    bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=wait_msg.message_id,
                        text=(f"ရပြီဝေ့:\n\n{safe_url}\n\n⚠️ <b>သတိထား</b> - ဒီလင့်ခ်က 15 minutes လောက်ပဲရမှာနော်\n\n{quota_info}"),
                        disable_web_page_preview=True
                    )
                else:
                    bot.edit_message_text(chat_id=chat_id, message_id=wait_msg.message_id, text="လောလောဆယ် အဆင်ပြေသော Cookie များ ကုန်နေပါသည်ကွာ။ Admin တင်ပေးတာကို စောင့်ပါဦးကွာ။")
            except Exception as e:
                bot.edit_message_text(chat_id=chat_id, message_id=wait_msg.message_id, text=f"Error တက်ကုန်ပြီဟ: {e}")
            finally:
                file_lock.release()

        Thread(target=process_claim_task).start()
        return

    # Admin Panel Actions
    if not is_admin(user_id): return

    if call.data == "admin_upload":
        with _pending_lock: _pending_upload_admins.add(user_id)
        bot.send_message(chat_id, "📦 <b>.zip ဖိုင်တစ်ခုကို ပို့ပေးပါ။</b>\n(Zip ထဲတွင် Netflix Cookie <code>.txt</code> ဖိုင်များ ပါဝင်ရပါမည်)\nမတင်လိုပါက /cancel ကို နှိပ်ပါ။", parse_mode="HTML")
        
    elif call.data == "admin_stats":
        available_pool = get_available_cookies_count()
        bot.send_message(chat_id, f"📋 <b>လက်ကျန်စာရင်း အခြေအနေ</b>\n\nPool ထဲတွင်ရှိသော Cookie ဖိုင်အရေအတွက်: <b>{available_pool}</b> ခု", parse_mode="HTML")

    elif call.data == "admin_panel":
        bot.send_message(chat_id, "⚙️ <b>Admin Management Panel</b>\nအောက်ပါ လုပ်ဆောင်ချက်များကို ရွေးချယ်ပါ:", reply_markup=admin_panel_keyboard(), parse_mode="HTML")

    elif call.data == "panel_add_vip":
        msg = bot.send_message(chat_id, "🌟 VIP သတ်မှတ်ပေးမည့် <b>User ID</b> ကို ရိုက်ထည့်ပါ:", parse_mode="HTML")
        bot.register_next_step_handler(msg, process_add_vip)

    elif call.data == "panel_rm_vip":
        msg = bot.send_message(chat_id, "❌ VIP စာရင်းမှ ဖယ်ရှားမည့် <b>User ID</b> ကို ရိုက်ထည့်ပါ:", parse_mode="HTML")
        bot.register_next_step_handler(msg, process_rm_vip)

    elif call.data == "panel_list_vip":
        try:
            res = supabase.table('vip_users').select('user_id').execute()
            db_vips = [v['user_id'] for v in res.data] if res.data else []
            if not db_vips:
                bot.send_message(chat_id, "🌟 VIP User မရှိသေးပါ။")
            else:
                vip_users.clear()
                vip_users.update(db_vips)
                text = f"🌟 <b>VIP User များ ({len(db_vips)} ဦး):</b>\n\n"
                for uid in db_vips:
                    uname = active_users.get(str(uid), "Unknown")
                    text += f"▪️ {uname} (ID: <code>{uid}</code>)\n"
                bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception as e:
            bot.send_message(chat_id, f"Error: {e}")

    elif call.data == "panel_ban":
        msg = bot.send_message(chat_id, "🚫 Block ပြုလုပ်မည့် <b>User ID</b> ကို ရိုက်ထည့်ပါ:", parse_mode="HTML")
        bot.register_next_step_handler(msg, process_ban)

    elif call.data == "panel_unban":
        msg = bot.send_message(chat_id, "✅ Unblock ပြုလုပ်မည့် <b>User ID</b> ကို ရိုက်ထည့်ပါ:", parse_mode="HTML")
        bot.register_next_step_handler(msg, process_unban)

    elif call.data == "panel_list_banned":
        try:
            res = supabase.table('banned_users').select('user_id').execute()
            db_banned = [b['user_id'] for b in res.data] if res.data else []
            if not db_banned:
                bot.send_message(chat_id, "🚫 Block ထားသော User မရှိသေးပါ။")
            else:
                banned_users.clear()
                banned_users.update(db_banned)
                text = f"🚫 <b>Block ထားသော User များ ({len(db_banned)} ဦး):</b>\n\n"
                for uid in db_banned:
                    uname = active_users.get(str(uid), "Unknown")
                    text += f"▪️ {uname} (ID: <code>{uid}</code>)\n"
                bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception as e:
            bot.send_message(chat_id, f"Error: {e}")
    elif call.data == "panel_list_users":
        try:
            # Supabase ထဲမှ User စာရင်း အကုန်လုံးကို တိုက်ရိုက်ဆွဲယူခြင်း
            res = supabase.table('users').select('user_id, username').execute()
            db_users = res.data or []
            
            if not db_users:
                bot.send_message(chat_id, "လက်ရှိတွင် အသုံးပြုသူ စာရင်း မရှိသေးပါ။")
            else:
                text = f"👥 <b>စုစုပေါင်း အသုံးပြုသူ: {len(db_users)} ဦး</b>\n\n"
                for u in db_users:
                    uid = str(u['user_id'])
                    uname = u.get('username') or "Unknown"
                    status = ""
                    if uid in banned_users: 
                        status = " (🚫 Blocked)"
                    elif uid in vip_users: 
                        status = " (🌟 VIP)"
                    text += f"▪️ {uname} (ID: <code>{uid}</code>){status}\n"
                    
                bot.send_message(chat_id, text, parse_mode="HTML")
        except Exception as e:
            bot.send_message(chat_id, f"Users ဖတ်ရာတွင် Error တက်ပါသည်: {e}")

    elif call.data == "panel_clear":
        try:
            res = supabase.table('cookies').select('id', count='exact').execute()
            total = res.count if res.count else 0
            if total > 0:
                supabase.table('cookies').delete().gt('id', -1).execute()
            bot.send_message(chat_id, f"🗑 <b>Cookie အဟောင်းများ ရှင်းလင်းခြင်း ပြီးစီးပါပြီ။</b>\n\nဖျက်လိုက်သော ဖိုင်အရေအတွက်: <b>{total}</b> ခု", parse_mode="HTML")
        except Exception as e:
            bot.send_message(chat_id, f"Error: {e}")

    elif call.data == "panel_broadcast":
        awaiting_broadcast[str(chat_id)] = True
        bot.send_message(chat_id, "📢 Broadcast ပို့ချင်တဲ့ စာသားကို ရိုက်ပို့ပါ။\nမလုပ်တော့ဘူးဆိုရင် အောက်က ခလုတ်ကို နှိပ်ပါ။", reply_markup=get_broadcast_menu())


# ==========================================
# FILE & MESSAGE HANDLERS
# ==========================================

def run_generator_task(chat_id, user_id, content_bytes, progress_msg_id=None):
    acquired = file_lock.acquire(timeout=90)
    if not acquired:
        bot.send_message(chat_id, "ငါအလုပ်များနေပါတယ်ဟ၊ ခဏနေမှ ထပ်ကြိုးစားပေး", reply_markup=get_main_menu())
        return

    try:
        if progress_msg_id: bot.edit_message_text(chat_id=chat_id, message_id=progress_msg_id, text="TXT ရပြီ Cookie ကို စစ်ဆေးနေပါတယ်...")

        if not check_cookie_active(content_bytes):
            bot.send_message(chat_id, "❌ ပို့လိုက်တဲ့ Cookie က သက်တမ်းကုန် (သို့) Sign up ပြန်တောင်းနေပါတယ်။ တခြားတစ်ခု စမ်းကြည့်ပါ။", reply_markup=get_main_menu())
            if progress_msg_id: bot.delete_message(chat_id=chat_id, message_id=progress_msg_id)
            return

        if progress_msg_id: bot.edit_message_text(chat_id=chat_id, message_id=progress_msg_id, text="Token ပြောင်းနေပါပြီ ခဏစောင့်ပါ။")

        clean_url = execute_token_generation(content_bytes, user_id, chat_id)
        if clean_url:
            bot.send_message(chat_id, f"ရပြီဝေ့:\n\n{clean_url}\n\n⚠️ <b>သတိထား</b> - ဒီလင့်ခ်က 15 minutes လောက်ပဲရမှာနော်", reply_markup=get_main_menu())
        else:
            bot.send_message(chat_id, "Token မတွေ့ဘူး (သို့မဟုတ် အကောင့်ပျက်နေသည်) နောက်တစ်ခုစမ်း", reply_markup=get_main_menu())
            bot.send_message(ADMIN_ID, f"⚠️ Token မတွေ့ဘူး (user {user_id})")
    except Exception as e:
        bot.send_message(chat_id, f"Error တက်ကုန်ပြီဟ: {e}", reply_markup=get_main_menu())
    finally:
        file_lock.release()

@bot.message_handler(content_types=["document"])
def process_document_merged(message: types.Message):
    if message.from_user is None or message.document is None: return
    user_id = message.chat.id
    str_user_id = str(user_id)

    if is_banned(user_id):
        bot.reply_to(message, "🚫 သင့်ကို Bot အသုံးပြုခွင့် ပိတ်ထားပါသည် (Blocked)။")
        return

    log_user(message)

    with _pending_lock: upload_expected = user_id in _pending_upload_admins

    if upload_expected and is_admin(user_id):
        document = message.document
        filename = document.file_name or "cookies.zip"
        
        if not filename.lower().endswith(".zip"):
            bot.reply_to(message, "❌ .zip ဖိုင်အမျိုးအစားသာ လက်ခံပါသည်။ ဖိုင်တင်ရန် စောင့်ဆိုင်းနေဆဲဖြစ်ပါသည်။")
            return
        if document.file_size and document.file_size > MAX_UPLOAD_BYTES:
            bot.reply_to(message, f"❌ ဖိုင်ဆိုဒ် ကြီးလွန်းနေပါသည်။ အများဆုံး {MAX_UPLOAD_BYTES // (1024*1024)} MB သာ လက်ခံပါသည်။")
            return

        progress = bot.reply_to(message, "📦 ZIP ဖိုင်ကို ဒေါင်းလုဒ်ဆွဲပြီး Supabase ထဲသို့ သွင်းနေပါသည်...")

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
            
            # Batch Insert into Supabase (Chunks of 500)
            chunk_size = 500
            for i in range(0, extracted_count, chunk_size):
                chunk = cookies_to_insert[i:i + chunk_size]
                supabase.table('cookies').insert(chunk).execute()

            with _pending_lock: _pending_upload_admins.discard(user_id)

            total_pool = get_available_cookies_count()
            bot.edit_message_text(
                chat_id=user_id, message_id=progress.message_id,
                text=(f"✅ <b>ZIP ဖိုင် Supabase ထဲသို့ ဖြေပြီးပါပြီ။</b>\n\n▪️ ယခုထည့်သွင်းလိုက်သော Cookie အရေအတွက်: <b>{extracted_count}</b> ခု\n▪️ စုစုပေါင်း အသင့်ရှိသော Cookie အရေအတွက်: <b>{total_pool}</b> ခု"),
                parse_mode="HTML"
            )
            return
        except zip_lib.BadZipFile:
            bot.edit_message_text(chat_id=user_id, message_id=progress.message_id, text="❌ ZIP ဖိုင် ပျက်နေပါသည်။")
            return
        except Exception as e:
            logger.exception("ZIP extract failed")
            bot.edit_message_text(chat_id=user_id, message_id=progress.message_id, text=f"❌ Error ဖြစ်သွားပါသည်: {e}")
            return

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
    if message.from_user is None or message.text.startswith('/'): return
    if message.text in ["/start 🔄", STOP_BTN, BROADCAST_CANCEL_BTN]: return

    chat_id = message.chat.id
    user_id = str(chat_id)

    if is_banned(user_id):
        bot.reply_to(message, "🚫 သင့်ကို Bot အသုံးပြုခွင့် ပိတ်ထားပါသည် (Blocked)။")
        return

    log_user(message)

    with _pending_lock: upload_expected = chat_id in _pending_upload_admins
    if upload_expected and is_admin(chat_id):
        bot.reply_to(message, "ကျေးဇူးပြု၍ .zip ဖိုင်ကို ပို့ပေးပါ သို့မဟုတ် /cancel ကိုနှိပ်ပါ။")
        return

    if chat_id == ADMIN_ID and awaiting_broadcast.get(user_id):
        awaiting_broadcast[user_id] = False
        broadcast_text = message.text
        sent, failed = 0, 0
        try:
            # Fetch all users from Supabase for broadcast
            res = supabase.table('users').select('user_id').execute()
            if res.data:
                for u in res.data:
                    uid = u['user_id']
                    if uid in banned_users: continue
                    try:
                        bot.send_message(int(uid), broadcast_text)
                        sent += 1
                    except Exception: 
                        failed += 1
        except Exception as e:
            logger.error(f"Broadcast error fetching users: {e}")
            
        bot.send_message(chat_id, f"📢 Broadcast ပို့ပြီးပါပြီ。\n✅ အောင်မြင်: {sent}\n❌ မအောင်မြင်: {failed}", reply_markup=get_main_menu())
        return

    stop_flags[user_id] = False
    progress_msg = bot.reply_to(message, "စာသားရပြီ အစဉ်လိုက်ပဲသွားမယ်ကွ(Queue)...")

    def task():
        run_generator_task(chat_id, user_id, message.text.encode('utf-8'), progress_msg.message_id)

    Thread(target=task).start()

# ==========================================
# MAIN EXECUTION
# ==========================================

if __name__ == "__main__":
    load_cached_data()
    Thread(target=run_web, daemon=True).start()
    logger.info("Bot စတင် အလုပ်လုပ်နေပါပြီ (Supabase Dynamic Users, Limits, VIP & Block စနစ် ဖြင့်)...")
    bot.infinity_polling(skip_pending=False, timeout=30, long_polling_timeout=30)
