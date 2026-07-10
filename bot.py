import os
import re
import json
import time
import urllib.parse
from datetime import datetime
from threading import Thread
import requests
import telebot
from telebot import types
from flask import Flask
from urllib3.exceptions import InsecureRequestWarning

# --- SETUP & CONFIG ---
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    print("Error: BOT_TOKEN ကို Environment Variable မှာ ထည့်သွင်းရသေးပါ ခင်ဗျာ။")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

active_users = {}
ADMIN_ID = 1847021130

API_URL = "https://ios.prod.ftl.netflix.com/iosui/user/15.48"
COOKIE_KEYS = ("NetflixId", "SecureNetflixId", "nfvdid", "OptanonConsent")
REQUIRED_COOKIE = "NetflixId"

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

QUERY_PARAMS = {
    "appVersion": "15.48.1",
    "config": '{"gamesInTrailersEnabled":"false","isTrailersEvidenceEnabled":"false","cdsMyListSortEnabled":"true","kidsBillboardEnabled":"true","addHorizontalBoxArtToVideoSummariesEnabled":"false","skOverlayTestEnabled":"false","homeFeedTestTVMovieListsEnabled":"false","baselineOnIpadEnabled":"true","trailersVideoIdLoggingFixEnabled":"true","postPlayPreviewsEnabled":"false","bypassContextualAssetsEnabled":"false","roarEnabled":"false","useSeason1AltLabelEnabled":"false","disableCDSSearchPaginationSectionKinds":["searchVideoCarousel"],"cdsSearchHorizontalPaginationEnabled":"true","searchPreQueryGamesEnabled":"true","kidsMyListEnabled":"true","billboardEnabled":"true","useCDSGalleryEnabled":"true","contentWarningEnabled":"true","videosInPopularGamesEnabled":"true","avifFormatEnabled":"false","sharksEnabled":"true"}',
    "device_type": "NFAPPL-02-",
    "esn": "NFAPPL-02-IPHONE8%3D1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "idiom": "phone",
    "iosVersion": "15.8.5",
    "isTablet": "false",
    "languages": "en-US",
    "locale": "en-US",
    "maxDeviceWidth": "375",
    "model": "saget",
    "modelType": "IPHONE8-1",
    "odpAware": "true",
    "path": '["account","token","default"]',
    "pathFormat": "graph",
    "pixelDensity": "2.0",
    "progressive": "false",
    "responseFormat": "json",
}

BASE_HEADERS = {
    "User-Agent": "Argo/15.48.1 (iPhone; iOS 15.8.5; Scale/2.00)",
    "x-netflix.request.attempt": "1",
    "x-netflix.request.client.user.guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.context.profile-guid": "A4CS633D7VCBPE2GPK2HL4EKOE",
    "x-netflix.request.routing": '{"path":"/nq/mobile/nqios/~15.48.0/user","control_tag":"iosui_argo"}',
    "x-netflix.context.app-version": "15.48.1",
    "x-netflix.argo.translated": "true",
    "x-netflix.context.form-factor": "phone",
    "x-netflix.context.sdk-version": "2012.4",
    "x-netflix.client.appversion": "15.48.1",
    "x-netflix.context.max-device-width": "375",
    "x-netflix.context.ab-tests": "",
    "x-netflix.tracing.cl.useractionid": "4DC655F2-9C3C-4343-8229-CA1B003C3053",
    "x-netflix.client.type": "argo",
    "x-netflix.client.ftl.esn": "NFAPPL-02-IPHONE8=1-PXA-02026U9VV5O8AUKEAEO8PUJETCGDD4PQRI9DEB3MDLEMD0EACM4CS78LMD334MN3MQ3NMJ8SU9O9MVGS6BJCURM1PH1MUTGDPF4S4200",
    "x-netflix.context.locales": "en-US",
    "x-netflix.context.top-level-uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.client.iosversion": "15.8.5",
    "accept-language": "en-US;q=1",
    "x-netflix.argo.abtests": "",
    "x-netflix.context.os-version": "15.8.5",
    "x-netflix.request.client.context": '{"appState":"foreground"}',
    "x-netflix.context.ui-flavor": "argo",
    "x-netflix.argo.nfnsm": "9",
    "x-netflix.context.pixel-density": "2.0",
    "x-netflix.request.toplevel.uuid": "90AFE39F-ADF1-4D8A-B33E-528730990FE3",
    "x-netflix.request.client.timezoneid": "Asia/Dhaka",
}

