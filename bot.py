import telebot
from telebot import types
import subprocess
import os
import re
from flask import Flask
from threading import Thread, Lock
import time

# 1. BOT TOKEN စစ်ဆေးခြင်း
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    print("Error: BOT_TOKEN ကို Environment Variable မှာ ထည့်သွင်းထားပေးပါခင်ဗျာ။")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# 2. အလုပ်လုပ်ရန် လိုအပ်သော Variable များ
file_lock = Lock()
active_users = {}
ADMIN_ID = 1847021130

# 3. Render.com အတွက် Web Server (Alive ထားရန်)
@app.route('/')
def alive():
    return "Bot is running online!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# 4. Keyboard Menu
def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(types.KeyboardButton("/start 🔄"))
    return markup

# 5. User မှတ်သားခြင်း
def log_user(message):
    user_id = str(message.chat.id)
    username = message.from_user.username or message.from_user.first_name or "Unknown"
    active_users[user_id] = username

# 6. Commands
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
    
    user_list_text = f"👥 စုစုပေါင်း အသုံးပြုသူ: {len(active_users)} ဦး\n\n"
    for uid, uname in active_users.items():
        user_list_text += f"▪️ {uname} (ID: `{uid}`)\n"
    bot.reply_to(message, user_list_text, parse_mode="Markdown")

@bot.message_handler(func=lambda message: message.text == "/start 🔄")
def refresh_bot(message):
    send_welcome(message)

# 7. Document (Cookie file) လက်ခံခြင်း
@bot.message_handler(content_types=['document'])
def process_document(message):
    log_user(message)
    file_name = message.document.file_name.lower()
    
    if file_name.endswith('.txt'):
        progress_msg = bot.reply_to(message, "ဖိုင်လက်ခံရရှိပါပြီ။ တန်းစီနေပါသည် (Queue)...")

        def process_task():
            with file_lock:
                try:
                    bot.edit_message_text(chat_id=message.chat.id, message_id=progress_msg.message_id, text="ဖိုင်ကို စတင်လုပ်ဆောင်နေပါသည်...")
                    
                    file_info = bot.get_file(message.document.file_id)
                    downloaded_file = bot.download_file(file_info.file_path)
                    file_content = downloaded_file.decode('utf-8', errors='ignore')
                    
                    with open("input.txt", "wb") as f: 
                        f.write(downloaded_file)
                    
                    # Netflix စစ်ဆေးခြင်း
                    if "NetflixId" in file_content or ".netflix.com" in file_content:
                        result = subprocess.run(['python3', 'nf-token-generator.py'], capture_output=True, text=True)
                        match = re.search(r'(https://netflix\.com/\?nftoken=[^\s]+)', result.stdout)
                        reply = f"✅ **Netflix Token ရပါပြီ:**\n{match.group(1)}" if match else "❌ Netflix Token ရှာမတွေ့ပါ။"
                    
                    # Crunchyroll စစ်ဆေးခြင်း
                    elif "etp_rt" in file_content or ".crunchyroll.com" in file_content:
                        result = subprocess.run(['python3', 'cr-token-generator.py'], capture_output=True, text=True)
                        match = re.search(r'Token:\s*([a-zA-Z0-9_.-]+)', result.stdout)
                        
                        if match:
                            reply = f"✅ **Crunchyroll Access Token ရပါပြီ:**\n\n`{match.group(1)}`"
                        else:
                            error_log = result.stdout.strip()[:1000]
                            stderr_log = result.stderr.strip()[:1000]
                            reply = f"❌ Token ထုတ်ယူလို့ မရပါဘူး ခင်ဗျာ။\n\nDebug Log:\n`{error_log}`\n`{stderr_log}`"
                    else:
                        reply = "❌ မှားယွင်းနေသော ဖိုင်ဖြစ်ပါသည်။ Netflix သို့မဟုတ် Crunchyroll Cookie ဖိုင်ကိုသာ ပေးပို့ပါ။"
                        
                    bot.send_message(message.chat.id, reply, parse_mode='Markdown', reply_markup=get_main_menu())
                    if os.path.exists("input.txt"): os.remove("input.txt")
                        
                except Exception as e:
                    bot.send_message(message.chat.id, f"Error ဖြစ်သွားပါတယ် ခင်ဗျာ: {e}")

        Thread(target=process_task).start()
    else:
        bot.reply_to(message, ".txt ဖိုင်ကိုသာ လက်ခံပါတယ် ခင်ဗျာ။", reply_markup=get_main_menu())

# 8. Main execution (Polling & Web)
if __name__ == "__main__":
    # Web server စတင်ခြင်း
    Thread(target=run_web).start()
    print("Bot စတင် အလုပ်လုပ်နေပါပြီ...")
    
    # Render အတွက် အရေးကြီးသော Polling (409 Error မတက်အောင် ပြင်ဆင်ထားခြင်း)
    bot.remove_webhook()
    time.sleep(1)
    bot.infinity_polling(allowed_updates=telebot.util.update_types)
