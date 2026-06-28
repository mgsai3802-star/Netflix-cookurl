import telebot
from telebot import types
import subprocess
import os
import re
import rarfile
from flask import Flask
from threading import Thread

# Token ဖတ်မည့်အပိုင်း
BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    print("Error: BOT_TOKEN ကို Environment Variable မှာ ထည့်သွင်းရသေးပါ ခင်ဗျာ။")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# အသုံးပြုသူစာရင်း သိမ်းဆည်းမည့်ဖိုင် နှင့် Admin ID
USER_FILE = "users.txt"
ADMIN_ID = 1847021130

# Render အတွက် Port ဖွင့်ပေးမည့် အပိုင်း
@app.route('/')
def alive():
    return "Bot is running online!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# Keyboard Menu ဖန်တီးသည့် Function
def get_main_menu():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    btn_start = types.KeyboardButton("/start 🔄")
    btn_rar = types.KeyboardButton("📦 RAR ကို TXT ပြောင်းရန်")
    markup.add(btn_start, btn_rar)
    return markup

# User အသစ်များကို မှတ်သားမည့် Function
def log_user(message):
    user_id = str(message.chat.id)
    username = message.from_user.username or message.from_user.first_name or "Unknown"
    
    users = {}
    if os.path.exists(USER_FILE):
        with open(USER_FILE, "r", encoding="utf-8") as f:
            for line in f:
                if "|" in line:
                    uid, uname = line.strip().split("|", 1)
                    users[uid] = uname
    
    if user_id not in users:
        with open(USER_FILE, "a", encoding="utf-8") as f:
            f.write(f"{user_id}|{username}\n")

# /start နှိပ်လျှင် ပြမည့်စာ
@bot.message_handler(commands=['start'])
def send_welcome(message):
    log_user(message) # User ကို မှတ်သားမည်
    bot.reply_to(message, "မင်္ဂလာပါ ခင်ဗျာ။ Netflix Cookie ပါဝင်တဲ့ .txt (သို့) .rar ဖိုင်ကို ပေးပို့နိုင်ပါတယ်။", reply_markup=get_main_menu())

