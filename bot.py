import telebot
import subprocess
import os
import re
import threading
from flask import Flask
from threading import Thread

BOT_TOKEN = os.environ.get('BOT_TOKEN')
if not BOT_TOKEN:
    print("Error: BOT_TOKEN ကို Environment Variable မှာ ထည့်သွင်းရသေးပါ ခင်ဗျာ။")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)
file_lock = threading.Lock()
ADMIN_ID = 1847021130

# --- Pre-loaded existing users (so /users shows them even before they message again) ---
active_users = {
    "1847021130": "Ren2512",
    "5786095389": "thureinlinlinn",
    "6609444194": "luke65214",
    "1833851827": "Aung",
    "6050862261": "khajhar",
    "1240231180": "VPNetwork25",
    "5555183383": "Sa Nay Maung",
    "1510379959": "Khine",
    "8029459862": "digitalworldmyanmar1212",
    "6445480256": "NyeinCHANAUNG7",
    "7814624012": "aeiou690",
    "8577702613": "Reno366",
    "7378715486": "NyeinChaNAungW",
    "5604493826": "Akai888",
    "5272159743": "phetkyam",
}

# --- track running process + stop flag per user ---
running_process = {}
stop_flags = {}


@app.route('/')
def alive():
    return "Bot is running online!"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


def get_main_menu():
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        telebot.types.KeyboardButton("/start 🔄"),
        telebot.types.KeyboardButton("⏹ ဟိုးစတော့")
    )
    return markup


def log_user(message):
    user_id = str(message.chat.id)
    username = message.from_user.username or message.from_user.first_name or "Unknown"
    active_users[user_id] = username


@bot.message_handler(commands=['start'])
def send_welcome(message):
    log_user(message)
    bot.reply_to(
        message,
        "မင်္ဂလာပါ ဝေ့ -Netflix Cookie ပါတဲ့ .txtဖိုင် ဖြစ်ဖြစ် textဖြစ်ဖြစ် ပို့လိုက်ကွာ",
        reply_markup=get_main_menu()
    )


@bot.message_handler(commands=['users'])
def show_users(message):
    if message.chat.id != ADMIN_ID:
        bot.reply_to(message, "ပိုင်ရှင်ရှိတယ်")
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


@bot.message_handler(func=lambda message: message.text == "⏹ ဟိုးစတော့")
def stop_process(message):
    user_id = str(message.chat.id)
    proc = running_process.get(user_id)
    if proc and proc.poll() is None:
        stop_flags[user_id] = True
        proc.terminate()
        bot.reply_to(
            message,
            "⏹ မလုပ်ပေးတော့ဘူးကွာ",
            reply_markup=get_main_menu()
        )
    else:
        bot.reply_to(
            message,
            "ဘာပို့ထားလို့ ရပ်ခိုင်းနေတာလဲဟ",
            reply_markup=get_main_menu()
        )


@bot.message_handler(content_types=['document'])
def process_document(message):
    log_user(message)
    user_id = str(message.chat.id)
    stop_flags[user_id] = False
    file_name = message.document.file_name.lower()

    if file_name.endswith('.txt'):
        progress_msg = bot.reply_to(message, "ဖိုင်ငါရပြီ - အစဉ်လိုက်ပဲ‌သွားမယ်ကွ(Queue)...")

        def process_task():
            with file_lock:
                try:
                    if stop_flags.get(user_id):
                        bot.edit_message_text(
                            chat_id=message.chat.id, message_id=progress_msg.message_id,
                            text="⏹ မလုပ်ပေးတော့ဘူးကွာ"
                        )
                        return

                    bot.edit_message_text(
                        chat_id=message.chat.id, message_id=progress_msg.message_id,
                        text="TXT ရပြီ Token ပြန်ပေးမယ် စောင့်နေ."
                    )

                    file_info = bot.get_file(message.document.file_id)
                    downloaded_file = bot.download_file(file_info.file_path)
                    with open("input.txt", "wb") as f:
                        f.write(downloaded_file)

                    if stop_flags.get(user_id):
                        bot.send_message(message.chat.id, "⏹ မလုပ်ပေးတော့ဘူးကွ", reply_markup=get_main_menu())
                        return

                    proc = subprocess.Popen(
                        ['python3', 'nf-token-generator.py'],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                    )
                    running_process[user_id] = proc
                    stdout, stderr = proc.communicate()
                    running_process.pop(user_id, None)

                    if stop_flags.get(user_id):
                        bot.send_message(message.chat.id, "⏹ မလုပ်ပေးတော့ဘူးကွ", reply_markup=get_main_menu())
                        return

                    match = re.search(r'(https://netflix\.com/\?nftoken=[^\s]+)', stdout)
                    if match:
                        clean_url = match.group(1)
                        reply = (
                            f"ရပြီကွ သုံးတော့:\n\n{clean_url}\n\n"
                            "⚠️ **သတိပေးချက်** - ဒီလင့်ခ်က 15 minutes မိ‌နစ်လောက်ပဲကွ"
                        )
                        bot.send_message(message.chat.id, reply, parse_mode='Markdown', reply_markup=get_main_menu())
                    else:
                        bot.send_message(message.chat.id, "Token ရှာမတွေ့ဘူးဟ နောက်တစ်ခုစမ်းကွာ", reply_markup=get_main_menu())

                    if os.path.exists("input.txt"):
                        os.remove("input.txt")

                except Exception as e:
                    bot.send_message(message.chat.id, f"Error ဖြစ်နေပြီကွ: {e}", reply_markup=get_main_menu())
                finally:
                    running_process.pop(user_id, None)

        Thread(target=process_task).start()
    else:
        bot.reply_to(message, ".txt ဖိုင်ပဲပို့ဟ", reply_markup=get_main_menu())


if __name__ == "__main__":
    Thread(target=run_web).start()
    print("Bot စတင် အလုပ်လုပ်နေပါပြီ (Queue စနစ်ဖြင့်)...")
    bot.infinity_polling()
    
