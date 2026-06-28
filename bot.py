import telebot
import subprocess
import os

# Render ရဲ့ Environment Variable ကနေ 'BOT_TOKEN' ကို လှမ်းဖတ်ပါမယ်
# ဒါကြောင့် Code ထဲမှာ Token ကို တိုက်ရိုက်ထည့်စရာ မလိုတော့ပါဘူး
BOT_TOKEN = os.environ.get('BOT_TOKEN')

if not BOT_TOKEN:
    print("Error: BOT_TOKEN ကို Environment Variable မှာ ထည့်သွင်းရသေးပါ ခင်ဗျာ။")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "မင်္ဂလာပါ ခင်ဗျာ။ Netflix Cookie ကို ပေးပို့နိုင်ပါတယ်။")

@bot.message_handler(func=lambda message: True)
def process_cookie(message):
    cookie_data = message.text
    
    # User ပို့လိုက်တဲ့ Cookie ကို input.txt ထဲ ရေးထည့်မယ်
    with open("input.txt", "w", encoding="utf-8") as file:
        file.write(cookie_data)
        
    bot.reply_to(message, "ခဏစောင့်ပါ ခင်ဗျာ... Token ထုတ်ပေးနေပါတယ်။")
    
    try:
        # nf-token-generator.py ကို လှမ်း Run မယ်
        result = subprocess.run(['python3', 'nf-token-generator.py'], capture_output=True, text=True)
        output_text = result.stdout
        
        # ထွက်လာတဲ့ ရလဒ်ကို Bot ကနေ ပြန်ပို့မယ်
        if output_text:
            bot.reply_to(message, f"ရပါပြီ ခင်ဗျာ:\n\n{output_text}")
        else:
            bot.reply_to(message, "Token ထုတ်လို့ မရပါ ခင်ဗျာ။ Cookie အမှားဖြစ်နေနိုင်ပါတယ်။")
            
    except Exception as e:
        bot.reply_to(message, f"Error ဖြစ်သွားပါတယ် ခင်ဗျာ: {e}")

# Bot ကို အမြဲ Run နေစေဖို့
print("Bot စတင် အလုပ်လုပ်နေပါပြီ...")
bot.infinity_polling()

