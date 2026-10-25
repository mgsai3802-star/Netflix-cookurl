"""Combined Telegram bot with Users, VIPs, Bans, Cookies, Settings, TV Login Automation, and Quotas."""

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

FORCE_JOIN_CHANNEL = "@premiumlogsren"
LOG_CHANNEL_ID = "-1003483107033"

ADMIN_ID = 1847021130
ADMIN_IDS = {ADMIN_ID}

active_users: dict[str, str] = {}
banned_users: set[str] = set()
vip_users: set[str] = set()
bot_is_open = True

# Daily limit default (Can be changed in supabase bot_settings)
DEFAULT_DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "10"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024))) # 20MB for ZIP

def configured_timezone() -> ZoneInfo:
    timezone_name = os.getenv("APP_TIMEZONE", "Asia/Yangon")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise RuntimeError(f"APP_TIMEZONE is invalid: {timezone_name}") from error

TIMEZONE = configured_timezone()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("combined_bot")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)
app = Flask(__name__)

file_lock = threading.Lock()
running_process = {}
awaiting_broadcast = {}
_pending_upload_admins: set[int] = set()
_pending_lock = threading.Lock()

TV_LOGIN_BTN = "📺 TV Login"
BROADCAST_CANCEL_BTN = "❌ Broadcast ပယ်ဖျက်"

MENU_BUTTONS = [
    "Netflix login link ယူရန်", 
    "ကျွန်ုပ်၏ Quota 📊", 
    "လက်ကျန်စာရင်း 📋", 
    "ZIP ဖိုင် တင်ရန် 📤", 
    "Admin Panel ⚙️", 
    "🌟 Get VIP 🌟",
    TV_LOGIN_BTN
]

COOKIE_LINE_RE = re.compile(
    r'^(?P<domain>\S+)\s+(?P<flag1>TRUE|FALSE)\s+(?P<path>/\S*?)\s*(?=TRUE|FALSE)'
    r'(?P<secure>TRUE|FALSE)\s+(?P<expiry>\d+)\s+(?P<name>\S+)\s+(?P<value>.*)$'
)

# ==========================================
# SUPABASE & HELPER FUNCTIONS
# ==========================================

def load_cached_data():
    global bot_is_open
    try:
        users_res = supabase.table('users').select('user_id, username').execute()
        for u in users_res.data: active_users[u['user_id']] = u.get('username') or 'Unknown'

        vip_res = supabase.table('vip_users').select('user_id').execute()
        for v in vip_res.data: vip_users.add(v['user_id'])
        
        ban_res = supabase.table('banned_users').select('user_id').execute()
        for b in ban_res.data: banned_users.add(b['user_id'])
            
        status_res = supabase.table('bot_settings').select('value').eq('key', 'bot_status').execute()
        if status_res.data and status_res.data[0]['value'] == 'closed':
            bot_is_open = False
            
        logger.info(f"Loaded {len(active_users)} Users, {len(vip_users)} VIPs, {len(banned_users)} Banned from Supabase.")
    except Exception as e:
        logger.error(f"Error loading cached data: {e}")

def get_daily_limit() -> int:
    try:
        res = supabase.table('bot_settings').select('value').eq('key', 'daily_limit').execute()
        if res.data: return int(res.data[0]['value'])
    except Exception as e: logger.error(f"Get daily limit error: {e}")
    return DEFAULT_DAILY_LIMIT

def get_quota(uid: str, date_str: str) -> int:
    record_id = f"{uid}_{date_str}"
    try:
        res = supabase.table('user_quotas').select('used_count').eq('id', record_id).execute()
        if res.data: return res.data[0]['used_count']
    except Exception as e: logger.error(f"Get quota error: {e}")
    return 0

def increment_quota(uid: str, date_str: str) -> int:
    record_id = f"{uid}_{date_str}"
    current = get_quota(uid, date_str)
    new_count = current + 1
    try:
        supabase.table('user_quotas').upsert({
            'id': record_id, 'user_id': uid, 'claim_date': date_str, 'used_count': new_count
        }).execute()
    except Exception as e: logger.error(f"Increment quota error: {e}")
    return new_count

def get_available_cookies_count() -> int:
    try:
        res = supabase.table('cookies').select('id', count='exact').execute()
        return res.count if res.count else 0
    except Exception: return 0

def is_admin(user_id: int) -> bool: return user_id in ADMIN_IDS
def is_banned(user_id: int | str) -> bool: return str(user_id) in banned_users
def is_vip(user_id: int | str) -> bool: return str(user_id) in vip_users
def current_date() -> str: return datetime.now(TIMEZONE).date().isoformat()

def log_user(message):
    user_id = str(message.chat.id)
    username = message.from_user.username or message.from_user.first_name or "Unknown"
    if user_id not in active_users or active_users[user_id] != username:
        active_users[user_id] = username
        try: supabase.table('users').upsert({'user_id': user_id, 'username': username}).execute()
        except Exception as e: logger.error(f"Error upserting user to DB: {e}")

def is_user_subscribed(user_id: int) -> bool:
    if is_admin(user_id): return True
    try:
        member = bot.get_chat_member(FORCE_JOIN_CHANNEL, user_id)
        return member.status in ['creator', 'administrator', 'member', 'restricted']
    except Exception as e:
        logger.error(f"Subscription check error: {e}")
        return False

