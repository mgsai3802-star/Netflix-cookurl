import telebot
from telebot import types
import subprocess
import os
import re
from flask import Flask
from threading import Thread, Lock

BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    print("Error: BOT_TOKEN ကို Environment Variable မှာ ထည့်သွင်းရသေးပါ ခင်ဗျာ။")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)
file_lock = Lock()
active_users = {}
ADMIN_ID = 1847021130 # Admin ID နေရာတွင် သင်၏ ID ကို ထည့်ပါ

@app.route('/')
def alive():
    return "Bot is running online!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(types.KeyboardButton("/start 🔄"))
    return markup

def log_user(message):
    user_id = str(message.chat.id)
    username = message.from_user.username or message.from_user.first_name or "Unknown"
    active_users[user_id] = username

@bot.message_handler(commands=['start'])
def send_welcome(message):
    log_user(message)
    bot.reply_to(message, "မင်္ဂလာပါ ခင်ဗျာ။ Netflix Cookie ပါဝင်သော .txt ဖိုင် (သို့မဟုတ်) Cookie စာသားကို တိုက်ရိုက် ပေးပို့နိုင်ပါတယ်ဗျ", reply_markup=get_main_menu())

# --- စာသားပျက်သွားလျှင် Cookie Format (Tabs) ပြန်ပြင်ပေးမည့် Function ---
def fix_cookie_format(text):
    fixed_lines = []
    for line in text.split('\n'):
        line = line.strip()
        if not line or line.startswith('#'):
            fixed_lines.append(line)
            continue
        
        # Space များကို Netscape Cookie ၏ Column ၇ ခုအတိုင်း Tab ဖြင့်ပြန်ခြားပေးမည်
        parts = re.split(r'\s+', line, maxsplit=6)
        if len(parts) == 7:
            fixed_lines.append('\t'.join(parts))
        else:
            fixed_lines.append(line)
            
    return '\n'.join(fixed_lines)

# Data Processing Logic
def process_cookie_data(message, cookie_data, progress_msg, is_text=False):
    
    # ⚠️ ဤနေရာတွင် Scope Error မဖြစ်စေရန် Data ကို final_data ဟု နာမည်သစ်ပေးထားပါသည်
    if is_text and isinstance(cookie_data, str):
        final_data = fix_cookie_format(cookie_data)
    else:
        final_data = cookie_data

    def process_task():
        with file_lock: 
            try:
                bot.edit_message_text(chat_id=message.chat.id, message_id=progress_msg.message_id, text="စတင်လုပ်ဆောင်နေပါပြီ။ Token ထုတ်ပေးနေပါသည်...")
                
                # Bytes (File) လား၊ String (Text) လား ခွဲခြားပြီး input.txt ထဲ ရေးမည်
                if isinstance(final_data, bytes):
                    with open("input.txt", "wb") as f: 
                        f.write(final_data)
                else:
                    with open("input.txt", "w", encoding="utf-8") as f: 
                        f.write(final_data + "\n")
                
                result = subprocess.run(['python3', 'nf-token-generator.py'], capture_output=True, text=True)
                match = re.search(r'(https://netflix\.com/\?nftoken=[^\s]+)', result.stdout)
                
                if match:
                    clean_url = match.group(1)
                    reply = f"ရပါပြီ ခင်ဗျာ:\n\n{clean_url}\n\n⚠️ **သတိပေးချက်** - ဒီလင့်ခ်က အချိန် 15 minutes ခန့်သာအသုံးပြုလို့ရမှာ ဖြစ်ပါတယ်ဗျ"
                    bot.send_message(message.chat.id, reply, parse_mode='Markdown', reply_markup=get_main_menu())
                else:
                    bot.send_message(message.chat.id, "Token ရှာမတွေ့ပါ ခင်ဗျာ။ Cookie အလုပ်မလုပ်တော့တာ သို့မဟုတ် Format မှားနေတာ ဖြစ်နိုင်ပါတယ်။ (.txt ဖိုင်ဖြင့် ပေးပို့ကြည့်ရန် အကြံပြုပါသည်)", reply_markup=get_main_menu())
                    
                if os.path.exists("input.txt"): 
                    os.remove("input.txt")
                    
            except Exception as e:
                bot.send_message(message.chat.id, f"Error ဖြစ်သွားပါတယ် ခင်ဗျာ: {e}", reply_markup=get_main_menu())

    Thread(target=process_task).start()

@bot.message_handler(content_types=['document'])
def process_document(message):
    log_user(message)
    file_name = message.document.file_name.lower()
    
    if file_name.endswith('.txt'):
        progress_msg = bot.reply_to(message, "ဖိုင်လက်ခံရရှိပါပြီ။ တန်းစီနေပါသည် (Queue)...")
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        process_cookie_data(message, downloaded_file, progress_msg, is_text=False)
    else:
        bot.reply_to(message, ".txt ဖိုင်ကိုသာ လက်ခံပါတယ် ခင်ဗျာ။", reply_markup=get_main_menu())

@bot.message_handler(content_types=['text'])
def process_text_message(message):
    if message.text.startswith('/'):
        return

    log_user(message)
    
    # Telegram ၏ စာလုံးရေကန့်သတ်ချက်ကြောင့် စာပြတ်သွားနိုင်ပါက သတိပေးမည်
    if len(message.text) >= 4000:
        bot.reply_to(message, "⚠️ သတိပေးချက်: Cookie စာသားသည် အလွန်ရှည်လျားသဖြင့် Telegram မှ အောက်ပိုင်းကို ဖြတ်ချလိုက်ဖွယ်ရှိပါသည်။ အဆင်မပြေပါက .txt ဖိုင်ဖြင့်သာ ပေးပို့ပါ ခင်ဗျာ။")

    progress_msg = bot.reply_to(message, "စာသားလက်ခံရရှိပါပြီ။ တန်းစီနေပါသည် (Queue)...")
    process_cookie_data(message, message.text, progress_msg, is_text=True)

if __name__ == "__main__":
    Thread(target=run_web).start()
    print("Bot စတင် အလုပ်လုပ်နေပါပြီ (Queue စနစ်ဖြင့်)...")
    bot.infinity_polling()
    
