"""Combined Telegram bot with Users, VIPs, Bans, Cookies, Settings, TV Login (Playwright), Peacock TV Quota & Referrals."""

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

DEFAULT_DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "3"))
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
stop_flags = {}
awaiting_broadcast = {}
_pending_upload_admins: dict[int, str] = {}
_pending_lock = threading.Lock()

TV_LOGIN_BTN = "📺 TV Login"

MENU_BUTTONS = [
    "Netflix login link ယူရန်", 
    "ကျွန်ုပ်၏ Quota 📊", 
    "လက်ကျန်စာရင်း 📋", 
    "🎁 Get Quota (Peacock)", 
    "🦚 Peacock TV (Email:Pass)",
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
# RENDER KEEP-AWAKE (ANTI-SLEEP THREAD)
# ==========================================

def keep_awake():
    """Render free tier မအိပ်စေရန် ၁၄ မိနစ်တစ်ခါ Self-ping လုပ်ပေးမည့် စနစ်"""
    url = os.environ.get("RENDER_EXTERNAL_URL", f"http://127.0.0.1:{os.environ.get('PORT', 10000)}")
    while True:
        time.sleep(14 * 60) # 14 Minutes
        try:
            requests.get(url, timeout=10)
            logger.info("Pinged self to stay awake.")
        except Exception:
            pass

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

def get_all_cookies_stats() -> dict:
    stats = {"netflix": 0, "hbo": 0, "prime": 0, "crunchyroll": 0, "peacock30": 0, "peacock365": 0}
    try:
        r_nf = supabase.table('cookies').select('id', count='exact').or_('service_type.eq.netflix,service_type.is.null').execute()
        stats["netflix"] = r_nf.count if r_nf.count else 0
        
        r_hbo = supabase.table('cookies').select('id', count='exact').eq('service_type', 'hbo').execute()
        stats["hbo"] = r_hbo.count if r_hbo.count else 0
        
        r_prime = supabase.table('cookies').select('id', count='exact').eq('service_type', 'prime').execute()
        stats["prime"] = r_prime.count if r_prime.count else 0
        
        r_cr = supabase.table('cookies').select('id', count='exact').eq('service_type', 'crunchyroll').execute()
        stats["crunchyroll"] = r_cr.count if r_cr.count else 0

        r_p30 = supabase.table('peacock_accounts').select('id', count='exact').eq('plan_type', '30days').execute()
        stats["peacock30"] = r_p30.count if r_p30.count else 0
        
        r_p365 = supabase.table('peacock_accounts').select('id', count='exact').eq('plan_type', '1year').execute()
        stats["peacock365"] = r_p365.count if r_p365.count else 0
        
    except Exception as e:
        logger.error(f"Stats count error: {e}")
    return stats

# --- 📌 PEACOCK CREDITS HELPER FUNCTIONS ---
def get_user_peacock_data(user_id: str) -> dict:
    try:
        res = supabase.table('users').select('peacock_credit, last_daily_claim').eq('user_id', user_id).execute()
        if res.data:
            return res.data[0]
    except Exception as e:
        logger.error(f"Error fetching user data: {e}")
    return {"peacock_credit": 0, "last_daily_claim": None}

def add_peacock_credit(user_id: str, amount: int):
    data = get_user_peacock_data(user_id)
    current_credit = data.get('peacock_credit') or 0
    try:
        supabase.table('users').update({'peacock_credit': current_credit + amount}).eq('user_id', user_id).execute()
    except Exception as e:
        logger.error(f"Error adding peacock credit: {e}")
# ---------------------------------------------

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


def execute_token_generation(content_bytes: bytes, user_id: str, chat_id: int):
    input_path = "input.txt"
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
            stdout, stderr = proc.communicate(timeout=60)
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
            bot.edit_message_text(chat_id=chat_id, message_id=progress_msg_id, text="Token ပြောင်းနေပါပြီ ခဏစောင့်ပါ...")
            
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
    playwright_cookies = []
    
    for line in cookie_text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
            
        m = COOKIE_LINE_RE.match(line)
        if m:
            domain = m.group('domain')
            
            if service == "netflix" and not ("netflix" in domain):
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
                page.wait_for_timeout(2000)
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
    markup.add(types.KeyboardButton("ကျွန်ုပ်၏ Quota 📊"), types.KeyboardButton("လက်ကျန်စာရင်း 📋"))
    markup.add(types.KeyboardButton("🎁 Get Quota (Peacock)"), types.KeyboardButton("🦚 Peacock TV (Email:Pass)"))
    
    if user_id and is_admin(user_id):
        markup.add(types.KeyboardButton("ZIP ဖိုင် တင်ရန် 📤"), types.KeyboardButton("Admin Panel ⚙️"))
    elif user_id and not is_admin(user_id):
        markup.add(types.KeyboardButton("🌟 Get VIP 🌟"))
        
    markup.add(types.KeyboardButton(TV_LOGIN_BTN), types.KeyboardButton("/start 🔄"))
    return markup

def get_tv_login_keyboard():
    markup = types.InlineKeyboardMarkup()
    
    markup.row(
        types.InlineKeyboardButton("📺 Netflix TV", callback_data="tv_netflix"), 
        types.InlineKeyboardButton("🟣 HBO TV", callback_data="tv_hbo")
    )
    markup.row(
        types.InlineKeyboardButton("📺 Prime Video TV", callback_data="tv_prime"), 
        types.InlineKeyboardButton("🍥 Crunchyroll TV", callback_data="tv_crunchyroll")
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

def get_clear_cookie_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("Netflix", callback_data="clear_netflix"),
        types.InlineKeyboardButton("HBO", callback_data="clear_hbo"),
        types.InlineKeyboardButton("Prime", callback_data="clear_prime"),
        types.InlineKeyboardButton("Crunchyroll", callback_data="clear_crunchyroll"),
        types.InlineKeyboardButton("💥 All Cookies", callback_data="clear_all"),
        types.InlineKeyboardButton("❌ Cancel", callback_data="clear_cancel")
    )
    return markup

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
    user_id = str(message.from_user.id)
    
    # --- 📌 Anti-Cheat Referral System ---
    if message.text.startswith('/start '):
        inviter_id = message.text.split(' ')[1]
        if inviter_id.isdigit() and inviter_id != user_id:
            # Check if new user (no referred_by yet)
            res = supabase.table('users').select('referred_by').eq('user_id', user_id).execute()
            if res.data and not res.data[0].get('referred_by'):
                # Force Channel Join Check & Anti-bot Check
                if is_user_subscribed(message.from_user.id) and not message.from_user.is_bot:
                    try:
                        supabase.table('users').update({'referred_by': inviter_id}).eq('user_id', user_id).execute()
                        add_peacock_credit(inviter_id, 1)
                        bot.send_message(int(inviter_id), "🎉 ဂုဏ်ယူပါသည်! လူသစ်တစ်ယောက် ဖိတ်ခေါ်နိုင်ခဲ့သဖြင့် Peacock <b>1 Credit</b> ရရှိပါပြီ။", parse_mode="HTML")
                    except Exception as e:
                        logger.error(f"Referral logic error: {e}")
    # --------------------------------------

    if not is_user_subscribed(message.from_user.id):
        safe_name = html.escape(message.from_user.first_name or "")
        bot.reply_to(
            message, 
            f"✦ <b>ချန်နယ် Join ရန် လိုအပ်ပါသည်</b>\n\nမင်္ဂလာပါ {safe_name}! 👋\nBot ကို စတင်အသုံးပြုရန် အောက်ပါချန်နယ်သို့ အရင်ဆုံး Join ပေးပါ။", 
            reply_markup=get_join_channel_keyboard(), 
            parse_mode="HTML"
        )
        return
        
    welcome_text = (
        "👋 မင်္ဂလာပါ ဝေ့... \n\n"
        "အောက်ပါ ဝန်ဆောင်မှုတွေကို အလွယ်တကူ ရယူနိုင်ပါပြီ-\n\n"
        "📺 TV Login Services (Auto Connect)\n"
        "▪️ Netflix, HBO Max, Prime Video, Crunchyroll စတဲ့ TV Code တွေကို ပို့ပေးရုံနဲ့ အလိုအလျောက် ချိတ်ဆက်ပေးပါမယ်။\n\n"
        "🔗 Netflix Cookie to URL Link\n"
        "▪️ အသင့်သုံး Netflix Cookie ကနေ 15 မိနစ်ခံ Token Link ကို ထုတ်ယူနိုင်ပါတယ်။\n\n"
        "🦚 <b>NEW: Peacock TV Premium (Email & Pass)</b>\n"
        "▪️ Daily Credits နှင့် Referrals များရှာဖွေပြီး အခမဲ့ အကောင့်များ ထုတ်ယူနိုင်ပါသည်။\n\n"
        "အောက်ပါ Menu ခလုတ်များကို နှိပ်၍ စတင် အသုံးပြုနိုင်ပါပြီ 👇"
    )
        
    bot.reply_to(
        message, 
        welcome_text, 
        reply_markup=get_main_menu(message.from_user.id)
    )

@bot.message_handler(func=lambda message: message.text == "/start 🔄")
def refresh_bot(message):
    send_welcome_and_menu(message)

@bot.message_handler(commands=['ntv', 'hbotv', 'pvtv', 'crtv'])
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
        '/crtv': 'crunchyroll'
    }
    service = service_map.get(cmd)
    
    if not service:
        return
    
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
                f"ဒီနေ့ Quota (TV & Cookies): <b>{used}/{limit_val}</b> သုံးထားတယ် — <b>{remaining}</b> ကျန်ပါသေးတယ်", 
                parse_mode="HTML"
            )
            
    elif text == "လက်ကျန်စာရင်း 📋":
        stats = get_all_cookies_stats()
        msg_text = (
            "📋 <b>လက်ကျန်စာရင်း အခြေအနေ</b>\n\n"
            f"📺 Netflix: <b>{stats['netflix']}</b> ခု\n"
            f"🟣 HBO Max: <b>{stats['hbo']}</b> ခု\n"
            f"📺 Prime Video: <b>{stats['prime']}</b> ခု\n"
            f"🍥 Crunchyroll: <b>{stats['crunchyroll']}</b> ခု\n\n"
            f"🦚 Peacock (30 Days): <b>{stats['peacock30']}</b> ခု\n"
            f"🦚 Peacock (1 Year): <b>{stats['peacock365']}</b> ခု"
        )
        bot.send_message(chat_id, msg_text, parse_mode="HTML")

    elif text == "🎁 Get Quota (Peacock)":
        user_data = get_user_peacock_data(str(user_id))
        credits = user_data.get('peacock_credit') or 0
        last_claim = user_data.get('last_daily_claim')
        today = current_date()
        
        bonus_text = "❌ ယူပြီးပါပြီ"
        if last_claim != today:
            try:
                supabase.table('users').update({'peacock_credit': credits + 10, 'last_daily_claim': today}).eq('user_id', str(user_id)).execute()
                credits += 10
                bonus_text = "✅ (ယနေ့အတွက် 10 မှတ် ရရှိပါပြီ)"
            except Exception as e:
                logger.error(f"Daily claim error: {e}")

        bot_info = bot.get_me()
        msg = (
            f"🎁 <b>သင့်၏ Peacock Quota (Credit)</b>\n\n"
            f"🌟 လက်ရှိ Credit: <b>{credits}</b> မှတ်\n"
            f"📅 ယနေ့ Daily Bonus: {bonus_text}\n\n"
            f"🔗 <b>Credit ထပ်မံရယူရန်:</b>\n"
            f"သင့်သူငယ်ချင်းများကို ဖိတ်ခေါ်ပြီး Credit များ စုဆောင်းပါ။ (၁ ယောက်လျှင် ၁ မှတ်)\n\n"
            f"👇 သင့်ဖိတ်ခေါ်လင့်ခ်:\n<code>https://t.me/{bot_info.username}?start={user_id}</code>"
        )
        bot.send_message(chat_id, msg, parse_mode="HTML")

    elif text == "🦚 Peacock TV (Email:Pass)":
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("30 Days (11 Credits)", callback_data="buy_peacock_30"),
            types.InlineKeyboardButton("1 Year (20 Credits)", callback_data="buy_peacock_365")
        )
        bot.send_message(chat_id, "🦚 <b>Peacock TV Premium</b>\n\nလိုချင်သည့် Plan ကို အောက်မှ ရွေးချယ်ပါ-", reply_markup=markup, parse_mode="HTML")

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
                
            wait_msg = bot.send_message(chat_id, "⏳ Token ထုတ်နေပါပြီ ခဏစောင့်ကွာ...")
            
            try:
                clean_url = None
                attempts = 0
                max_attempts = 5
                
                while attempts < max_attempts:
                    res = supabase.table('cookies').select('id, content').or_('service_type.eq.netflix,service_type.is.null').limit(1).execute()
                    if not res.data:
                        break
                        
                    cookie_id = res.data[0]['id']
                    content_bytes = res.data[0]['content'].encode('utf-8')
                    
                    supabase.table('cookies').delete().eq('id', cookie_id).execute()
                    attempts += 1
                    
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
                elif attempts >= max_attempts:
                    bot.edit_message_text(
                        chat_id=chat_id, 
                        message_id=wait_msg.message_id, 
                        text="⚠️ <b>Cookie သက်တမ်းကုန်နေပါသည်။</b>\n\nစစ်ဆေးခဲ့သော Cookie များမှာ အလုပ်မလုပ်ပါ။ 'Netflix login link ယူရန်' ကို ထပ်နှိပ်ပြီး နောက်ထပ် Cookie များကို ဆက်စမ်းကြည့်ပေးပါ။",
                        parse_mode="HTML"
                    )
                else:
                    bot.edit_message_text(
                        chat_id=chat_id, 
                        message_id=wait_msg.message_id, 
                        text="လောလောဆယ် အဆင်ပြေသော Netflix Cookie များ ကုန်နေပါသည်ကွာ။"
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
            types.InlineKeyboardButton("Peacock 30 Days", callback_data="zip_peacock30"),
            types.InlineKeyboardButton("Peacock 1 Year", callback_data="zip_peacock365")
        )
        
        bot.send_message(
            chat_id, 
            "📦 <b>ဘယ် Service အတွက် ဖိုင်တွေ တင်မှာလဲ?</b>\nအောက်ကနေ အရင်ရွေးချယ်ပေးပါ:", 
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
    if message.text in ["/start 🔄", "/cancel"] or message.text in MENU_BUTTONS: 
        return
    uid = message.text.strip()
    vip_users.add(uid)
    try: 
        supabase.table('vip_users').upsert({'user_id': uid}).execute()
    except Exception as e: 
        logger.error(f"Add VIP Error: {e}")
    bot.send_message(message.chat.id, f"🌟 User ID <code>{html.escape(uid)}</code> ကို VIP အဖြစ် သတ်မှတ်လိုက်ပါပြီ။", parse_mode="HTML")

def process_rm_vip(message):
    if message.text in ["/start 🔄", "/cancel"] or message.text in MENU_BUTTONS: 
        return
    uid = message.text.strip()
    vip_users.discard(uid)
    try: 
        supabase.table('vip_users').delete().eq('user_id', uid).execute()
    except Exception as e: 
        logger.error(f"Remove VIP Error: {e}")
    bot.send_message(message.chat.id, f"❌ User ID <code>{html.escape(uid)}</code> ကို VIP မှ ပယ်ဖျက်လိုက်ပါပြီ။", parse_mode="HTML")

def process_ban(message):
    if message.text in ["/start 🔄", "/cancel"] or message.text in MENU_BUTTONS: 
        return
    uid = message.text.strip()
    banned_users.add(uid)
    try: 
        supabase.table('banned_users').upsert({'user_id': uid}).execute()
    except Exception as e: 
        logger.error(f"Ban Error: {e}")
    bot.send_message(message.chat.id, f"🚫 User ID <code>{html.escape(uid)}</code> ကို Block လိုက်ပါပြီ။", parse_mode="HTML")

def process_unban(message):
    if message.text in ["/start 🔄", "/cancel"] or message.text in MENU_BUTTONS: 
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

    # 📌 Peacock TV Account ဝယ်ယူခြင်း
    if call.data.startswith("buy_peacock_"):
        plan = "30days" if call.data == "buy_peacock_30" else "1year"
        cost = 11 if plan == "30days" else 20
        
        user_data = get_user_peacock_data(str(user_id))
        credits = user_data.get('peacock_credit') or 0
        
        if credits < cost:
            bot.answer_callback_query(call.id, f"⚠️ Credit မလုံလောက်ပါ။ {cost} မှတ် လိုအပ်ပါသည်။ (လက်ရှိ: {credits})", show_alert=True)
            return
            
        try:
            res = supabase.table('peacock_accounts').select('id, email_pass').eq('plan_type', plan).limit(1).execute()
            if not res.data:
                bot.answer_callback_query(call.id, "⚠️ လောလောဆယ် အကောင့် ကုန်နေပါသည်။ Admin ကို ပြောပါ။", show_alert=True)
                return
                
            acc_id = res.data[0]['id']
            email_pass = res.data[0]['email_pass']
            
            # နုတ်မည် & အကောင့်ထုတ်ပေးမည်
            supabase.table('peacock_accounts').delete().eq('id', acc_id).execute()
            supabase.table('users').update({'peacock_credit': credits - cost}).eq('user_id', str(user_id)).execute()
            
            bot.edit_message_text(
                chat_id=chat_id, 
                message_id=call.message.message_id, 
                text=f"🦚 <b>Peacock TV ({plan})</b>\n\n✅ ဝယ်ယူမှု အောင်မြင်ပါသည်။\n\n📧 Email:Pass 👇\n<code>{email_pass}</code>\n\n💳 လက်ကျန် Credit: <b>{credits - cost}</b>", 
                parse_mode="HTML"
            )
            send_log_to_channel(call.from_user, f"Peacock {plan} အကောင့် ထုတ်ယူသွားပါသည်။")
        except Exception as e:
            bot.answer_callback_query(call.id, "⚠️ Error occurred.", show_alert=True)
            logger.error(f"Peacock buy error: {e}")
        return

    # ZIP / TXT Upload Handling Menu
    if call.data.startswith("zip_"):
        if not is_admin(user_id):
            return
            
        svc = call.data.split("_")[1] # e.g. netflix, hbo, peacock30, peacock365
        
        with _pending_lock:
            _pending_upload_admins[user_id] = svc
            
        if svc.startswith("peacock"):
            upload_msg = f"📦 <b>{svc.upper()}</b> အတွက် `.zip`, `.txt` ဖိုင် (သို့မဟုတ်) <b>အကောင့်စာသားများကို</b> တိုက်ရိုက် ပို့ပေးပါ။\n(မတင်လိုပါက /cancel ဟု ရိုက်ထည့်ပါ)"
        else:
            upload_msg = f"📦 <b>{svc.upper()}</b> အတွက် `.zip` ဖိုင် ကို ပို့ပေးပါ။\n(မတင်လိုပါက /cancel ဟု ရိုက်ထည့်ပါ)"
            
        bot.edit_message_text(
            chat_id=chat_id, 
            message_id=call.message.message_id, 
            text=upload_msg, 
            parse_mode="HTML"
        )
        return

    # Clear Cookie Menu Handling
    if call.data.startswith("clear_"):
        if not is_admin(user_id):
            return
        svc = call.data.split("_")[1]
        try:
            if svc == "cancel":
                bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text="❌ ရှင်းလင်းခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။")
                return
            elif svc == "all":
                supabase.table('cookies').delete().gt('id', -1).execute()
                bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text="🗑 <b>Cookie များ အားလုံး ရှင်းလင်းပြီးပါပြီ။</b>", parse_mode="HTML")
            elif svc == "netflix":
                supabase.table('cookies').delete().or_('service_type.eq.netflix,service_type.is.null').execute()
                bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text="🗑 <b>Netflix Cookie များ ရှင်းလင်းပြီးပါပြီ။</b>", parse_mode="HTML")
            else:
                supabase.table('cookies').delete().eq('service_type', svc).execute()
                bot.edit_message_text(chat_id=chat_id, message_id=call.message.message_id, text=f"🗑 <b>{svc.upper()} Cookie များ ရှင်းလင်းပြီးပါပြီ။</b>", parse_mode="HTML")
        except Exception as e:
            bot.send_message(chat_id, f"Error clearing cookies: {html.escape(str(e))}")
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
            "tv_crunchyroll": "╔══════════════════════════╗\n║   🍥  C R U N C H Y R O L L   T V\n╚══════════════════════════╝\n\n1. Open Crunchyroll on your TV\n2. Grab the 6-character code\n3. Send it here\n\nSend /crtv ABC123"
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
        
    elif call.data == "panel_list_users":
        if not active_users:
            bot.send_message(chat_id, "လက်ရှိတွင် အသုံးပြုသူ စာရင်း မရှိသေးပါ။")
        else:
            chunks = []
            text = f"👥 <b>စုစုပေါင်း အသုံးပြုသူ: {len(active_users)} ဦး</b>\n\n"
            for uid, uname in active_users.items():
                status = ""
                if uid in banned_users: status = " (🚫 Blocked)"
                elif uid in vip_users: status = " (🌟 VIP)"
                
                line = f"▪️ {html.escape(uname)} (ID: <code>{uid}</code>){status}\n"
                
                if len(text) + len(line) > 3900:
                    chunks.append(text)
                    text = line
                else:
                    text += line
                    
            if text:
                chunks.append(text)
                
            for chunk in chunks:
                bot.send_message(chat_id, chunk, parse_mode="HTML")
        
    elif call.data == "panel_clear":
        bot.edit_message_text(
            chat_id=chat_id, 
            message_id=call.message.message_id, 
            text="🗑 <b>မည်သည့် Service ၏ Cookie များကို ရှင်းလင်းမည်နည်း?</b>", 
            reply_markup=get_clear_cookie_keyboard(), 
            parse_mode="HTML"
        )
            
    elif call.data == "panel_broadcast":
        awaiting_broadcast[user_id] = True
        bot.send_message(chat_id, "📢 Broadcast စာသားကို ရိုက်ပို့ပါ။ (ပယ်ဖျက်လိုပါက /cancel ဟု ရိုက်ပါ)")

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
        
        # --- Peacock (Email:Pass) .txt သို့မဟုတ် .zip ဖိုင် အလွယ်တင်ရန် ---
        if svc_type.startswith("peacock"):
            plan = "30days" if svc_type == "peacock30" else "1year"
            progress = bot.reply_to(message, f"📦 Peacock ({plan}) ဖိုင်ကို သွင်းနေပါသည်...")
            try:
                file_info = bot.get_file(document.file_id)
                raw_data = bot.download_file(file_info.file_path)
                
                accounts_to_insert = []
                # ZIP ဖိုင်ဖြစ်လျှင် 
                if document.file_name.lower().endswith(".zip"):
                    with zip_lib.ZipFile(io.BytesIO(raw_data)) as z:
                        for file_info_z in z.infolist():
                            if file_info_z.filename.lower().endswith('.txt') and not file_info_z.is_dir():
                                content = z.read(file_info_z.filename).decode('utf-8', errors='ignore')
                                for acc in content.strip().split('\n'):
                                    if acc.strip():
                                        accounts_to_insert.append({'email_pass': acc.strip(), 'plan_type': plan})
                # TXT ဖိုင်ဖြစ်လျှင် (ပိုလွယ်ကူရန်)
                elif document.file_name.lower().endswith(".txt"):
                    content = raw_data.decode('utf-8', errors='ignore')
                    for acc in content.strip().split('\n'):
                        if acc.strip():
                            accounts_to_insert.append({'email_pass': acc.strip(), 'plan_type': plan})
                else:
                    bot.edit_message_text(chat_id=user_id, message_id=progress.message_id, text="❌ .zip သို့မဟုတ် .txt ဖိုင်အမျိုးအစားသာ လက်ခံပါသည်။")
                    return
                
                if not accounts_to_insert:
                    bot.edit_message_text(chat_id=user_id, message_id=progress.message_id, text="❌ ဖိုင်ထဲတွင် အကောင့်များ မတွေ့ပါ။")
                    return
                    
                for i in range(0, len(accounts_to_insert), 500):
                    supabase.table('peacock_accounts').insert(accounts_to_insert[i:i + 500]).execute()
                    
                with _pending_lock:
                    _pending_upload_admins.pop(user_id, None)
                    
                bot.edit_message_text(
                    chat_id=user_id, 
                    message_id=progress.message_id, 
                    text=f"✅ <b>ဖိုင် ဖြေပြီးပါပြီ။ (Peacock {plan})</b>\n\n▪️ ထည့်သွင်းလိုက်သော အရေအတွက်: <b>{len(accounts_to_insert)}</b> ခု", 
                    parse_mode="HTML"
                )
            except Exception as e:
                bot.edit_message_text(chat_id=user_id, message_id=progress.message_id, text=f"❌ Error: {html.escape(str(e))}")
            return
        # ---------------------------------------------------------------
        
        if not document.file_name.lower().endswith(".zip"):
            bot.reply_to(message, "❌ Cookies အတွက် .zip ဖိုင်အမျိုးအစားသာ လက်ခံပါသည်။")
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

    progress_msg = bot.reply_to(message, "ဖိုင်ငါရပြီ - Token ပြောင်းနေပါပြီ ခဏစောင့်ပါ...")
    
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
                        "❌ တင်ခြင်းကို ပယ်ဖျက်လိုက်ပါပြီ။", 
                        reply_markup=get_main_menu(message.chat.id)
                    )
                elif message.chat.id in awaiting_broadcast:
                    awaiting_broadcast[message.chat.id] = False
                    bot.reply_to(message, "❌ Broadcast ပယ်ဖျက်လိုက်ပါပြီ။")
            return
        return

    if message.text in MENU_BUTTONS:
        return
        
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if is_banned(user_id) or not check_bot_open(message):
        return

    # 📌 1. Admin မှ Peacock အကောင့်များကို Text အနေဖြင့် တိုက်ရိုက်တင်ခြင်း
    with _pending_lock:
        svc_type = _pending_upload_admins.get(user_id)
        
    if svc_type and is_admin(user_id) and svc_type.startswith("peacock"):
        plan = "30days" if svc_type == "peacock30" else "1year"
        progress = bot.reply_to(message, f"📦 Peacock ({plan}) အကောင့်များကို သွင်းနေပါသည်...")
        try:
            content = message.text
            accounts_to_insert = []
            for acc in content.strip().split('\n'):
                if acc.strip():
                    accounts_to_insert.append({'email_pass': acc.strip(), 'plan_type': plan})
            
            if not accounts_to_insert:
                bot.edit_message_text(chat_id=user_id, message_id=progress.message_id, text="❌ စာသားထဲတွင် အကောင့်များ မတွေ့ပါ။")
                return
                
            for i in range(0, len(accounts_to_insert), 500):
                supabase.table('peacock_accounts').insert(accounts_to_insert[i:i + 500]).execute()
                
            with _pending_lock:
                _pending_upload_admins.pop(user_id, None)
                
            bot.edit_message_text(
                chat_id=user_id, 
                message_id=progress.message_id, 
                text=f"✅ <b>စာသားမှတစ်ဆင့် အကောင့်များ သွင်းပြီးပါပြီ။ (Peacock {plan})</b>\n\n▪️ ထည့်သွင်းလိုက်သော အရေအတွက်: <b>{len(accounts_to_insert)}</b> ခု", 
                parse_mode="HTML"
            )
        except Exception as e:
            bot.edit_message_text(chat_id=user_id, message_id=progress.message_id, text=f"❌ Error: {html.escape(str(e))}")
        return
    # ---------------------------------------------------------------

    # 📌 2. Broadcast State ကို ဤနေရာတွင် တိုက်ရိုက်ဖမ်းယူပါသည်
    if chat_id == ADMIN_ID and awaiting_broadcast.get(chat_id):
        awaiting_broadcast[chat_id] = False
        sent, failed = 0, 0
        for uid in active_users.keys():
            if uid not in banned_users:
                try:
                    bot.send_message(int(uid), message.text)
                    sent += 1
                except Exception:
                    failed += 1
        bot.send_message(
            chat_id, 
            f"📢 Broadcast ပို့ပြီးပါပြီ။\n✅ အောင်မြင်: {sent} ဦး\n❌ မအောင်မြင်: {failed} ဦး", 
            reply_markup=get_main_menu(chat_id)
        )
        return

    # 📌 3. အခြား Text များဆိုလျှင် Netflix Cookie မှ Token ပြောင်းသည့် လုပ်ငန်းစဉ်သို့ သွားမည်
    progress_msg = bot.reply_to(message, "စာသားရပြီ - Token ပြောင်းနေပါပြီ ခဏစောင့်ပါ...")
    
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
    # 📌 Render တွင် မအိပ်စေရန် Thread များ ပေါင်းထည့်ထားပါသည်
    Thread(target=keep_awake, daemon=True).start()
    Thread(target=run_web, daemon=True).start()
    logger.info("Bot စတင် အလုပ်လုပ်နေပါပြီ...")
    bot.infinity_polling(skip_pending=False, timeout=30, long_polling_timeout=30)