# User စာရင်းကို ကြည့်ရန် /users Command (Admin Only)
@bot.message_handler(commands=['users'])
def show_users(message):
    # Admin ID ဟုတ်မဟုတ် စစ်ဆေးခြင်း
    if message.chat.id != ADMIN_ID:
        bot.reply_to(message, "တောင်းပန်ပါတယ် ခင်ဗျာ။ ဒီ Command ကို Admin သာ အသုံးပြုနိုင်ပါတယ်။")
        return
        
    if not os.path.exists(USER_FILE):
        bot.reply_to(message, "အသုံးပြုသူ စာရင်း မရှိသေးပါ ခင်ဗျာ။")
        return
        
    with open(USER_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
        
    count = len(lines)
    if count == 0:
        bot.reply_to(message, "အသုံးပြုသူ စာရင်း မရှိသေးပါ ခင်ဗျာ။")
        return
        
    user_list_text = f"👥 စုစုပေါင်း အသုံးပြုသူ: {count} ဦး\n\n"
    for line in lines:
        uid, uname = line.strip().split("|", 1)
        user_list_text += f"▪️ {uname} (ID: `{uid}`)\n"
        
    # စာရင်းရှည်သွားပါက ဖိုင်အနေဖြင့် ပို့ပေးမည်
    if len(user_list_text) > 4000:
        with open(USER_FILE, "rb") as doc:
            bot.send_document(message.chat.id, doc, caption=f"👥 စုစုပေါင်း အသုံးပြုသူ: {count} ဦး")
    else:
        bot.reply_to(message, user_list_text, parse_mode="Markdown")

# Keyboard မှ Menu များ နှိပ်လျှင် အလုပ်လုပ်မည့်အပိုင်း
@bot.message_handler(func=lambda message: message.text == "📦 RAR ကို TXT ပြောင်းရန်")
def handle_rar_menu(message):
    bot.reply_to(message, "RAR ဖိုင်ကို တိုက်ရိုက် ပေးပို့နိုင်ပါတယ် ခင်ဗျာ။ Bot မှ အလိုအလျောက် ဖြည်ချပြီး TXT နှင့် Token ထုတ်ပေးပါမည်။", reply_markup=get_main_menu())

@bot.message_handler(func=lambda message: message.text == "/start 🔄")
def handle_start_menu(message):
    send_welcome(message)

# စာရိုက်ပို့လျှင် .txt ဖြင့်ပို့ရန် အသိပေးမည်
@bot.message_handler(content_types=['text'])
def process_text(message):
    bot.reply_to(message, "Cookie က အရှည်ကြီးဖြစ်နေရင် Telegram က ဖြတ်ပစ်တတ်လို့ပါ။ ကျေးဇူးပြု၍ .txt သို့မဟုတ် .rar ဖိုင်လေးနဲ့ ပို့ပေးပါ ခင်ဗျာ။", reply_markup=get_main_menu())

# Document ဖိုင်ကို လက်ခံမည့်အပိုင်း
@bot.message_handler(content_types=['document'])
def process_document(message):
    file_name = message.document.file_name.lower()
    log_user(message) # ဖိုင်ပို့သူများကိုလည်း မှတ်သားမည်
    
    if file_name.endswith('.txt'):
        bot.reply_to(message, "TXT ဖိုင်လက်ခံရရှိပါပြီ ခင်ဗျာ... Token ထုတ်ပေးနေပါတယ်။")
        process_and_generate_token(message, is_rar=False)
        
    elif file_name.endswith('.rar'):
        bot.reply_to(message, "RAR ဖိုင်လက်ခံရရှိပါပြီ ခင်ဗျာ... ဖိုင်ကိုဖြည်ချပြီး Token ထုတ်ပေးနေပါတယ်။")
        process_and_generate_token(message, is_rar=True)
        
    else:
        bot.reply_to(message, ".txt နှင့် .rar ဖိုင်အမျိုးအစားများကိုသာ လက်ခံပါတယ် ခင်ဗျာ။", reply_markup=get_main_menu())

# Token ထုတ်ပေးမည့် အဓိက Function
def process_and_generate_token(message, is_rar):
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        if is_rar:
            rar_path = "temp.rar"
            with open(rar_path, 'wb') as new_file:
                new_file.write(downloaded_file)
            
            rf = rarfile.RarFile(rar_path)
            txt_found = False
            for f in rf.infolist():
                if f.filename.endswith('.txt'):
                    with rf.open(f.filename) as txt_file:
                        cookie_data = txt_file.read()
                        with open("input.txt", "wb") as out_file:
                            out_file.write(cookie_data)
                    txt_found = True
                    
                    with open("input.txt", "rb") as out_txt:
                        bot.send_document(message.chat.id, out_txt, caption="📦 RAR ထဲမှ ပြန်လည်ဖြည်ချထားသော TXT ဖိုင် ဖြစ်ပါတယ် ခင်ဗျာ။", reply_markup=get_main_menu())
                    break
                    
            if not txt_found:
                bot.send_message(message.chat.id, "RAR ဖိုင်ထဲတွင် .txt ဖိုင် ရှာမတွေ့ပါ ခင်ဗျာ။", reply_markup=get_main_menu())
                return
        else:
            with open("input.txt", 'wb') as new_file:
                new_file.write(downloaded_file)
                
        result = subprocess.run(['python3', 'nf-token-generator.py'], capture_output=True, text=True)
        output_text = result.stdout
        
        match = re.search(r'(https://netflix\.com/\?nftoken=[^\s]+)', output_text)
        
        if match:
            clean_url = match.group(1)
            reply_message = f"ရပါပြီ ခင်ဗျာ:\n\n{clean_url}\n\n⚠️ **သတိပေးချက်** - ဒီလင့်ခ်က ခဏသာ အသုံးပြုလို့ရမှာ ဖြစ်ပါတယ် ခင်ဗျာ။"
            bot.send_message(message.chat.id, reply_message, parse_mode='Markdown', reply_markup=get_main_menu())
        else:
            bot.send_message(message.chat.id, "Token URL ရှာမတွေ့ပါ ခင်ဗျာ။ Cookie ဖိုင်ကို ပြန်စစ်ဆေးပေးပါ။", reply_markup=get_main_menu())
            
    except Exception as e:
        bot.reply_to(message, f"Error ဖြစ်သွားပါတယ် ခင်ဗျာ: {e}", reply_markup=get_main_menu())

if __name__ == "__main__":
    Thread(target=run_web).start()
    print("Bot စတင် အလုပ်လုပ်နေပါပြီ...")
    bot.infinity_polling()
        
