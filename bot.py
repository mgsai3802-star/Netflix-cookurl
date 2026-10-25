"""Combined Telegram bot with Users, VIPs, Bans, Cookies, Settings, TV Login (Playwright Browser Automation)."""

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
import logging
import requests
import time
import html
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from flask import Flask
from threading import Thread
from supabase import create_client, Client
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

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

DEFAULT_DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "10"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))

def configured_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Yangon"))
    except ZoneInfoNotFoundError:
        return ZoneInfo("Asia/Yangon")

TIMEZONE = configured_timezone()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("combined_bot")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)
app = Flask(__name__)

file_lock = threading.Lock()
running_process = {}
awaiting_broadcast = {}
_pending_upload_admins: dict[int, str] = {}
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
        for u in users_res.data:
            active_users[u['user_id']] = u.get('username') or 'Unknown'

        vip_res = supabase.table('vip_users').select('user_id').execute()
        for v in vip_res.data:
            vip_users.add(v['user_id'])

        ban_res = supabase.table('banned_users').select('user_id').execute()
        for b in ban_res.data:
            banned_users.add(b['user_id'])

        status_res = supabase.table('bot_settings').select('value').eq('key', 'bot_status').execute()
        if status_res.data and status_res.data[0]['value'] == 'closed':
            bot_is_open = False
            
    except Exception as e:
        logger.error(f"Cache load error: {e}")

def get_daily_limit() -> int:
    try:
        res = supabase.table('bot_settings').select('value').eq('key', 'daily_limit').execute()
        if res.data:
            return int(res.data[0]['value'])
    except Exception as e:
        logger.error(f"Get limit error: {e}")
    return DEFAULT_DAILY_LIMIT

def get_quota(uid: str, date_str: str) -> int:
    try:
        res = supabase.table('user_quotas').select('used_count').eq('id', f"{uid}_{date_str}").execute()
        if res.data:
            return res.data[0]['used_count']
    except Exception as e:
        logger.error(f"Get quota error: {e}")
    return 0

