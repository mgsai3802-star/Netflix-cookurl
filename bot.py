import telebot
from telebot import types
import subprocess
import os
import re
from flask import Flask
from threading import Thread

# Token ဖတ်မည့်အပိုင်း
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    print("Error: BOT_TOKEN ကို Environment Variable မှာ ထည့်သွင်းရသေးပါ ခင်ဗျာ။")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# User List သိမ်းမည့်ဖိုင် နှင့် Admin ID
USER_FILE = "users.txt"
ADMIN_ID = 1847021130

@app.route('/')
def alive():
    return "Bot is running online!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Keyboard Menu ဖန်တီးသည့် Function (Start ခလုတ်သာ ကျန်ရှိသည်)
def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(types.KeyboardButton("/start 🔄"))
    return markup

# User မှတ်သားခြင်း
def log_user(message):
    user_id = str(message.chat.id)
    username = message.from_user.username or message.from_user.first_name or "Unknown"
    if os.path.exists(USER_FILE):
        with open(USER_FILE, "r", encoding="utf-8") as f:
            if user_id in f.read(): return
    with open(USER_FILE, "a", encoding="utf-8") as f:
        f.write(f"{user_id}|{username}\n")

# Commands
@bot.message_handler(commands=['start'])
def send_welcome(message):
    log_user(message)
    bot.reply_to(message, "မင်္ဂလာပါ ခင်ဗျာ။ Netflix Cookie ပါဝင်သော .txt ဖိုင်ကို ပေးပို့နိုင်ပါသည်။", reply_markup=get_main_menu())

@bot.message_handler(commands=['users'])
def show_users(message):
    if message.chat.id != ADMIN_ID:
        bot.reply_to(message, "Admin သာ အသုံးပြုနိုင်ပါတယ် ခင်ဗျာ။")
        return
    if os.path.exists(USER_FILE):
        with open(USER_FILE, "rb") as f:
            bot.send_document(message.chat.id, f, caption="အသုံးပြုသူစာရင်း")
    else:
        bot.reply_to(message, "စာရင်းမရှိသေးပါ")

@bot.message_handler(func=lambda message: message.text == "/start 🔄")
def refresh_bot(message):
    send_welcome(message)

# Document လက်ခံခြင်း
@bot.message_handler(content_types=['document'])
def process_document(message):
    log_user(message)
    file_name = message.document.file_name.lower()
    
    if file_name.endswith('.txt'):
        bot.reply_to(message, "TXT လက်ခံရရှိပါပြီ။ Token ထုတ်ပေးနေပါသည်...")
        try:
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            with open("input.txt", "wb") as f: f.write(downloaded_file)
            
            result = subprocess.run(['python3', 'nf-token-generator.py'], capture_output=True, text=True)
            match = re.search(r'(https://netflix\.com/\?nftoken=[^\s]+)', result.stdout)
            
            if match:
                reply = f"ရပါပြီ ခင်ဗျာ:\n\n{match.group(1)}\n\n⚠️ **သတိပေးချက်** - ဒီလင့်ခ်က ခဏသာ အသုံးပြုလို့ရမှာ ဖြစ်ပါတယ် ခင်ဗျာ။"
                bot.send_message(message.chat.id, reply, parse_mode='Markdown', reply_markup=get_main_menu())
            else:
                bot.reply_to(message, "Token ရှာမတွေ့ပါ ခင်ဗျာ။")
        except Exception as e:
            bot.reply_to(message, f"Error ဖြစ်သွားပါတယ် ခင်ဗျာ: {e}")
    else:
        bot.reply_to(message, ".txt ဖိုင်ကိုသာ လက်ခံပါတယ် ခင်ဗျာ။", reply_markup=get_main_menu())

if __name__ == "__main__":
    Thread(target=run_web).start()
    print("Bot စတင် အလုပ်လုပ်နေပါပြီ...")
    bot.infinity_polling()


