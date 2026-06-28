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
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# /start နှိပ်လျှင် ပြမည့်စာ
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "မင်္ဂလာပါ ခင်ဗျာ။ Netflix Cookie ပါဝင်တဲ့ .txt ဖိုင်ကို ပေးပို့နိုင်ပါတယ်။")

# စာရိုက်ပို့လျှင် .txt ဖြင့်ပို့ရန် အသိပေးမည်
@bot.message_handler(content_types=['text'])
def process_text(message):
    bot.reply_to(message, "Cookie က အရှည်ကြီးဖြစ်နေရင် Telegram က ဖြတ်ပစ်တတ်လို့ပါ။ ကျေးဇူးပြု၍ .txt ဖိုင်လေးနဲ့ ပို့ပေးပါ ခင်ဗျာ။")

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
            
            # ထွက်လာတဲ့ ရလဒ်ကို token_result.txt အနေနဲ့ ပြန်သိမ်းမယ်
            with open("token_result.txt", "w", encoding="utf-8") as out_file:
                out_file.write(output_text)
                
            # ထွက်လာတဲ့ ဖိုင်ကို User ဆီ ပြန်ပို့မယ်
            with open("token_result.txt", "rb") as out_file:
                bot.send_document(message.chat.id, out_file, caption="ရပါပြီ ခင်ဗျာ။")
                
        except Exception as e:
            bot.reply_to(message, f"Error ဖြစ်သွားပါတယ် ခင်ဗျာ: {e}")
    else:
        bot.reply_to(message, ".txt ဖိုင်အမျိုးအစားသာ လက်ခံပါတယ် ခင်ဗျာ။")

if __name__ == "__main__":
    # Web Server ကို Thread သီးသန့်ဖြင့် အရင် Run ပါမယ်
    Thread(target=run_web).start()
    
    # ပြီးမှ Bot ကို Run ပါမယ်
    print("Bot စတင် အလုပ်လုပ်နေပါပြီ...")
    bot.infinity_polling()
    
