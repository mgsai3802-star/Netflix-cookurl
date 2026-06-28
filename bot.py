import telebot
from telebot import types
import subprocess
import os
import re
import rarfile
from flask import Flask
from threading import Thread

# Render ပေါ်မှာ unrar tool ကို ရှာဖွေနိုင်ရန်
rarfile.UNRAR_TOOL = "/usr/bin/unrar"

BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

USER_FILE = "users.txt"
ADMIN_ID = 1847021130

@app.route('/')
def alive():
    return "Bot is running online!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton("/start 🔄"), types.KeyboardButton("📦 RAR ကို TXT ပြောင်းရန်"))
    return markup

def log_user(message):
    user_id = str(message.chat.id)
    username = message.from_user.username or message.from_user.first_name or "Unknown"
    if os.path.exists(USER_FILE):
        with open(USER_FILE, "r", encoding="utf-8") as f:
            if user_id in f.read(): return
    with open(USER_FILE, "a", encoding="utf-8") as f:
        f.write(f"{user_id}|{username}\n")

@bot.message_handler(commands=['start'])
def send_welcome(message):
    log_user(message)
    bot.reply_to(message, "မင်္ဂလာပါ ခင်ဗျာ။ Netflix Cookie ပါဝင်သော .txt သို့မဟုတ် .rar ဖိုင်ကို ပေးပို့နိုင်ပါသည်။", reply_markup=get_main_menu())

@bot.message_handler(commands=['users'])
def show_users(message):
    if message.chat.id != ADMIN_ID:
        bot.reply_to(message, "Admin သာ အသုံးပြုနိုင်ပါတယ် ခင်ဗျာ။")
        return
    if os.path.exists(USER_FILE):
        with open(USER_FILE, "rb") as f:
            bot.send_document(message.chat.id, f, caption="အသုံးပြုသူစာရင်း")

@bot.message_handler(func=lambda message: message.text == "/start 🔄")
def refresh_bot(message):
    send_welcome(message)

@bot.message_handler(content_types=['document'])
def process_document(message):
    log_user(message)
    file_name = message.document.file_name.lower()
    
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # RAR ဖိုင်ဖြစ်ခဲ့လျှင် TXT ပြန်ထုတ်ပေးမည်
        if file_name.endswith('.rar'):
            bot.reply_to(message, "RAR ဖိုင်လက်ခံရရှိပါပြီ။ ဖြည်ချနေသည်...")
            with open("temp.rar", "wb") as f: f.write(downloaded_file)
            rf = rarfile.RarFile("temp.rar")
            for f in rf.infolist():
                if f.filename.endswith(".txt"):
                    with rf.open(f.filename) as txt_file:
                        with open("result.txt", "wb") as out: out.write(txt_file.read())
                    with open("result.txt", "rb") as out:
                        bot.send_document(message.chat.id, out, caption="📦 RAR ထဲမှ TXT ဖိုင် ဖြစ်ပါသည်။")
                    break
        
        # TXT ဖြစ်ခဲ့လျှင် Token ထုတ်ပေးမည်
        elif file_name.endswith('.txt'):
            bot.reply_to(message, "TXT လက်ခံရရှိပါပြီ။ Token ထုတ်ပေးနေပါသည်...")
            with open("input.txt", "wb") as f: f.write(downloaded_file)
            result = subprocess.run(['python3', 'nf-token-generator.py'], capture_output=True, text=True)
            match = re.search(r'(https://netflix\.com/\?nftoken=[^\s]+)', result.stdout)
            if match:
                reply = f"ရပါပြီ ခင်ဗျာ:\n\n{match.group(1)}\n\n⚠️ **သတိပေးချက်** - ဒီလင့်ခ်က ခဏသာ အသုံးပြုလို့ရမှာ ဖြစ်ပါတယ် ခင်ဗျာ။"
                bot.send_message(message.chat.id, reply, parse_mode='Markdown', reply_markup=get_main_menu())
            else:
                bot.reply_to(message, "Token ရှာမတွေ့ပါ ခင်ဗျာ။")
    except Exception as e:
        bot.reply_to(message, f"Error: {e}")

if __name__ == "__main__":
    Thread(target=run_web).start()
    bot.infinity_polling()

