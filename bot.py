import telebot
from telebot import types
import subprocess
import os
import re
from flask import Flask
from threading import Thread, Lock

# Token ဖတ်မည့်အပိုင်း
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    print("Error: BOT_TOKEN ကို Environment Variable မှာ ထည့်သွင်းရသေးပါ ခင်ဗျာ။")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# ပြိုင်တူဖိုင်တွေဝင်လာရင် တစ်ခုပြီးမှတစ်ခု အလုပ်လုပ်ရန် Lock
file_lock = Lock()

# အသုံးပြုသူစာရင်းကို Memory ထဲမှာပဲ သိမ်းဆည်းမည် (File မသုံးပါ)
active_users = {}
ADMIN_ID = 1847021130

@app.route('/')
def alive():
    return "Bot is running online!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Keyboard Menu
def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(types.KeyboardButton("/start 🔄"))
    return markup

# User မှတ်သားခြင်း (Memory ထဲတွင်သာ)
def log_user(message):
    user_id = str(message.chat.id)
    username = message.from_user.username or message.from_user.first_name or "Unknown"
    active_users[user_id] = username

# Commands
@bot.message_handler(commands=['start'])
def send_welcome(message):
    log_user(message)
    welcome_text = (
        "မင်္ဂလာပါ ခင်ဗျာ။\n\n"
        "**Netflix** သို့မဟုတ် **Crunchyroll** Cookie ပါဝင်သော `.txt` ဖိုင်ကို ပေးပို့နိုင်ပါတယ်ဗျ။\n"
        "Bot မှ အလိုအလျောက် ခွဲခြားပြီး Token ပြောင်းပေးပါမည်။"
    )
    bot.reply_to(message, welcome_text, parse_mode="Markdown", reply_markup=get_main_menu())

@bot.message_handler(commands=['users'])
def show_users(message):
    if message.chat.id != ADMIN_ID:
        bot.reply_to(message, "Admin သာ အသုံးပြုနိုင်ပါတယ် ခင်ဗျာ။")
        return
    
    if not active_users:
        bot.reply_to(message, "လက်ရှိတွင် အသုံးပြုသူ စာရင်း မရှိသေးပါ။")
        return
    
    # Memory ထဲက စာရင်းကို ပြန်ထုတ်ပြခြင်း
    user_list_text = f"👥 စုစုပေါင်း အသုံးပြုသူ: {len(active_users)} ဦး\n\n"
    for uid, uname in active_users.items():
        user_list_text += f"▪️ {uname} (ID: `{uid}`)\n"
    
    bot.reply_to(message, user_list_text, parse_mode="Markdown")

@bot.message_handler(func=lambda message: message.text == "/start 🔄")
def refresh_bot(message):
    send_welcome(message)