def check_bot_open(message) -> bool:
    if not bot_is_open and not is_admin(message.from_user.id):
        bot.reply_to(message, "⚠️ <b>Bot ကို ယာယီ ပိတ်ထားပါသည် (Maintenance)။</b>\n\nAdmin မှ ပြန်လည်မဖွင့်ပေးမချင်း အသုံးပြု၍ မရသေးပါ။", parse_mode="HTML")
        return False
    return True

def send_log_to_channel(user, status_text: str):
    try:
        bot_info = bot.get_me()
        bot_username = bot_info.username
        time_now = datetime.now(TIMEZONE).strftime("%I:%M %p (%d %b %Y)")
        uid_str = str(user.id)
        masked_id = uid_str[:3] + "****" + uid_str[-2:] if len(uid_str) > 5 else uid_str
        
        log_text = (
            f"🎬 <b>PREMIUM UPDATE</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 User : {user.first_name}\n"
            f"🆔 <code>{masked_id}</code>\n"
            f"🎉 Status : {status_text}\n"
            f"⏱️ Time : {time_now}\n\n"
        )
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🚀 Open Bot Now", url=f"https://t.me/{bot_username}"))
        bot.send_message(LOG_CHANNEL_ID, log_text, parse_mode="HTML", reply_markup=markup)
    except Exception as e:
        logger.error(f"Log Error: {e}")

# ==========================================
# COOKIE PROCESSING (ORIGINAL NF-TOKEN)
# ==========================================

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
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Cookie": f"NetflixId={netflix_id}; SecureNetflixId={secure_netflix_id}"
            },
            allow_redirects=True, timeout=15
        )

        url_lower = res.url.lower()
        text_lower = res.text.lower()
        if any(k in url_lower for k in ["youraccount", "signup", "finishsignup"]) or \
           any(k in text_lower for k in ["restart your membership", "finish sign up", "step 1 of"]):
            return False
        return True
    except Exception as e:
        logger.error(f"Cookie validation error: {e}")
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

def run_generator_task(chat_id, user, content_bytes, progress_msg_id=None):
    user_id = str(user.id)
    acquired = file_lock.acquire(timeout=90)
    if not acquired:
        bot.send_message(chat_id, "ငါအလုပ်များနေပါတယ်ဟ၊ ခဏနေမှ ထပ်ကြိုးစားပေး", reply_markup=get_main_menu(user.id))
        return

    try:
        if progress_msg_id: bot.edit_message_text(chat_id=chat_id, message_id=progress_msg_id, text="TXT ရပြီ Cookie ကို စစ်ဆေးနေပါတယ်...")

        if not check_cookie_active(content_bytes):
            bot.send_message(chat_id, "❌ ပို့လိုက်တဲ့ Cookie က သက်တမ်းကုန် (သို့) Sign up ပြန်တောင်းနေပါတယ်။ တခြားတစ်ခု စမ်းကြည့်ပါ။", reply_markup=get_main_menu(user.id))
            if progress_msg_id: bot.delete_message(chat_id=chat_id, message_id=progress_msg_id)
            return

        if progress_msg_id: bot.edit_message_text(chat_id=chat_id, message_id=progress_msg_id, text="Token ပြောင်းနေပါပြီ ခဏစောင့်ပါ။")

        clean_url = execute_token_generation(content_bytes, user_id, chat_id)
        if clean_url:
            bot.send_message(
                chat_id,
                f"ရပြီဝေ့:\n\n{clean_url}\n\n⚠️ <b>သတိထား</b> - ဒီလင့်ခ်က 15 minutes လောက်ပဲရမှာနော်",
                disable_web_page_preview=True
            )
            send_log_to_channel(user, "ကိုယ်ပိုင် Cookie ဖြင့် Token Link ထုတ်ယူသွားပါသည်။")
        else:
            bot.send_message(chat_id, "Token မတွေ့ဘူး (သို့မဟုတ် အကောင့်ပျက်နေသည်) နောက်တစ်ခုစမ်း", reply_markup=get_main_menu(user.id))
            bot.send_message(ADMIN_ID, f"⚠️ Token မတွေ့ဘူး (user {user_id})")
    except Exception as e:
        bot.send_message(chat_id, f"Error တက်ကုန်ပြီဟ: {e}", reply_markup=get_main_menu(user.id))
    finally:
        file_lock.release()

# ==========================================
# TV LOGIN AUTOMATION (API SUBMITTER)
# ==========================================

def submit_tv_code_to_api(service: str, tv_code: str, cookie_text: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": cookie_text, "Content-Type": "application/json"
    }
    try:
        if service == "netflix":
            res = requests.post("https://www.netflix.com/api/shakti/tv8/activation", json={"code": tv_code}, headers=headers, timeout=15)
        elif service == "hbo":
            res = requests.post("https://oauth.max.com/device/activate", json={"user_code": tv_code}, headers=headers, timeout=15)
        elif service == "prime":
            res = requests.post("https://www.amazon.com/a/c/action/device/auth", data={"code": tv_code}, headers=headers, timeout=15)
        elif service == "crunchyroll":
            res = requests.post("https://www.crunchyroll.com/activate/api", json={"device_code": tv_code}, headers=headers, timeout=15)
        elif service == "spotify":
            res = requests.post("https://accounts.spotify.com/api/device/activate", json={"code": tv_code}, headers=headers, timeout=15)
        else:
            return {"ok": False, "msg": "Service မသိပါ"}

        if res.status_code in [200, 201, 204]:
            return {"ok": True, "msg": f"✅ {service.upper()} TV ချိတ်ဆက်မှု အောင်မြင်ပါသည်။ မျက်နှာပြင်ကို ကြည့်ပါ။"}
        else:
            return {"ok": False, "msg": f"❌ Code မှားနေသည် သို့မဟုတ် Cookie အလုပ်မလုပ်ပါ။ (Status: {res.status_code})"}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "msg": f"⚠️ Server ချိတ်ဆက်မှု Error: {str(e)}"}