# --- FLASK WEB SERVER (FOR KEEP ALIVE) ---
@app.route('/')
def alive():
    return "Bot is running online!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- UTILITY FUNCTIONS ---
def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(types.KeyboardButton("/start 🔄"))
    return markup

def log_user(message):
    user_id = str(message.chat.id)
    username = message.from_user.username or message.from_user.first_name or "Unknown"
    active_users[user_id] = username

def parse_netscape_cookie_line(line):
    parts = line.strip().split("\t")
    if len(parts) >= 7:
        return {parts[5]: parts[6]}
    return {}

def _decode_cookie_value(value):
    if isinstance(value, str) and "%" in value:
        try:
            return urllib.parse.unquote(value)
        except Exception:
            return value
    return value

def extract_cookie_dict(text):
    cookie_dict = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        cookie_dict.update(parse_netscape_cookie_line(line))

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, list):
        for cookie in data:
            name = cookie.get("name")
            value = cookie.get("value")
            if name in COOKIE_KEYS and isinstance(value, str):
                cookie_dict[name] = _decode_cookie_value(value)
    elif isinstance(data, dict):
        if any(key in data for key in COOKIE_KEYS):
            for key in COOKIE_KEYS:
                value = data.get(key)
                if isinstance(value, str):
                    cookie_dict[key] = _decode_cookie_value(value)
        elif isinstance(data.get("cookies"), list):
            for cookie in data["cookies"]:
                name = cookie.get("name")
                value = cookie.get("value")
                if name in COOKIE_KEYS and isinstance(value, str):
                    cookie_dict[name] = _decode_cookie_value(value)

    for key in COOKIE_KEYS:
        if key in cookie_dict:
            continue
        match = re.search(rf"(?<!\w){re.escape(key)}=([^;,\s]+)", text)
        if match:
            cookie_dict[key] = _decode_cookie_value(match.group(1))

    return cookie_dict

def fetch_nftoken(cookie_dict):
    netflix_id = cookie_dict.get(REQUIRED_COOKIE)
    if not netflix_id:
        raise ValueError("ဖိုင်ထဲတွင် NetflixId Cookie မတွေ့ရပါ ခင်ဗျာ။")

    headers = dict(BASE_HEADERS)
    headers["Cookie"] = f"NetflixId={netflix_id}"

    response = requests.get(
        API_URL,
        params=QUERY_PARAMS,
        headers=headers,
        timeout=20,
        verify=False,
    )
    response.raise_for_status()

    data = response.json()
    token_data = (
        (((data.get("value") or {}).get("account") or {}).get("token") or {}).get("default")
        or {}
    )
    token = token_data.get("token")
    expires = token_data.get("expires")

    if not token:
        raise ValueError("Netflix API မှ Token ထုတ်ပေးခြင်းမရှိပါ (Cookie သက်တမ်းကုန်နေနိုင်ပါသည်)။")

    if isinstance(expires, int) and len(str(expires)) == 13:
        expires //= 1000

    return token, expires

# --- TELEGRAM BOT HANDLERS ---
@bot.message_handler(commands=['start'])
def send_welcome(message):
    log_user(message)
    bot.reply_to(message, "မင်္ဂလာပါ ခင်ဗျာ။ Netflix Cookie ပါဝင်သော .txt ဖိုင်ကို ပေးပို့နိုင်ပါသည်။", reply_markup=get_main_menu())

@bot.message_handler(commands=['users'])
def show_users(message):
    if message.chat.id != ADMIN_ID:
        bot.reply_to(message, "Admin သာ အသုံးပြုနိုင်ပါတယ် ခင်ဗျာ။")
        return
    if not active_users:
        bot.reply_to(message, "လက်ရှိတွင် အသုံးပြုသူ စာရင်း မရှိသေးပါ။")
        return
    
    user_list_text = f"👥 စုစုပေါင်း အသုံးပြုသူ: {len(active_users)} ဦး\n\n"
    for uid, uname in active_users.items():
        user_list_text += f"▪️ {uname} (ID: `{uid}`)\n"
    
    bot.reply_to(message, user_list_text, parse_mode="Markdown")

@bot.message_handler(func=lambda message: message.text == "/start 🔄")
def refresh_bot(message):
    send_welcome(message)