def increment_quota(uid: str, date_str: str) -> int:
    new_count = get_quota(uid, date_str) + 1
    try:
        supabase.table('user_quotas').upsert({
            'id': f"{uid}_{date_str}", 
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
        if res.count:
            return res.count
        return 0
    except Exception as e:
        logger.error(f"Get cookies count error: {e}")
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
    uid = str(message.chat.id)
    uname = message.from_user.username or message.from_user.first_name or "Unknown"
    
    if uid not in active_users or active_users[uid] != uname:
        active_users[uid] = uname
        try:
            supabase.table('users').upsert({'user_id': uid, 'username': uname}).execute()
        except Exception as e:
            logger.error(f"User log error: {e}")

def is_user_subscribed(user_id: int) -> bool:
    if is_admin(user_id):
        return True
    try:
        member = bot.get_chat_member(FORCE_JOIN_CHANNEL, user_id)
        if member.status in ['creator', 'administrator', 'member', 'restricted']:
            return True
        return False
    except Exception as e:
        logger.error(f"Subscription check error: {e}")
        return False

def check_bot_open(message) -> bool:
    if not bot_is_open and not is_admin(message.from_user.id):
        bot.reply_to(message, "⚠️ <b>Bot ကို ယာယီ ပိတ်ထားပါသည် (Maintenance)။</b>", parse_mode="HTML")
        return False
    return True

def send_log_to_channel(user, status_text: str):
    try:
        bot_info = bot.get_me()
        time_now = datetime.now(TIMEZONE).strftime("%I:%M %p (%d %b %Y)")
        uid_str = str(user.id)
        
        if len(uid_str) > 5:
            masked_id = uid_str[:3] + "****" + uid_str[-2:]
        else:
            masked_id = uid_str
            
        safe_name = html.escape(user.first_name or "Unknown")
            
        log_text = (
            f"🎬 <b>PREMIUM UPDATE</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 User : {safe_name}\n"
            f"🆔 <code>{masked_id}</code>\n"
            f"🎉 Status : {status_text}\n"
            f"⏱️ Time : {time_now}\n\n"
        )
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🚀 Open Bot Now", url=f"https://t.me/{bot_info.username}"))
        
        bot.send_message(LOG_CHANNEL_ID, log_text, parse_mode="HTML", reply_markup=markup)
    except Exception as e:
        logger.error(f"Log send error: {e}")

# ==========================================
# COOKIE PROCESSING (ORIGINAL NF-TOKEN)
# ==========================================

def normalize_cookie_text(raw_bytes: bytes) -> bytes:
    fixed_lines = []
    text = raw_bytes.decode('utf-8', errors='ignore')
    
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            fixed_lines.append(line)
            continue
            
        m = COOKIE_LINE_RE.match(line)
        if m:
            fixed_line = '\t'.join([
                m.group('domain'), 
                m.group('flag1'), 
                m.group('path'), 
                m.group('secure'), 
                m.group('expiry'), 
                m.group('name'), 
                m.group('value')
            ])
            fixed_lines.append(fixed_line)
        else:
            fixed_lines.append(line)
            
    return ('\n'.join(fixed_lines) + '\n').encode('utf-8')

def check_cookie_active(content_bytes: bytes) -> bool:
    try:
        cookie_dict = {}
        text = content_bytes.decode('utf-8', errors='ignore')
        
        for line in text.splitlines():
            line = line.strip()
            m = COOKIE_LINE_RE.match(line)
            if m:
                cookie_dict[m.group('name')] = m.group('value')
        
        netflix_id = cookie_dict.get("NetflixId")
        if not netflix_id:
            match = re.search(r'NetflixId=([^;,\s]+)', text)
            if match:
                netflix_id = match.group(1)
                
        secure_netflix_id = cookie_dict.get("SecureNetflixId")
        if not secure_netflix_id:
            match = re.search(r'SecureNetflixId=([^;,\s]+)', text)
            if match:
                secure_netflix_id = match.group(1)
        else:
            secure_netflix_id = ""
        
        if not netflix_id:
            return False
            
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Cookie": f"NetflixId={netflix_id}; SecureNetflixId={secure_netflix_id}"
        }
        
        res = requests.get(
            "https://www.netflix.com/browse", 
            headers=headers, 
            allow_redirects=True, 
            timeout=15
        )
        
        url_lower = res.url.lower()
        text_lower = res.text.lower()
        
        if any(k in url_lower for k in ["youraccount", "signup"]):
            return False
            
        if any(k in text_lower for k in ["restart your membership", "finish sign up", "step 1 of"]):
            return False
            
        return True
    except Exception as e:
        logger.error(f"Cookie check error: {e}")
        return False

def execute_token_generation(content_bytes: bytes, user_id: str, chat_id: int):
    input_path = f"input_{user_id}_{datetime.now().timestamp()}.txt"
    try:
        fixed_content = normalize_cookie_text(content_bytes)
        with open(input_path, "wb") as f:
            f.write(fixed_content)
            
        proc = subprocess.Popen(
            [sys.executable, 'nf-token-generator.py'], 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True
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

def run_generator_task(chat_id, user, content_bytes, progress_msg_id=None):
    acquired = file_lock.acquire(timeout=90)
    if not acquired:
        bot.send_message(chat_id, "ငါအလုပ်များနေပါတယ်ဟ၊ ခဏနေမှ ထပ်ကြိုးစားပေး")
        return
        
    try:
        if progress_msg_id:
            bot.edit_message_text(chat_id=chat_id, message_id=progress_msg_id, text="TXT ရပြီ Cookie ကို စစ်ဆေးနေပါတယ်...")
            
        if not check_cookie_active(content_bytes):
            bot.send_message(chat_id, "❌ ပို့လိုက်တဲ့ Cookie က သက်တမ်းကုန် (သို့) Sign up ပြန်တောင်းနေပါတယ်။ တခြားတစ်ခု စမ်းကြည့်ပါ။")
            if progress_msg_id:
                bot.delete_message(chat_id=chat_id, message_id=progress_msg_id)
            return
            
        if progress_msg_id:
            bot.edit_message_text(chat_id=chat_id, message_id=progress_msg_id, text="Token ပြောင်းနေပါပြီ ခဏစောင့်ပါ။")
            
        clean_url = execute_token_generation(content_bytes, str(user.id), chat_id)
        
        if clean_url:
            bot.send_message(
                chat_id, 
                f"ရပြီဝေ့:\n\n{clean_url}\n\n⚠️ <b>သတိထား</b> - ဒီလင့်ခ်က 15 minutes လောက်ပဲရမှာနော်", 
                disable_web_page_preview=True
            )
            send_log_to_channel(user, "ကိုယ်ပိုင် Cookie ဖြင့် Token Link ထုတ်ယူသွားပါသည်။")
        else:
            bot.send_message(chat_id, "Token မတွေ့ဘူး (သို့မဟုတ် အကောင့်ပျက်နေသည်) နောက်တစ်ခုစမ်း")
            
    except Exception as e:
        bot.send_message(chat_id, f"Error: {html.escape(str(e))}")
    finally:
        file_lock.release()

# ==========================================
# 🌟 HEADLESS BROWSER TV LOGIN (PLAYWRIGHT)
# ==========================================

def submit_tv_code_playwright(service: str, tv_code: str, cookie_text: str) -> dict:
    """နောက်ကွယ်မှ Browser ဖြင့် TV Code ကို Auto ရိုက်ထည့်ပေးမည့် စနစ်"""
    playwright_cookies = []
    
    for line in cookie_text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
            
        m = COOKIE_LINE_RE.match(line)
        if m:
            domain = m.group('domain')
            
            # 📌 Filter cookies to prevent invalid domain protocol errors
            if service == "spotify" and not ("spotify" in domain):
                continue
            elif service == "netflix" and not ("netflix" in domain):
                continue
            elif service == "hbo" and not ("max" in domain or "hbo" in domain):
                continue
            elif service == "prime" and not ("amazon" in domain or "prime" in domain):
                continue
            elif service == "crunchyroll" and not ("crunchyroll" in domain):
                continue

            playwright_cookies.append({
                "name": m.group('name'),
                "value": m.group('value'),
                "domain": domain,
                "path": m.group('path')
            })

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
            context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
            
            context.add_cookies(playwright_cookies)
            page = context.new_page()

            if service == "netflix":
                page.goto("https://www.netflix.com/tv8", timeout=60000)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(1500)
                page.keyboard.type(tv_code, delay=150)
                page.keyboard.press("Enter")
                page.wait_for_timeout(4000)
                
            elif service == "hbo":
                page.goto("https://auth.max.com/device/activate", timeout=60000)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(1500)
                page.keyboard.type(tv_code, delay=150)
                page.keyboard.press("Enter")
                page.wait_for_timeout(4000)
                
            elif service == "prime":
                page.goto("https://www.amazon.com/a/c/action/device/auth", timeout=60000)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(1500)
                page.keyboard.type(tv_code, delay=150)
                page.keyboard.press("Enter")
                page.wait_for_timeout(4000)
                
            elif service == "crunchyroll":
                page.goto("https://www.crunchyroll.com/activate", timeout=60000)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(2000) # Crunchyroll needs a bit more wait time
                page.keyboard.type(tv_code, delay=150)
                page.keyboard.press("Enter")
                page.wait_for_timeout(4000)
                
            elif service == "spotify":
                page.goto("https://www.spotify.com/us/pair/", timeout=60000)
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(2000) # Spotify might need a bit more time to render inputs
                page.keyboard.type(tv_code, delay=150)
                page.keyboard.press("Enter")
                page.wait_for_timeout(4000)
                
            else:
                browser.close()
                return {"ok": False, "msg": "Service မသိပါ"}

            browser.close()
            return {
                "ok": True, 
                "msg": f"✅ <b>{service.upper()}</b> TV အတွက် Code <code>{html.escape(tv_code)}</code> ချိတ်ဆက်မှု လုပ်ဆောင်ပြီးပါပြီ။\n\n(TV မျက်နှာပြင်ကို စောင့်ကြည့်ပေးပါ)"
            }
            
    except PlaywrightTimeoutError:
        return {"ok": False, "msg": "⚠️ Browser ချိတ်ဆက်မှု အချိန်ကြာလွန်းနေပါသည်။ (Timeout)"}
    except Exception as e:
        return {"ok": False, "msg": f"⚠️ Browser Automation Error: {html.escape(str(e))}"}

# ==========================================
# KEYBOARDS & MENUS
# ==========================================

def get_join_channel_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(
        types.InlineKeyboardButton("📢 Channel သို့ Join မည်", url=f"https://t.me/{FORCE_JOIN_CHANNEL.replace('@', '')}")
    )
    markup.add(
        types.InlineKeyboardButton("🔄 ကျွန်တော် Join ပြီးပါပြီ (စစ်ဆေးရန်)", callback_data="check_joined")
    )
    return markup

def get_main_menu(user_id=None):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    
    markup.add(types.KeyboardButton("Netflix login link ယူရန်"))
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
    markup.row(
        types.InlineKeyboardButton("🔙 Back (နောက်သို့)", callback_data="tv_back")
    )
    
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
    if not is_admin(message.from_user.id):
        return
        
    bot_is_open = True
    try:
        supabase.table('bot_settings').upsert({'key': 'bot_status', 'value': 'open'}).execute()
    except Exception as e:
        logger.error(f"Open bot error: {e}")
        
    bot.reply_to(message, "✅ <b>Bot ကို ဖွင့်လိုက်ပါပြီ။</b>", parse_mode="HTML")

@bot.message_handler(commands=['close'])
def close_bot(message):
    global bot_is_open
    if not is_admin(message.from_user.id):
        return
        
    bot_is_open = False
    try:
        supabase.table('bot_settings').upsert({'key': 'bot_status', 'value': 'closed'}).execute()
    except Exception as e:
        logger.error(f"Close bot error: {e}")
        
    bot.reply_to(message, "🛑 <b>Bot ကို ပိတ်လိုက်ပါပြီ (Maintenance)။</b>", parse_mode="HTML")

@bot.message_handler(commands=['start', 'menu'])
def send_welcome_and_menu(message):
    if is_banned(message.chat.id):
        return
        
    if not check_bot_open(message):
        return
        
    log_user(message)
    
    if not is_user_subscribed(message.from_user.id):
        safe_name = html.escape(message.from_user.first_name or "")
        bot.reply_to(
            message, 
            f"✦ <b>ချန်နယ် Join ရန် လိုအပ်ပါသည်</b>\n\nမင်္ဂလာပါ {safe_name}! 👋\nBot ကို စတင်အသုံးပြုရန် အောက်ပါချန်နယ်သို့ အရင်ဆုံး Join ပေးပါ။", 
            reply_markup=get_join_channel_keyboard(), 
            parse_mode="HTML"
        )
        return
        
    bot.reply_to(
        message, 
        "မင်္ဂလာပါ ဝေ့ - အောက်က ခလုတ်‌တွေကိုနှိပ်ပြီး အသုံးပြုနိုင်ပါပြီ။", 
        reply_markup=get_main_menu(message.from_user.id)
    )

@bot.message_handler(commands=['ntv', 'hbotv', 'pvtv', 'crtv', 'sptv'])
def handle_tv_commands(message):
    user_id = message.from_user.id
    
    if is_banned(user_id):
        return
        
    if not check_bot_open(message):
        return
    
    limit_val = get_daily_limit()
    user_limit = 999999 if (is_admin(user_id) or is_vip(user_id)) else limit_val
    used = get_quota(str(user_id), current_date())
    
    if used >= user_limit:
        bot.reply_to(message, f"🚫 ဒီနေ့အတွက် သတ်မှတ်ထားတဲ့ <b>{limit_val}</b> ခု ပြည့်သွားပြီကွ။", parse_mode="HTML")
        return

    code_split = message.text.split(maxsplit=1)
    if len(code_split) < 2:
        cmd_safe = html.escape(code_split[0])
        bot.reply_to(message, f"⚠️ TV Code ရိုက်ထည့်ရန် လိုအပ်ပါသည်။ ဥပမာ: <code>{cmd_safe} 123456</code>", parse_mode="HTML")
        return
        
    tv_code = code_split[1].strip().upper()
    cmd = code_split[0].lower()
    
    service_map = {
        '/ntv': 'netflix', 
        '/hbotv': 'hbo', 
        '/pvtv': 'prime', 
        '/crtv': 'crunchyroll', 
        '/sptv': 'spotify'
    }
    service = service_map.get(cmd)
    
    # 📌 ဤနေရာတွင် 'Browser ဖွင့်၍' စာသားကို ဖြုတ်လိုက်ပါပြီ။
    progress = bot.reply_to(
        message, 
        f"⏳ {service.upper()} သို့ ချိတ်ဆက်နေပါသည်... (စက္ကန့်အနည်းငယ် ကြာနိုင်ပါသည်)", 
        parse_mode="HTML"
    )
    
    def process_tv_login():
        acquired = file_lock.acquire(timeout=120)
        if not acquired:
            bot.edit_message_text(chat_id=message.chat.id, message_id=progress.message_id, text="⚠️ Server အလုပ်များနေပါသည်။ ခဏနေမှ ထပ်စမ်းပါ။")
            return
            
        try:
            res = supabase.table('cookies').select('id, content').eq('service_type', service).limit(1).execute()
            if not res.data:
                bot.edit_message_text(chat_id=message.chat.id, message_id=progress.message_id, text=f"❌ {service.upper()} အတွက် Cookie လက်ကျန်မရှိတော့ပါ။ Admin ကို အကြောင်းကြားပေးပါ။")
                return
                
            cookie_id = res.data[0]['id']
            cookie_content = res.data[0]['content']
            
            result = submit_tv_code_playwright(service, tv_code, cookie_content)
            
            if result["ok"]:
                supabase.table('cookies').delete().eq('id', cookie_id).execute()
                new_used = increment_quota(str(user_id), current_date())
                
                if is_admin(user_id) or is_vip(user_id):
                    quota_info = "👑 <b>VIP/Admin Account</b>"
                else:
                    quota_info = f"ယနေ့ Quota <b>{new_used}/{limit_val}</b> ခု သုံးထားသည်"
                    
                bot.edit_message_text(
                    chat_id=message.chat.id, 
                    message_id=progress.message_id, 
                    text=f"{result['msg']}\n\n{quota_info}", 
                    parse_mode="HTML"
                )
                send_log_to_channel(message.from_user, f"{service.upper()} TV ချိတ်ဆက်မှု အောင်မြင်ပါသည်။")
            else:
                bot.edit_message_text(
                    chat_id=message.chat.id, 
                    message_id=progress.message_id, 
                    text=result["msg"]
                )
                
        except Exception as e:
            bot.edit_message_text(chat_id=message.chat.id, message_id=progress.message_id, text=f"⚠️ Error: {html.escape(str(e))}")
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

    if not check_bot_open(message):
        return
        
    log_user(message)

    if not is_user_subscribed(user_id):
        bot.send_message(
            chat_id, 
            "✦ <b>ချန်နယ် Join ရန် လိုအပ်ပါသည်</b>", 
            reply_markup=get_join_channel_keyboard(), 
            parse_mode="HTML"
        )
        return

    if text == "ကျွန်ုပ်၏ Quota 📊":
        if is_admin(user_id) or is_vip(user_id):
            bot.send_message(chat_id, "👑 Admin/VIP (Unlimited)")
        else:
            limit_val = get_daily_limit()
            used = get_quota(str(user_id), current_date())
            remaining = max(0, limit_val - used)
            bot.send_message(
                chat_id, 
                f"ဒီနေ့ Quota: <b>{used}/{limit_val}</b> သုံးထားတယ် — <b>{remaining}</b> ကျန်ပါသေးတယ်", 
                parse_mode="HTML"
            )
            
    elif text == "လက်ကျန်စာရင်း 📋":
        available_cookies = get_available_cookies_count()
        bot.send_message(
            chat_id, 
            f"📋 <b>လက်ကျန်စာရင်း အခြေအနေ</b>\nစုစုပေါင်း Cookie: <b>{available_cookies}</b> ခု", 
            parse_mode="HTML"
        )

    elif text == "🌟 Get VIP 🌟":
        bot.send_message(
            chat_id, 
            "🌟 VIP အကောင့်ရယူရန် ဆက်သွယ်ပါ 👉 https://t.me/Ren2512", 
            disable_web_page_preview=True
        )
        
    elif text == TV_LOGIN_BTN:
        bot.send_message(
            chat_id, 
            "📺 <b>TV Login Services</b>\n\nအောက်ပါ Service များထဲမှ တစ်ခုကို ရွေးချယ်ပါ:", 
            reply_markup=get_tv_login_keyboard(), 
            parse_mode="HTML"
        )

    elif text == "Netflix login link ယူရန်":
        limit_val = get_daily_limit()
        user_limit = 999999 if (is_admin(user_id) or is_vip(user_id)) else limit_val
        used = get_quota(str(user_id), current_date())
        
        if used >= user_limit:
            bot.send_message(
                chat_id, 
                f"ဒီနေ့အတွက် သတ်မှတ်ထားတဲ့ <b>{limit_val}</b> ခု ပြည့်သွားပြီကွ။", 
                parse_mode="HTML"
            )
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
                    res = supabase.table('cookies').select('id, content').or_('service_type.eq.netflix,service_type.is.null').limit(1).execute()
                    if not res.data:
                        break
                        
                    cookie_id = res.data[0]['id']
                    content_bytes = res.data[0]['content'].encode('utf-8')
                    
                    supabase.table('cookies').delete().eq('id', cookie_id).execute()
                    
                    if not check_cookie_active(content_bytes):
                        continue
                        
                    url_result = execute_token_generation(content_bytes, str(user_id), chat_id)
                    if url_result:
                        clean_url = url_result
                        break

                if clean_url:
                    new_used = increment_quota(str(user_id), current_date())
                    
                    if is_admin(user_id) or is_vip(user_id):
                        quota_info = "👑 <b>VIP/Admin</b>"
                    else:
                        quota_info = f"ယနေ့ <b>{new_used}/{limit_val}</b> ခု သုံးထားတယ်ကွာ"
                        
                    bot.edit_message_text(
                        chat_id=chat_id, 
                        message_id=wait_msg.message_id, 
                        text=f"ရပြီဝေ့:\n\n{clean_url}\n\n⚠️ <b>သတိထား</b> - ဒီလင့်ခ်က 15 minutes လောက်ပဲရမှာနော်\n\n{quota_info}", 
                        disable_web_page_preview=True, 
                        parse_mode="HTML"
                    )
                    send_log_to_channel(message.from_user, "Netflix Token Link ထုတ်ယူသွားပါသည်။")
                else:
                    bot.edit_message_text(
                        chat_id=chat_id, 
                        message_id=wait_msg.message_id, 
                        text="လောလောဆယ် အဆင်ပြေသော Cookie များ ကုန်နေပါသည်ကွာ။"
                    )
            except Exception as e:
                bot.edit_message_text(chat_id=chat_id, message_id=wait_msg.message_id, text=f"Error: {html.escape(str(e))}")
            finally:
                file_lock.release()

        Thread(target=process_claim_task).start()

    elif text == "ZIP ဖိုင် တင်ရန် 📤":
        if not is_admin(user_id):
            return
            
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("Netflix", callback_data="zip_netflix"), 
            types.InlineKeyboardButton("HBO", callback_data="zip_hbo"),
            types.InlineKeyboardButton("Prime Video", callback_data="zip_prime"), 
            types.InlineKeyboardButton("Crunchyroll", callback_data="zip_crunchyroll"),
            types.InlineKeyboardButton("Spotify", callback_data="zip_spotify")
        )
        
        bot.send_message(
            chat_id, 
            "📦 <b>ဘယ် Service အတွက် Cookie တွေ တင်မှာလဲ?</b>\nအောက်ကနေ အရင်ရွေးချယ်ပေးပါ:", 
            reply_markup=markup, 
            parse_mode="HTML"
        )

    elif text == "Admin Panel ⚙️":
        if not is_admin(user_id):
            return
            
        bot.send_message(
            chat_id, 
            "⚙️ <b>Admin Management Panel</b>", 
            reply_markup=admin_panel_keyboard(), 
            parse_mode="HTML"
        )

# ==========================================
# ADMIN NEXT STEP HANDLERS
# ==========================================

def process_add_vip(message):
    if message.text in ["/start 🔄", BROADCAST_CANCEL_BTN] or message.text in MENU_BUTTONS: 
        return
    uid = message.text.strip()
    vip_users.add(uid)
    try: 
        supabase.table('vip_users').upsert({'user_id': uid}).execute()
    except Exception as e: 
        logger.error(f"Add VIP Error: {e}")
    bot.send_message(message.chat.id, f"🌟 User ID <code>{html.escape(uid)}</code> ကို VIP အဖြစ် သတ်မှတ်လိုက်ပါပြီ။", parse_mode="HTML")

def process_rm_vip(message):
    if message.text in ["/start 🔄", BROADCAST_CANCEL_BTN] or message.text in MENU_BUTTONS: 
        return
    uid = message.text.strip()
    vip_users.discard(uid)
    try: 
        supabase.table('vip_users').delete().eq('user_id', uid).execute()
    except Exception as e: 
        logger.error(f"Remove VIP Error: {e}")
    bot.send_message(message.chat.id, f"❌ User ID <code>{html.escape(uid)}</code> ကို VIP မှ ပယ်ဖျက်လိုက်ပါပြီ။", parse_mode="HTML")

def process_ban(message):
    if message.text in ["/start 🔄", BROADCAST_CANCEL_BTN] or message.text in MENU_BUTTONS: 
        return
    uid = message.text.strip()
    banned_users.add(uid)
    try: 
        supabase.table('banned_users').upsert({'user_id': uid}).execute()
    except Exception as e: 
        logger.error(f"Ban Error: {e}")
    bot.send_message(message.chat.id, f"🚫 User ID <code>{html.escape(uid)}</code> ကို Block လိုက်ပါပြီ။", parse_mode="HTML")

def process_unban(message):
    if message.text in ["/start 🔄", BROADCAST_CANCEL_BTN] or message.text in MENU_BUTTONS: 
        return
    uid = message.text.strip()
    banned_users.discard(uid)
    try: 
        supabase.table('banned_users').delete().eq('user_id', uid).execute()
    except Exception as e: 
        logger.error(f"Unban Error: {e}")
    bot.send_message(message.chat.id, f"✅ User ID <code>{html.escape(uid)}</code> ကို Unblock လုပ်ပေးလိုက်ပါပြီ။", parse_mode="HTML")

# ==========================================
# CALLBACK QUERIES
# ==========================================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call: types.CallbackQuery) -> None:
    if call.from_user is None or call.message is None:
        return
        
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    if is_banned(user_id):
        return

    # ZIP Upload Handling
    if call.data.startswith("zip_"):
        if not is_admin(user_id):
            return
            
        svc = call.data.split("_")[1]
        
        with _pending_lock:
            _pending_upload_admins[user_id] = svc
            
        bot.edit_message_text(
            chat_id=chat_id, 
            message_id=call.message.message_id, 
            text=f"📦 <b>{svc.upper()}</b> အတွက် `.zip` ဖိုင်ကို ပို့ပေးပါ။\n(မတင်လိုပါက /cancel ဟု ရိုက်ထည့်ပါ)", 
            parse_mode="HTML"
        )
        return

    # TV Login Menus
    if call.data.startswith("tv_"):
        bot.answer_callback_query(call.id)
        
        if call.data == "tv_back":
            bot.delete_message(chat_id, call.message.message_id)
            return
            
        msgs = {
            "tv_netflix": "╔══════════════════════════╗\n║   📺  N E T F L I X   T V\n╚══════════════════════════╝\n\n1. Open Netflix on your TV\n2. Grab the 8-digit code\n3. Send it here\n\nSend /ntv 12345678",
            "tv_hbo": "╔══════════════════════════╗\n║   🟣  H B O   T V   L O G I N\n╚══════════════════════════╝\n\n1. Open HBO Max on your TV\n2. Grab the 6-digit code\n3. Send it here\n\nSend /hbotv 123456",
            "tv_prime": "╔══════════════════════════╗\n║   📺  P R I M E   V I D E O   T V\n╚══════════════════════════╝\n\n1. Open Prime Video on your TV\n2. Grab the 6-character code\n3. Send it here\n\nSend /pvtv A7HSUW",
            "tv_crunchyroll": "╔══════════════════════════╗\n║   🍥  C R U N C H Y R O L L   T V\n╚══════════════════════════╝\n\n1. Open Crunchyroll on your TV\n2. Grab the 6-character code\n3. Send it here\n\nSend /crtv ABC123",
            "tv_spotify": "╔══════════════════════════╗\n║   🟢  S P O T I F Y   T V\n╚══════════════════════════╝\n\n1. Open Spotify on your TV\n2. Grab the pairing code\n3. Send it here\n\nSend /sptv ABCDEF"
        }
        
        bot.edit_message_text(
            chat_id=chat_id, 
            message_id=call.message.message_id, 
            text=msgs.get(call.data, "")
        )
        return

    # Channel Check
    if call.data == "check_joined":
        if is_user_subscribed(user_id):
            bot.answer_callback_query(call.id, "✅ အောင်မြင်ပါသည်။")
            bot.delete_message(chat_id, call.message.message_id)
            bot.send_message(
                chat_id, 
                "မင်္ဂလာပါ ဝေ့ - အောက်က ခလုတ်‌တွေကိုနှိပ်ပြီး အသုံးပြုနိုင်ပါပြီ", 
                reply_markup=get_main_menu(user_id)
            )
        else:
            bot.answer_callback_query(call.id, "⚠️ သင်သည် Channel သို့ အရင်ဆုံး Join ရန် လိုအပ်ပါသည်။", show_alert=True)
        return

    bot.answer_callback_query(call.id)
    
    if not is_admin(user_id):
        return
    
    # Admin Panels functions
    if call.data == "panel_add_vip":
        msg = bot.send_message(chat_id, "🌟 VIP ID ကို ရိုက်ထည့်ပါ:", parse_mode="HTML")
        bot.register_next_step_handler(msg, process_add_vip)
        
    elif call.data == "panel_rm_vip":
        msg = bot.send_message(chat_id, "❌ VIP ဖယ်ရှားမည့် ID ကို ရိုက်ထည့်ပါ:", parse_mode="HTML")
        bot.register_next_step_handler(msg, process_rm_vip)
        
    elif call.data == "panel_ban":
        msg = bot.send_message(chat_id, "🚫 Block မည့် ID ကို ရိုက်ထည့်ပါ:", parse_mode="HTML")
        bot.register_next_step_handler(msg, process_ban)
        
    elif call.data == "panel_unban":
        msg = bot.send_message(chat_id, "✅ Unblock မည့် ID ကို ရိုက်ထည့်ပါ:", parse_mode="HTML")
        bot.register_next_step_handler(msg, process_unban)
        
    elif call.data == "panel_list_vip":
        vip_list_str = "\n".join(vip_users) if vip_users else "မရှိပါ"
        bot.send_message(chat_id, f"🌟 VIP User များ:\n\n{html.escape(vip_list_str)}")
        
    elif call.data == "panel_list_banned":
        banned_list_str = "\n".join(banned_users) if banned_users else "မရှိပါ"
        bot.send_message(chat_id, f"🚫 Block ထားသော User များ:\n\n{html.escape(banned_list_str)}")
        
    elif call.data == "panel_clear":
        try:
            supabase.table('cookies').delete().gt('id', -1).execute()
            bot.send_message(chat_id, "🗑 Cookie အဟောင်းများ အားလုံး ရှင်းလင်းပြီးပါပြီ။")
        except Exception as e:
            bot.send_message(chat_id, f"Error clearing cookies: {html.escape(str(e))}")
            
    elif call.data == "panel_broadcast":
        awaiting_broadcast[str(chat_id)] = True
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add(BROADCAST_CANCEL_BTN)
        bot.send_message(chat_id, "📢 Broadcast စာသားကို ရိုက်ပို့ပါ။", reply_markup=markup)

# ==========================================
# FILE UPLOAD HANDLERS
# ==========================================

@bot.message_handler(content_types=["document"])
def process_document_merged(message: types.Message):
    if message.from_user is None or message.document is None:
        return
        
    user_id = message.chat.id
    
    if is_banned(user_id) or not check_bot_open(message):
        return
        
    if not is_user_subscribed(message.from_user.id):
        bot.send_message(
            user_id, 
            "✦ <b>ချန်နယ် Join ရန် လိုအပ်ပါသည်</b>", 
            reply_markup=get_join_channel_keyboard(), 
            parse_mode="HTML"
        )
        return

    with _pending_lock:
        svc_type = _pending_upload_admins.get(user_id)

    if svc_type and is_admin(user_id):
        document = message.document
        
        if not document.file_name.lower().endswith(".zip"):
            bot.reply_to(message, "❌ .zip ဖိုင်အမျိုးအစားသာ လက်ခံပါသည်။")
            return
            
        progress = bot.reply_to(message, f"📦 {svc_type.upper()} ZIP ဖိုင်ကို Supabase ထဲသို့ သွင်းနေပါသည်...")
        
        try:
            file_info = bot.get_file(document.file_id)
            raw_data = bot.download_file(file_info.file_path)
            
            cookies_to_insert = []
            with zip_lib.ZipFile(io.BytesIO(raw_data)) as z:
                for file_info_z in z.infolist():
                    if file_info_z.filename.lower().endswith('.txt') and not file_info_z.is_dir():
                        content = z.read(file_info_z.filename).decode('utf-8', errors='ignore')
                        cookies_to_insert.append({
                            'content': content, 
                            'service_type': svc_type
                        })
                        
            for i in range(0, len(cookies_to_insert), 500):
                supabase.table('cookies').insert(cookies_to_insert[i:i + 500]).execute()
                
            with _pending_lock:
                _pending_upload_admins.pop(user_id, None)
                
            bot.edit_message_text(
                chat_id=user_id, 
                message_id=progress.message_id, 
                text=f"✅ <b>ZIP ဖိုင် ဖြေပြီးပါပြီ။ ({svc_type.upper()})</b>\n\n▪️ ထည့်သွင်းလိုက်သော အရေအတွက်: <b>{len(cookies_to_insert)}</b> ခု", 
                parse_mode="HTML"
            )
        except Exception as e:
            bot.edit_message_text(chat_id=user_id, message_id=progress.message_id, text=f"❌ Error: {html.escape(str(e))}")
        return

    if not message.document.file_name.lower().endswith('.txt'):
        bot.reply_to(
            message, 
            "⚠️ ကျေးဇူးပြု၍ .txt ဖိုင် သို့မဟုတ် ZIP တင်ခလုတ်ကို အသုံးပြုပါ။", 
            reply_markup=get_main_menu(message.from_user.id)
        )
        return

    progress_msg = bot.reply_to(message, "ဖိုင်ငါရပြီ - အစဉ်လိုက်ပဲသွားမယ်ကွ(Queue)...")
    
    def document_task():
        try:
            file_info = bot.get_file(message.document.file_id)
            file_data = bot.download_file(file_info.file_path)
            run_generator_task(message.chat.id, message.from_user, file_data, progress_msg.message_id)
        except Exception as e:
            bot.edit_message_text(chat_id=message.chat.id, message_id=progress_msg.message_id, text=f"Error: {html.escape(str(e))}")
            
    Thread(target=document_task).start()

@bot.message_handler(content_types=['text'])
def handle_text_merged(message: types.Message):
    if message.from_user is None or message.text.startswith('/'):
        if message.text.lower() == '/cancel' and is_admin(message.chat.id):
            with _pending_lock:
                if message.chat.id in _pending_upload_admins:
                    _pending_upload_admins.pop(message.chat.id)
                    bot.reply_to(
                        message, 
                        "❌ ZIP တင်ခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။", 
                        reply_markup=get_main_menu(message.chat.id)
                    )
            return
        return

    if message.text in [BROADCAST_CANCEL_BTN] or message.text in MENU_BUTTONS:
        return
        
    chat_id = message.chat.id
    user_id = str(message.chat.id)
    
    if is_banned(user_id) or not check_bot_open(message):
        return
    
    if chat_id == ADMIN_ID and awaiting_broadcast.get(user_id):
        awaiting_broadcast[user_id] = False
        sent = 0
        for uid in active_users.keys():
            if uid not in banned_users:
                try:
                    bot.send_message(int(uid), message.text)
                    sent += 1
                except Exception:
                    pass
        bot.send_message(
            chat_id, 
            f"📢 Broadcast ပို့ပြီးပါပြီ။ အောင်မြင်: {sent}", 
            reply_markup=get_main_menu(chat_id)
        )
        return

    progress_msg = bot.reply_to(message, "စာသားရပြီ အစဉ်လိုက်ပဲသွားမယ်ကွ(Queue)...")
    
    def text_task():
        run_generator_task(message.chat.id, message.from_user, message.text.encode('utf-8'), progress_msg.message_id)
        
    Thread(target=text_task).start()

# ==========================================
# WEB SERVER & MAIN EXECUTION
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
    logger.info("Bot စတင် အလုပ်လုပ်နေပါပြီ...")
    bot.infinity_polling(skip_pending=False, timeout=30, long_polling_timeout=30)