# ==========================================
# KEYBOARDS
# ==========================================

def get_join_channel_keyboard():
    markup = types.InlineKeyboardMarkup()
    url = f"https://t.me/{FORCE_JOIN_CHANNEL.replace('@', '')}"
    markup.add(types.InlineKeyboardButton("📢 Channel သို့ Join မည်", url=url))
    markup.add(types.InlineKeyboardButton("🔄 ကျွန်တော် Join ပြီးပါပြီ (စစ်ဆေးရန်)", callback_data="check_joined"))
    return markup

def get_main_menu(user_id=None):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    # 🌟 Netflix ခလုတ်ကို အပေါ်ဆုံးမှာ တစ်လိုင်းတည်း (အကြီးဆုံး/အကျယ်ဆုံး) ထားခြင်း
    markup.add(types.KeyboardButton("Netflix login link ယူရန်"))
    
    # ကျန်တဲ့ခလုတ်များကို အောက်တွင် ၂ ခု တစ်တန်းစီ ထားခြင်း
    markup.add(types.KeyboardButton(TV_LOGIN_BTN), types.KeyboardButton("ကျွန်ုပ်၏ Quota 📊"))
    markup.add(types.KeyboardButton("လက်ကျန်စာရင်း 📋"), types.KeyboardButton("/start 🔄"))
    
    if user_id and is_admin(user_id):
        markup.add(types.KeyboardButton("ZIP ဖိုင် တင်ရန် 📤"), types.KeyboardButton("Admin Panel ⚙️"))
    elif user_id and not is_admin(user_id):
        markup.add(types.KeyboardButton("🌟 Get VIP 🌟"))
        
    return markup


def get_tv_login_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("📺 Netflix TV Login", callback_data="tv_netflix"),
        types.InlineKeyboardButton("🟣 HBO TV Login", callback_data="tv_hbo")
    )
    markup.row(
        types.InlineKeyboardButton("📺 Prime Video TV", callback_data="tv_prime"),
        types.InlineKeyboardButton("🍥 Crunchyroll TV", callback_data="tv_crunchyroll")
    )
    markup.row(
        types.InlineKeyboardButton("🟢 Spotify TV Login", callback_data="tv_spotify")
    )
    # Back ခလုတ် ထပ်ထည့်ခြင်း
    markup.row(
        types.InlineKeyboardButton("🔙 Back (နောက်သို့)", callback_data="tv_back")
    )
    return markup

def get_broadcast_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(types.KeyboardButton(BROADCAST_CANCEL_BTN))
    return markup

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
# COMMAND HANDLERS
# ==========================================

@bot.message_handler(commands=['open'])
def open_bot(message):
    global bot_is_open
    if not is_admin(message.from_user.id): return
    bot_is_open = True
    try: supabase.table('bot_settings').upsert({'key': 'bot_status', 'value': 'open'}).execute()
    except Exception as e: logger.error(f"Open Bot Error: {e}")
    bot.reply_to(message, "✅ <b>Bot ကို ဖွင့်လိုက်ပါပြီ။</b>\nအသုံးပြုသူများ ပုံမှန်အတိုင်း ပြန်လည်အသုံးပြုနိုင်ပါပြီ။", parse_mode="HTML")

@bot.message_handler(commands=['close'])
def close_bot(message):
    global bot_is_open
    if not is_admin(message.from_user.id): return
    bot_is_open = False
    try: supabase.table('bot_settings').upsert({'key': 'bot_status', 'value': 'closed'}).execute()
    except Exception as e: logger.error(f"Close Bot Error: {e}")
    bot.reply_to(message, "🛑 <b>Bot ကို ပိတ်လိုက်ပါပြီ (Maintenance)။</b>\nအသုံးပြုသူများ ခေတ္တအသုံးပြုခွင့် ရပ်ဆိုင်းသွားပါမည်။", parse_mode="HTML")

@bot.message_handler(commands=['start', 'menu'])
def send_welcome_and_menu(message):
    if is_banned(message.chat.id):
        bot.reply_to(message, "🚫 သင့်ကို Bot အသုံးပြုခွင့် ပိတ်ထားပါသည် (Blocked)။")
        return
    if not check_bot_open(message): return
    log_user(message)
    
    if not is_user_subscribed(message.from_user.id):
        bot.reply_to(
            message,
            f"✦ <b>ချန်နယ် Join ရန် လိုအပ်ပါသည်</b>\n\nမင်္ဂလာပါ {message.from_user.first_name}! 👋\nBot ကို စတင်အသုံးပြုရန် အောက်ပါချန်နယ်သို့ အရင်ဆုံး Join ပေးပါ။",
            reply_markup=get_join_channel_keyboard(),
            parse_mode="HTML"
        )
        return

    bot.reply_to(
        message, 
        "မင်္ဂလာပါ ဝေ့ -Netflix Cookie ပါတဲ့ .txtဖိုင် ဖြစ်ဖြစ် textဖြစ်ဖြစ် ပို့လိုက်ကွာ\n\nအောက်က ခလုတ်‌တွေကိုနှိပ်ပြီး Admin တင်ပေးထားတဲ့ အသင့်သုံး link‌ တွေထုတ်ကွာ", 
        reply_markup=get_main_menu(message.from_user.id)
    )