# Document (TXT) ဖိုင်လက်ခံပြီး လုပ်ဆောင်ပေးမည့်အပိုင်း
@bot.message_handler(content_types=['document'])
def process_document(message):
    log_user(message)
    file_name = message.document.file_name.lower()
    
    if not file_name.endswith('.txt'):
        bot.reply_to(message, ".txt ဖိုင်ကိုသာ လက်ခံပါတယ် ခင်ဗျာ။", reply_markup=get_main_menu())
        return

    # အလုပ်စလုပ်ကြောင်းနှင့် Loading 10% ပြခြင်း
    progress_msg = bot.reply_to(message, "⏳ စတင်လုပ်ဆောင်နေပါပြီ...\n\n|█▒▒▒▒▒▒▒▒▒| 10% [ဖိုင်ကို စစ်ဆေးနေသည်]")
    
    try:
        # Telegram Server မှ ဖိုင်ဒေါင်းလုဒ်ဆွဲခြင်း
        file_info = bot.get_file(message.document.file_id)
        downloaded_bytes = bot.download_file(file_info.file_path)
        
        # Loading 40% သို့ ပြောင်းလဲခြင်း
        time.sleep(0.5)
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=progress_msg.message_id,
            text="Loading...\n\n|████▒▒▒▒▒▒| 40% [Cookie ကို စာသားအဖြစ် ပြောင်းလဲနေသည်]"
        )
        
        # စာသားအဖြစ်ပြောင်းပြီး Cookie ရှာဖွေခြင်း (ဖိုင်ထဲမသိမ်းဘဲ RAM ထဲမှာတင် တိုက်ရိုက်လုပ်သည်)
        raw_cookie_text = downloaded_bytes.decode('utf-8', errors='ignore')
        cookie_dict = extract_cookie_dict(raw_cookie_text)
        
        if not cookie_dict:
            bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=progress_msg.message_id,
                text="❌ Error: ဖိုင်ထဲတွင် မှန်ကန်သော Cookie ပုံစံ ရှာမတွေ့ပါ ခင်ဗျာ။",
                reply_markup=get_main_menu()
            )
            return

        # Loading 70% သို့ ပြောင်းလဲခြင်း
        time.sleep(0.5)
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=progress_msg.message_id,
            text="⚙️ Cookie စစ်ဆေးပြီးပါပြီ...\n\n|███████▒▒▒| 70% [Netflix  သို့ ချိတ်ဆက်တောင်းဆိုနေသည်]"
        )
        
        # Netflix API သို့ လှမ်းတောင်းခြင်း
        token, expires = fetch_nftoken(cookie_dict)
        
        # Loading 90% သို့ ပြောင်းလဲခြင်း
        time.sleep(0.5)
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=progress_msg.message_id,
            text="အောင်မြင်ပါသည်...\n\n|█████████▒| 90% [လင့်ခ်ကို ထုတ်ပေးနေသည်]"
        )
        
        # အားလုံးပြီးဆုံးကြောင်း 100% ပြပြီး Link ပို့ပေးခြင်း
        clean_url = "https://netflix.com/?nftoken=" + token
        expiry_str = datetime.fromtimestamp(expires).strftime("%Y-%m-%d %H:%M:%S") if isinstance(expires, (int, float)) else "Unknown"
        
        reply_final = (
            "✅ **အောင်မြင်စွာ Token ထုတ်ပြီးပါပြီ**\n\n"
            f"|██████████| 100%\n\n"
            f"🔗 **Login URL:**\n`{clean_url}`\n\n"
            f"📅 **Expires:** `{expiry_str}`\n\n"
            "⚠️ **သတိပေးချက်** - ဒီလင့်ခ်က သက်တမ်း အချိန်ခနသာအသုံးပြုနိုင်မှာ ဖြစ်ပါတယ်ဗျ"
        )
        
        time.sleep(0.3)
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=progress_msg.message_id,
            text=reply_final,
            parse_mode='Markdown',
            reply_markup=get_main_menu()
        )
        
    except Exception as e:
        bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=progress_msg.message_id,
            text=f"❌ Error ဖြစ်သွားပါတယ် ခင်ဗျာ:\n`{str(e)}`",
            parse_mode='Markdown',
            reply_markup=get_main_menu()
        )

if __name__ == "__main__":
    # Flask Web server ကို Background Thread အနေနဲ့ Run မည်
    Thread(target=run_web).start()
    print("Bot စတင် အလုပ်လုပ်နေပါပြီ (RAM Optimization & Real-time Loading ပါဝင်ပြီး)...")
    bot.infinity_polling()


