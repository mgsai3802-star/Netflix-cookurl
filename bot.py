import telebot
from telebot import types # Keyboard အတွက် ထပ်ထည့်ထားသည်
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

# Render အတွက် Port ဖွင့်ပေးမည့် အပိုင်း
@app.route('/')
def alive():
    return "Bot is running online!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Keyboard Menu ဖန်တီးသည့် Function
def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    btn_rar = types.KeyboardButton("📦 RAR ကို TXT ပြောင်းရန်")
    markup.add(btn_rar)
    return markup

# /start နှိပ်လျှင် ပြမည့်စာ
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "မင်္ဂလာပါ ခင်ဗျာ။ Netflix Cookie ပါဝင်တဲ့ .txt ဖိုင်ကို ပေးပို့နိုင်ပါတယ်။", reply_markup=get_main_menu())

# Keyboard က Menu ကို နှိပ်လျှင် အလုပ်လုပ်မည့်အပိုင်း
@bot.message_handler(func=lambda message: message.text == "📦 RAR ကို TXT ပြောင်းရန်")
def handle_rar_menu(message):
    bot.reply_to(message, "RAR ဖိုင်ကို ပေးပို့နိုင်ပါတယ် ခင်ဗျာ။\n\n(မှတ်ချက် - လောလောဆယ် Menu Button အနေနဲ့ ထည့်ပေးထားခြင်းဖြစ်ပြီး၊ RAR ဖိုင်ကို တကယ် ဖြည်ချဖို့အတွက် Code ထပ်မံရေးသားရန် လိုအပ်ပါသေးသည် ခင်ဗျာ။)")

# စာရိုက်ပို့လျှင် .txt ဖြင့်ပို့ရန် အသိပေးမည်
@bot.message_handler(content_types=['text'])
def process_text(message):
    bot.reply_to(message, "Cookie က အရှည်ကြီးဖြစ်နေရင် Telegram က ဖြတ်ပစ်တတ်လို့ပါ။ ကျေးဇူးပြု၍ .txt ဖိုင်လေးနဲ့ ပို့ပေးပါ ခင်ဗျာ။", reply_markup=get_main_menu())

# Document (.txt) ဖိုင်ကို လက်ခံမည့်အပိုင်း
@bot.message_handler(content_types=['document'])
def process_document(message):
    if message.document.file_name.endswith('.txt'):
        bot.reply_to(message, "ဖိုင်လက်ခံရရှိပါပြီ ခင်ဗျာ... Token ထုတ်ပေးနေပါတယ်။")
        
        try:
            # User ပို့လိုက်တဲ့ .txt ဖိုင်ကို Download ဆွဲပြီး input.txt အနေနဲ့ သိမ်းမယ်
            file_info = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            
            with open("input.txt", 'wb') as new_file:
                new_file.write(downloaded_file)
                
            # nf-token-generator.py ကို Run မယ်
            result = subprocess.run(['python3', 'nf-token-generator.py'], capture_output=True, text=True)
            output_text = result.stdout
            
            # Regex အသုံးပြု၍ https://netflix.com/?nftoken= ဖြင့်စသော လင့်ခ်ကိုသာ ဆွဲထုတ်မည်
            match = re.search(r'(https://netflix\.com/\?nftoken=[^\s]+)', output_text)
            
            if match:
                clean_url = match.group(1)
                
                # လင့်ခ်အောက်တွင် သတိပေးချက်စာသား ထည့်သွင်းခြင်း
                reply_message = f"ရပါပြီ ခင်ဗျာ:\n\n`{clean_url}`\n\n⚠️ **သတိပေးချက်** - ဒီလင့်ခ်က ခဏသာ အသုံးပြုလို့ရမှာ ဖြစ်ပါတယ် ခင်ဗျာ။"
                
                bot.send_message(message.chat.id, reply_message, parse_mode='Markdown', reply_markup=get_main_menu())
            else:
                bot.send_message(message.chat.id, "Token URL ရှာမတွေ့ပါ ခင်ဗျာ။ Cookie ကို ပြန်စစ်ဆေးပေးပါ။", reply_markup=get_main_menu())
                
        except Exception as e:
            bot.reply_to(message, f"Error ဖြစ်သွားပါတယ် ခင်ဗျာ: {e}", reply_markup=get_main_menu())
    else:
        bot.reply_to(message, ".txt ဖိုင်အမျိုးအစားသာ လက်ခံပါတယ် ခင်ဗျာ။", reply_markup=get_main_menu())

if __name__ == "__main__":
    Thread(target=run_web).start()
    print("Bot စတင် အလုပ်လုပ်နေပါပြီ...")
    bot.infinity_polling()