@bot.message_handler(func=lambda message: message.text == BROADCAST_CANCEL_BTN)
def cancel_broadcast(message):
    if message.chat.id != ADMIN_ID: return
    awaiting_broadcast[str(message.chat.id)] = False
    bot.reply_to(message, "❌ Broadcast ကို ပယ်ဖျက်လိုက်ပါပြီ။", reply_markup=get_main_menu(message.from_user.id))

@bot.message_handler(commands=['ntv', 'hbotv', 'pvtv', 'crtv', 'sptv'])
def handle_tv_commands(message):
    user_id = message.from_user.id
    if is_banned(user_id): return
    if not check_bot_open(message): return
    
    # 📌 QUOTA CHECK (TV Login အတွက်ပါ ကန့်သတ်ချက်စစ်ဆေးခြင်း)
    limit_val = get_daily_limit()
    user_limit = 999999 if (is_admin(user_id) or is_vip(user_id)) else limit_val
    used = get_quota(str(user_id), current_date())

    if used >= user_limit:
        bot.reply_to(message, f"🚫 ဒီနေ့အတွက် သတ်မှတ်ထားတဲ့ <b>{limit_val}</b> ခု ပြည့်သွားပြီကွ။ ညသန်းခေါင်ယံမှာ Quota ပြန်လည်စတင်မယ်ကွ", parse_mode="HTML")
        return

    code_split = message.text.split(maxsplit=1)
    if len(code_split) < 2:
        bot.reply_to(message, f"⚠️ TV Code ရိုက်ထည့်ရန် လိုအပ်ပါသည်။\nဥပမာ: <code>{code_split[0]} 123456</code>", parse_mode="HTML")
        return
        
    tv_code = code_split[1].strip().upper()
    cmd = code_split[0].lower()
    
    service_map = {
        '/ntv': 'netflix', '/hbotv': 'hbo', '/pvtv': 'prime', '/crtv': 'crunchyroll', '/sptv': 'spotify'
    }
    service = service_map.get(cmd)
    
    progress = bot.reply_to(message, f"⏳ {service.upper()} Code <code>{tv_code}</code> ကို စစ်ဆေးနေပါသည်...", parse_mode="HTML")
    
    def process_tv_login():
        acquired = file_lock.acquire(timeout=60)
        if not acquired:
            bot.edit_message_text(chat_id=message.chat.id, message_id=progress.message_id, text="⚠️ Server အလုပ်များနေပါသည်။ ခဏနေမှ ထပ်စမ်းပါ။")
            return
        try:
            # supbase တွင် TV Login အတွက် Cookie ရယူခြင်း
            res = supabase.table('cookies').select('id, content').eq('service_type', service).limit(1).execute()
            if not res.data:
                bot.edit_message_text(chat_id=message.chat.id, message_id=progress.message_id, text=f"❌ {service.upper()} အတွက် အသင့်သုံး Cookie လက်ကျန်မရှိတော့ပါ။ Admin ကို အကြောင်းကြားပေးပါ။")
                return
                
            cookie_id = res.data[0]['id']
            cookie_content = res.data[0]['content']
            
            result = submit_tv_code_to_api(service, tv_code, cookie_content)
            
            if result["ok"]:
                # အောင်မြင်ပါက Cookie ကိုဖျက်ပြီး Quota နှုတ်မည်
                supabase.table('cookies').delete().eq('id', cookie_id).execute()
                new_used = increment_quota(str(user_id), current_date())
                quota_info = "👑 <b>VIP/Admin Account (Unlimited)</b>" if (is_admin(user_id) or is_vip(user_id)) else f"ယနေ့ Quota <b>{new_used}/{limit_val}</b> ခု သုံးထားသည်"
                
                bot.edit_message_text(chat_id=message.chat.id, message_id=progress.message_id, text=f"{result['msg']}\n\n{quota_info}", parse_mode="HTML")
                send_log_to_channel(message.from_user, f"{service.upper()} TV ချိတ်ဆက်မှု အောင်မြင်ပါသည်။")
            else:
                bot.edit_message_text(chat_id=message.chat.id, message_id=progress.message_id, text=result["msg"])
        except Exception as e:
            bot.edit_message_text(chat_id=message.chat.id, message_id=progress.message_id, text=f"⚠️ Error: {e}")
        finally:
            file_lock.release()
            
    Thread(target=process_tv_login).start()

# ==========================================
# MENU BUTTONS HANDLER
# ==========================================

