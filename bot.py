import telebot
import subprocess
import os
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
    # Render က သတ်မှတ်ပေးတဲ့ Port ကို ယူသုံးပါမယ်
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Bot ရဲ့ လုပ်ဆောင်ချက်များ
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "မင်္ဂလာပါ ခင်ဗျာ။ Netflix Cookie ကို ပေးပို့နိုင်ပါတယ်။")

@bot.message_handler(func=lambda message: True)
def process_cookie(message):
    cookie_data = message.text
    
    with open("input.txt", "w", encoding="utf-8") as file:
        file.write(cookie_data)
        
    bot.reply_to(message, "ခဏစောင့်ပါ ခင်ဗျာ... Token ထုတ်ပေးနေပါတယ်။")
    
    try:
        result = subprocess.run(['python3', 'nf-token-generator.py'], capture_output=True, text=True)
        output_text = result.stdout
        
        if output_text:
            bot.reply_to(message, f"ရပါပြီ ခင်ဗျာ:\n\n{output_text}")
        else:
            bot.reply_to(message, "Token ထုတ်လို့ မရပါ ခင်ဗျာ။ Cookie အမှားဖြစ်နေနိုင်ပါတယ်။")
            
    except Exception as e:
        bot.reply_to(message, f"Error ဖြစ်သွားပါတယ် ခင်ဗျာ: {e}")

if __name__ == "__main__":
    # Web Server ကို Thread သီးသန့်ဖြင့် အရင် Run ပါမယ်
    Thread(target=run_web).start()
    
    # ပြီးမှ Bot ကို Run ပါမယ်
    print("Bot စတင် အလုပ်လုပ်နေပါပြီ...")
    bot.infinity_polling()