# Document လက်ခံခြင်း
@bot.message_handler(content_types=['document'])
def process_document(message):
    log_user(message)
    file_name = message.document.file_name.lower()
    
    if file_name.endswith('.txt'):
        # ဖိုင်ဝင်လာတာနဲ့ တန်းစီထားကြောင်း အရင်ပြောမည်
        progress_msg = bot.reply_to(message, "ဖိုင်လက်ခံရရှိပါပြီ။ တန်းစီနေပါသည် (Queue)...")

        # နောက်ကွယ်မှ အလှည့်ကျ အလုပ်လုပ်မည့်အပိုင်း
        def process_task():
            with file_lock: # တစ်ကြိမ်လျှင် ဖိုင်တစ်ခုသာ အလုပ်လုပ်ရန် သော့ခတ်ထားမည်
                try:
                    bot.edit_message_text(chat_id=message.chat.id, message_id=progress_msg.message_id, text="TXT ကို စတင်လုပ်ဆောင်နေပါပြီ။ ဖိုင်အမျိုးအစားကို စစ်ဆေးနေပါသည်...")
                    
                    file_info = bot.get_file(message.document.file_id)
                    downloaded_file = bot.download_file(file_info.file_path)
                    
                    # ဖိုင်ထဲက စာသားများကို ဖတ်ပြီး ဘာ Cookie လဲ ခွဲခြားမည်
                    file_content = downloaded_file.decode('utf-8', errors='ignore')
                    
                    with open("input.txt", "wb") as f: 
                        f.write(downloaded_file)
                    
                    # Netflix ဖြစ်/မဖြစ် စစ်ဆေးခြင်း
                    if "NetflixId" in file_content or ".netflix.com" in file_content:
                        bot.edit_message_text(chat_id=message.chat.id, message_id=progress_msg.message_id, text="Netflix Cookie တွေ့ရှိပါသည်။ Token ထုတ်ပေးနေပါသည်...")
                        
                        result = subprocess.run(['python3', 'nf-token-generator.py'], capture_output=True, text=True)
                        match = re.search(r'(https://netflix\.com/\?nftoken=[^\s]+)', result.stdout)
                        
                        if match:
                            clean_url = match.group(1)
                            reply = f"✅ **Netflix Token ရပါပြီ:**\n\n{clean_url}\n\n⚠️ **သတိပေးချက်** - ဒီလင့်ခ်က အချိန် 15 minutes ခန့်သာအသုံးပြုလို့ရမှာ ဖြစ်ပါတယ်ဗျ"
                        else:
                            reply = "❌ Netflix Token ရှာမတွေ့ပါ ခင်ဗျာ။"
                            
                    # Crunchyroll ဖြစ်/မဖြစ် စစ်ဆေးခြင်း
                    elif "etp_rt" in file_content or ".crunchyroll.com" in file_content:
                        bot.edit_message_text(chat_id=message.chat.id, message_id=progress_msg.message_id, text="Crunchyroll Cookie တွေ့ရှိပါသည်။ Token ထုတ်ပေးနေပါသည်...")
                        
                        result = subprocess.run(['python3', 'cr-token-generator.py'], capture_output=True, text=True)
                        match = re.search(r'Token:\s*([a-zA-Z0-9_.-]+)', result.stdout)
                        
                        if match:
                            cr_token = match.group(1)
                            reply = f"✅ **Crunchyroll Access Token ရပါပြီ:**\n\n`{cr_token}`\n\n⚠️**သတိပေးချက်** - ဒီလင့်ခ်က အချိန် 15 minutes ခန့်သာအသုံးပြုလို့ရမှာ ဖြစ်ပါတယ်ဗျ"
                        else:
                            # ⚠️ ဒီနေရာလေးကို ပြင်လိုက်တာပါ (Error အတိအကျကို ပြခိုင်းထားပါသည်)
                            error_log = result.stdout.strip()[:800]
                            stderr_log = result.stderr.strip()[:800]
                            reply = f"❌ Crunchyroll Token ထုတ်ယူလို့ မရပါဘူး ခင်ဗျာ။\n\n**အကြောင်းရင်း (Debug Log):**\n`{error_log}`\n`{stderr_log}`"
                            
                    else:
                        reply = "❌ မှားယွင်းနေသော ဖိုင်ဖြစ်ပါသည်။ Netflix သို့မဟုတ် Crunchyroll Cookie ဖိုင်ကိုသာ ပေးပို့ပါ။"
                        
                    bot.send_message(message.chat.id, reply, parse_mode='Markdown', reply_markup=get_main_menu())
                        
                    if os.path.exists("input.txt"): 
                        os.remove("input.txt")
                        
                except Exception as e:
                    bot.send_message(message.chat.id, f"Error ဖြစ်သွားပါတယ် ခင်ဗျာ: {e}", reply_markup=get_main_menu())

        # တခြား User တွေ Bot ကို သုံးလို့ရနေအောင် Thread အသစ်နဲ့ အလုပ်လုပ်ခိုင်းမည်
        Thread(target=process_task).start()
        
    else:
        bot.reply_to(message, ".txt ဖိုင်ကိုသာ လက်ခံပါတယ် ခင်ဗျာ။", reply_markup=get_main_menu())

if __name__ == "__main__":
    Thread(target=run_web).start()
    print("Bot စတင် အလုပ်လုပ်နေပါပြီ (Queue စနစ်ဖြင့်)...")
    bot.infinity_polling()