@bot.message_handler(func=lambda message: message.text in MENU_BUTTONS)
def handle_menu_buttons(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text

    if is_banned(user_id):
        bot.send_message(chat_id, "🚫 သင့်ကို Bot အသုံးပြုခွင့် ပိတ်ထားပါသည် (Blocked)။")
        return

    if not check_bot_open(message): return
    log_user(message)

    if not is_user_subscribed(user_id):
        bot.send_message(chat_id, "✦ <b>ချန်နယ် Join ရန် လိုအပ်ပါသည်</b>\n\nBot ကို ဆက်လက်အသုံးပြုရန် အောက်ပါချန်နယ်သို့ အရင်ဆုံး Join ပေးပါ။", reply_markup=get_join_channel_keyboard(), parse_mode="HTML")
        return

    if text == "ကျွန်ုပ်၏ Quota 📊":
        if is_admin(user_id) or is_vip(user_id):
            bot.send_message(chat_id, "👑 သင်ဟာ Admin/VIP ဖြစ်တဲ့အတွက် Quota အကန့်အသတ်မရှိ (Unlimited) သုံးနိုင်ပါတယ်။")
        else:
            limit_val = get_daily_limit()
            used = get_quota(str(user_id), current_date())
            remaining = max(0, limit_val - used)
            bot.send_message(chat_id, f"ဒီနေ့ Quota: <b>{used}/{limit_val}</b> ခု သုံးထားတယ် — <b>{remaining}</b> ခု ကျန်ပါသေးတယ်ကွ", parse_mode="HTML")
        return

    elif text == "လက်ကျန်စာရင်း 📋":
        available_pool = get_available_cookies_count()
        bot.send_message(chat_id, f"📋 <b>လက်ကျန်စာရင်း အခြေအနေ</b>\n\nPool ထဲတွင် အသင့်ရှိသော Netflix Cookie: <b>{available_pool}</b> ခု", parse_mode="HTML")
        return

    elif text == "🌟 Get VIP 🌟":
        bot.send_message(chat_id, "🌟 VIP အကောင့်ရယူရန် ဆက်သွယ်ပါ 👉 https://t.me/Ren2512", disable_web_page_preview=True)
        return
        
    elif text == TV_LOGIN_BTN:
        bot.send_message(chat_id, "📺 <b>TV Login Services</b>\n\nအောက်ပါ Service များထဲမှ တစ်ခုကို ရွေးချယ်ပါ:", reply_markup=get_tv_login_keyboard(), parse_mode="HTML")
        return

    elif text == "Netflix login link ယူရန်":
        limit_val = get_daily_limit()
        user_limit = 999999 if (is_admin(user_id) or is_vip(user_id)) else limit_val
        used = get_quota(str(user_id), current_date())
        
        if used >= user_limit:
            bot.send_message(chat_id, f"ဒီနေ့အတွက် သတ်မှတ်ထားတဲ့ <b>{limit_val}</b> ခု ပြည့်သွားပြီကွ။ ညသန်းခေါင်ယံမှာ Quota ပြန်လည်စတင်မယ်ကွ", parse_mode="HTML")
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
                    # Netflix အတွက် service_type က netflix (သို့မဟုတ် null) ဖြစ်တာကိုပဲ ဆွဲမယ်
                    res = supabase.table('cookies').select('id, content').or_('service_type.eq.netflix,service_type.is.null').limit(1).execute()
                    if not res.data:
                        break # Pool is empty

                    cookie_id = res.data[0]['id']
                    content_text = res.data[0]['content']
                    content_bytes = content_text.encode('utf-8')

                    supabase.table('cookies').delete().eq('id', cookie_id).execute()

                    if not check_cookie_active(content_bytes):
                        continue

                    url_result = execute_token_generation(content_bytes, str(user_id), chat_id)
                    if url_result:
                        clean_url = url_result
                        break

                if clean_url:
                    new_used = increment_quota(str(user_id), current_date())
                    quota_info = "👑 <b>VIP/Admin Account (Unlimited)</b>" if (is_admin(user_id) or is_vip(user_id)) else f"ယနေ့ <b>{new_used}/{limit_val}</b> ခု သုံးထားတယ်ကွာ — <b>{max(0, limit_val - new_used)}</b> ခု ကျန်သေးတယ်ကွာ"

                    bot.edit_message_text(
                        chat_id=chat_id, message_id=wait_msg.message_id,
                        text=(f"ရပြီဝေ့:\n\n{clean_url}\n\n⚠️ <b>သတိထား</b> - ဒီလင့်ခ်က 15 minutes လောက်ပဲရမှာနော်\n\n{quota_info}"),
                        disable_web_page_preview=True, parse_mode="HTML"
                    )
                    send_log_to_channel(message.from_user, "Netflix Token Link ထုတ်ယူသွားပါသည်။")
                else:
                    bot.edit_message_text(chat_id=chat_id, message_id=wait_msg.message_id, text="လောလောဆယ် အဆင်ပြေသော Cookie များ ကုန်နေပါသည်ကွာ။ Admin တင်ပေးတာကို စောင့်ပါဦးကွာ။")
            except Exception as e:
                bot.edit_message_text(chat_id=chat_id, message_id=wait_msg.message_id, text=f"Error တက်ကုန်ပြီဟ: {e}")
            finally:
                file_lock.release()

        Thread(target=process_claim_task).start()
        return

    elif text == "ZIP ဖိုင် တင်ရန် 📤":
        if not is_admin(user_id): return
        with _pending_lock: _pending_upload_admins.add(user_id)
        bot.send_message(chat_id, "📦 <b>.zip ဖိုင်တစ်ခုကို ပို့ပေးပါ။</b>\n(Zip ထဲတွင် Cookie <code>.txt</code> ဖိုင်များ ပါဝင်ရပါမည်)\n\n<i>မှတ်ချက်: Netflix အစား တခြား Service တင်လိုပါက ZIP နာမည်ကို <code>hbo.zip</code>, <code>prime.zip</code> စသဖြင့် ပေးပါ။</i>\n\nမတင်လိုပါက /cancel ကို နှိပ်ပါ။", parse_mode="HTML")
        return

    elif text == "Admin Panel ⚙️":
        if not is_admin(user_id): return
        bot.send_message(chat_id, "⚙️ <b>Admin Management Panel</b>\nအောက်ပါ လုပ်ဆောင်ချက်များကို ရွေးချယ်ပါ:", reply_markup=admin_panel_keyboard(), parse_mode="HTML")
        return

# ==========================================
# ADMIN PANEL HANDLERS (CALLBACKS)
# ==========================================

def process_add_vip(message):
    if message.text in ["/start 🔄", BROADCAST_CANCEL_BTN] or message.text in MENU_BUTTONS: return
    uid = message.text.strip()
    vip_users.add(uid)
    try: supabase.table('vip_users').upsert({'user_id': uid}).execute()
    except Exception as e: logger.error(f"Add VIP Error: {e}")
    bot.send_message(message.chat.id, f"🌟 User ID <code>{uid}</code> ကို VIP အဖြစ် သတ်မှတ်လိုက်ပါပြီ။", parse_mode="HTML")

def process_rm_vip(message):
    if message.text in ["/start 🔄", BROADCAST_CANCEL_BTN] or message.text in MENU_BUTTONS: return
    uid = message.text.strip()
    vip_users.discard(uid)
    try: supabase.table('vip_users').delete().eq('user_id', uid).execute()
    except Exception as e: logger.error(f"Remove VIP Error: {e}")
    bot.send_message(message.chat.id, f"❌ User ID <code>{uid}</code> ကို VIP မှ ပယ်ဖျက်လိုက်ပါပြီ။", parse_mode="HTML")

def process_ban(message):
    if message.text in ["/start 🔄", BROADCAST_CANCEL_BTN] or message.text in MENU_BUTTONS: return
    uid = message.text.strip()
    banned_users.add(uid)
    try: supabase.table('banned_users').upsert({'user_id': uid}).execute()
    except Exception as e: logger.error(f"Ban Error: {e}")
    bot.send_message(message.chat.id, f"🚫 User ID <code>{uid}</code> ကို Block လိုက်ပါပြီ။", parse_mode="HTML")

def process_unban(message):
    if message.text in ["/start 🔄", BROADCAST_CANCEL_BTN] or message.text in MENU_BUTTONS: return
    uid = message.text.strip()
    banned_users.discard(uid)
    try: supabase.table('banned_users').delete().eq('user_id', uid).execute()
    except Exception as e: logger.error(f"Unban Error: {e}")
    bot.send_message(message.chat.id, f"✅ User ID <code>{uid}</code> ကို Unblock လုပ်ပေးလိုက်ပါပြီ။", parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call: types.CallbackQuery) -> None:
    if call.from_user is None or call.message is None: return

    user_id = call.from_user.id
    chat_id = call.message.chat.id

    if is_banned(user_id):
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, "🚫 သင့်ကို Bot အသုံးပြုခွင့် ပိတ်ထားပါသည် (Blocked)။")
        return

    # TV Login Button Instructions
    if call.data.startswith("tv_"):
        bot.answer_callback_query(call.id)
        
        # 📌 Back ခလုတ် နှိပ်လျှင် Message ကို ဖျက်ပစ်မည်
        if call.data == "tv_back":
            bot.delete_message(chat_id, call.message.message_id)
            return

        if call.data == "tv_netflix":
            msg = "╔══════════════════════════╗\n║   📺  N E T F L I X   T V\n╚══════════════════════════╝\n\n1. Open Netflix on your TV\n2. Grab the 8-digit code\n3. Send it here\n\nSend /ntv 12345678 or just the code."
        elif call.data == "tv_hbo":
            msg = "╔══════════════════════════╗\n║   🟣  H B O   T V   L O G I N\n╚══════════════════════════╝\n\n1. Open HBO Max / Max on your TV\n2. Grab the 6-digit code\n3. Send it here\n\nSend /hbotv 123456 or just the number."
        elif call.data == "tv_prime":
            msg = "╔══════════════════════════╗\n║   📺  P R I M E   V I D E O   T V\n╚══════════════════════════╝\n\n1. Open Prime Video on your TV\n2. Grab the 6-character code\n3. Send it here\n\nSend /pvtv A7HSUW or just the code."
        elif call.data == "tv_crunchyroll":
            msg = "╔══════════════════════════╗\n║   🍥  C R U N C H Y R O L L   T V\n╚══════════════════════════╝\n\n1. Open Crunchyroll on your TV\n2. Grab the 6-character code\n3. Send it here\n\nSend /crtv ABC123 or just the code."
        elif call.data == "tv_spotify":
            msg = "╔══════════════════════════╗\n║   🟢  S P O T I F Y   T V\n╚══════════════════════════╝\n\n1. Open Spotify on your TV\n2. Grab the pairing code\n3. Send it here\n\nSend /sptv ABCDEF or just the code."
        bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=msg)
        return

    # Channel Check
    if call.data == "check_joined":
        if is_user_subscribed(user_id):
            bot.answer_callback_query(call.id, "✅ အောင်မြင်ပါသည်။ ကြိုဆိုပါတယ်။")
            bot.delete_message(chat_id, call.message.message_id)
            bot.send_message(chat_id, "မင်္ဂလာပါ ဝေ့ - အောက်က ခလုတ်‌တွေကိုနှိပ်ပြီး အသုံးပြုနိုင်ပါပြီ", reply_markup=get_main_menu(user_id))
        else:
            bot.answer_callback_query(call.id, "⚠️ သင်သည် Channel သို့ အရင်ဆုံး Join ရန် လိုအပ်ပါသည်။", show_alert=True)
        return

    bot.answer_callback_query(call.id)

    # Admin Actions
    if not is_admin(user_id): return

    if call.data == "panel_add_vip":
        msg = bot.send_message(chat_id, "🌟 VIP သတ်မှတ်ပေးမည့် <b>User ID</b> ကို ရိုက်ထည့်ပါ:", parse_mode="HTML")
        bot.register_next_step_handler(msg, process_add_vip)
    elif call.data == "panel_rm_vip":
        msg = bot.send_message(chat_id, "❌ VIP စာရင်းမှ ဖယ်ရှားမည့် <b>User ID</b> ကို ရိုက်ထည့်ပါ:", parse_mode="HTML")
        bot.register_next_step_handler(msg, process_rm_vip)
    elif call.data == "panel_list_vip":
        if not vip_users: bot.send_message(chat_id, "🌟 VIP User မရှိသေးပါ။")
        else: bot.send_message(chat_id, f"🌟 <b>VIP User များ ({len(vip_users)} ဦး):</b>\n\n" + "\n".join([f"▪️ <code>{u}</code>" for u in vip_users]), parse_mode="HTML")
    elif call.data == "panel_ban":
        msg = bot.send_message(chat_id, "🚫 Block ပြုလုပ်မည့် <b>User ID</b> ကို ရိုက်ထည့်ပါ:", parse_mode="HTML")
        bot.register_next_step_handler(msg, process_ban)
    elif call.data == "panel_unban":
        msg = bot.send_message(chat_id, "✅ Unblock ပြုလုပ်မည့် <b>User ID</b> ကို ရိုက်ထည့်ပါ:", parse_mode="HTML")
        bot.register_next_step_handler(msg, process_unban)
    elif call.data == "panel_list_banned":
        if not banned_users: bot.send_message(chat_id, "🚫 Block ထားသော User မရှိသေးပါ။")
        else: bot.send_message(chat_id, f"🚫 <b>Block ထားသော User များ ({len(banned_users)} ဦး):</b>\n\n" + "\n".join([f"▪️ <code>{u}</code>" for u in banned_users]), parse_mode="HTML")
    elif call.data == "panel_list_users":
        if not active_users: bot.send_message(chat_id, "လက်ရှိတွင် အသုံးပြုသူ စာရင်း မရှိသေးပါ။")
        else:
            text = f"👥 <b>စုစုပေါင်း အသုံးပြုသူ: {len(active_users)} ဦး</b>\n\n"
            for uid, uname in active_users.items():
                status = " (🚫 Blocked)" if uid in banned_users else " (🌟 VIP)" if uid in vip_users else ""
                text += f"▪️ {uname} (ID: <code>{uid}</code>){status}\n"
            bot.send_message(chat_id, text, parse_mode="HTML")
    elif call.data == "panel_clear":
        try:
            res = supabase.table('cookies').select('id', count='exact').execute()
            total = res.count if res.count else 0
            if total > 0: supabase.table('cookies').delete().gt('id', -1).execute()
            bot.send_message(chat_id, f"🗑 <b>Cookie အဟောင်းများ ရှင်းလင်းခြင်း ပြီးစီးပါပြီ။</b>\n\nဖျက်လိုက်သော ဖိုင်အရေအတွက်: <b>{total}</b> ခု", parse_mode="HTML")
        except Exception as e:
            bot.send_message(chat_id, f"Error: {e}")
    elif call.data == "panel_broadcast":
        awaiting_broadcast[str(chat_id)] = True
        bot.send_message(chat_id, "📢 Broadcast ပို့ချင်တဲ့ စာသားကို ရိုက်ပို့ပါ။\nမလုပ်တော့ဘူးဆိုရင် အောက်က ခလုတ်ကို နှိပ်ပါ။", reply_markup=get_broadcast_menu())

# ==========================================
# FILE & MESSAGE HANDLERS
# ==========================================

@bot.message_handler(content_types=["document"])
def process_document_merged(message: types.Message):
    if message.from_user is None or message.document is None: return
    user_id = message.chat.id

    if is_banned(user_id):
        bot.reply_to(message, "🚫 သင့်ကို Bot အသုံးပြုခွင့် ပိတ်ထားပါသည် (Blocked)။")
        return
    if not check_bot_open(message): return
    log_user(message)
    
    if not is_user_subscribed(message.from_user.id):
        bot.send_message(user_id, "✦ <b>ချန်နယ် Join ရန် လိုအပ်ပါသည်</b>", reply_markup=get_join_channel_keyboard(), parse_mode="HTML")
        return

    with _pending_lock: upload_expected = user_id in _pending_upload_admins

    # -- Admin ZIP Upload Handling --
    if upload_expected and is_admin(user_id):
        document = message.document
        filename = document.file_name or "cookies.zip"
        
        if not filename.lower().endswith(".zip"):
            bot.reply_to(message, "❌ .zip ဖိုင်အမျိုးအစားသာ လက်ခံပါသည်။ ဖိုင်တင်ရန် စောင့်ဆိုင်းနေဆဲဖြစ်ပါသည်။")
            return
        if document.file_size and document.file_size > MAX_UPLOAD_BYTES:
            bot.reply_to(message, f"❌ ဖိုင်ဆိုဒ် ကြီးလွန်းနေပါသည်။")
            return

        progress = bot.reply_to(message, "📦 ZIP ဖိုင်ကို ဒေါင်းလုဒ်ဆွဲပြီး Supabase ထဲသို့ သွင်းနေပါသည်...")

        try:
            file_info = bot.get_file(document.file_id)
            raw_data = bot.download_file(file_info.file_path)
            
            svc_type = document.file_name.lower().replace(".zip", "")
            if svc_type not in ["netflix", "hbo", "prime", "crunchyroll", "spotify"]:
                svc_type = "netflix"
                
            cookies_to_insert = []
            with zip_lib.ZipFile(io.BytesIO(raw_data)) as z:
                for file_info_z in z.infolist():
                    if file_info_z.filename.lower().endswith('.txt') and not file_info_z.is_dir():
                        content = z.read(file_info_z.filename).decode('utf-8', errors='ignore')
                        cookies_to_insert.append({'content': content, 'service_type': svc_type})

            extracted_count = len(cookies_to_insert)
            
            chunk_size = 500
            for i in range(0, extracted_count, chunk_size):
                chunk = cookies_to_insert[i:i + chunk_size]
                supabase.table('cookies').insert(chunk).execute()

            with _pending_lock: _pending_upload_admins.discard(user_id)
            total_pool = get_available_cookies_count()
            bot.edit_message_text(
                chat_id=user_id, message_id=progress.message_id,
                text=(f"✅ <b>ZIP ဖိုင် Supabase ထဲသို့ ဖြေပြီးပါပြီ။ ({svc_type.upper()})</b>\n\n▪️ ယခုထည့်သွင်းလိုက်သော Cookie အရေအတွက်: <b>{extracted_count}</b> ခု\n▪️ စုစုပေါင်း အသင့်ရှိသော Cookie အရေအတွက်: <b>{total_pool}</b> ခု"),
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

    # -- Normal User .txt Upload Handling --
    file_name = message.document.file_name.lower()

    if not file_name.endswith('.txt'):
        bot.reply_to(message, ".txt ဖိုင်ပဲပို့ဟ", reply_markup=get_main_menu(message.from_user.id))
        return

    progress_msg = bot.reply_to(message, "ဖိုင်ငါရပြီ - အစဉ်လိုက်ပဲသွားမယ်ကွ(Queue)...")

    def task():
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        run_generator_task(message.chat.id, message.from_user, downloaded_file, progress_msg.message_id)

    Thread(target=task).start()

@bot.message_handler(content_types=['text'])
def handle_text_merged(message: types.Message):
    if message.from_user is None or message.text.startswith('/'): return
    if message.text in ["/start 🔄", BROADCAST_CANCEL_BTN]: return
    if message.text in MENU_BUTTONS: return

    chat_id = message.chat.id
    user_id = str(chat_id)

    if is_banned(user_id):
        bot.reply_to(message, "🚫 သင့်ကို Bot အသုံးပြုခွင့် ပိတ်ထားပါသည် (Blocked)။")
        return
    if not check_bot_open(message): return
    log_user(message)
    
    if not is_user_subscribed(message.from_user.id):
        bot.send_message(chat_id, "✦ <b>ချန်နယ် Join ရန် လိုအပ်ပါသည်</b>", reply_markup=get_join_channel_keyboard(), parse_mode="HTML")
        return

    with _pending_lock: upload_expected = chat_id in _pending_upload_admins
    if upload_expected and is_admin(chat_id):
        bot.reply_to(message, "ကျေးဇူးပြု၍ .zip ဖိုင်ကို ပို့ပေးပါ သို့မဟုတ် /cancel ကိုနှိပ်ပါ။")
        return

    if chat_id == ADMIN_ID and awaiting_broadcast.get(user_id):
        awaiting_broadcast[user_id] = False
        broadcast_text = message.text
        sent, failed = 0, 0
        for uid in active_users.keys():
            if uid in banned_users: continue
            try:
                bot.send_message(int(uid), broadcast_text)
                sent += 1
            except Exception: failed += 1
        bot.send_message(chat_id, f"📢 Broadcast ပို့ပြီးပါပြီ。\n✅ အောင်မြင်: {sent}\n❌ မအောင်မြင်: {failed}", reply_markup=get_main_menu(chat_id))
        return

    progress_msg = bot.reply_to(message, "စာသားရပြီ အစဉ်လိုက်ပဲသွားမယ်ကွ(Queue)...")

    def task():
        run_generator_task(chat_id, message.from_user, message.text.encode('utf-8'), progress_msg.message_id)

    Thread(target=task).start()

# ==========================================
# MAIN EXECUTION
# ==========================================

@app.route('/')
def alive():
    return "Bot is running online and awake!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    load_cached_data()
    Thread(target=run_web, daemon=True).start()
    logger.info("Bot စတင် အလုပ်လုပ်နေပါပြီ (Supabase & Force Join စနစ် ဖြင့်)...")
    bot.infinity_polling(skip_pending=False, timeout=30, long_polling_timeout=30)
