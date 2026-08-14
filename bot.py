import telebot
import subprocess
import sys
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

running_process = {}
stop_flags = {}
awaiting_broadcast = {}   # admin_id (str) -> True/False

STOP_BTN = "⏹ ဟိုးစတော့"
BROADCAST_CANCEL_BTN = "❌ Broadcast ပယ်ဖျက်"

# Matches a Netscape cookie line whether it still has tabs, single
# spaces, or NO separator at all between the path ("/...") and the
# TRUE/FALSE secure flag (which is what happens when a phone keyboard
# eats a tab character while pasting).
COOKIE_LINE_RE = re.compile(
    r'^(?P<domain>\S+)\s+'
    r'(?P<flag1>TRUE|FALSE)\s+'
    r'(?P<path>/\S*?)\s*(?=TRUE|FALSE)'
    r'(?P<secure>TRUE|FALSE)\s+'
    r'(?P<expiry>\d+)\s+'
    r'(?P<name>\S+)\s+'
    r'(?P<value>.*)$'
)


def normalize_cookie_text(raw_bytes: bytes) -> bytes:
    """Rebuild proper TAB-separated Netscape cookie lines even if the
    tabs got mangled by copy/paste (mobile keyboards often collapse or
    drop TAB characters when text is pasted into a message box)."""
    text = raw_bytes.decode('utf-8', errors='ignore')
    fixed_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            fixed_lines.append(line)
            continue
        m = COOKIE_LINE_RE.match(stripped)
        if m:
            fixed_lines.append('\t'.join([
                m.group('domain'), m.group('flag1'), m.group('path'),
                m.group('secure'), m.group('expiry'),
                m.group('name'), m.group('value')
            ]))
        else:
            # Couldn't confidently fix it — keep as-is so we don't
            # silently destroy content the parser could still handle.
            fixed_lines.append(line)
    return ('\n'.join(fixed_lines) + '\n').encode('utf-8')


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
        telebot.types.KeyboardButton(STOP_BTN)
    )
    return markup


def get_broadcast_menu():
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(telebot.types.KeyboardButton(BROADCAST_CANCEL_BTN))
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


@bot.message_handler(commands=['broadcast'])
def start_broadcast(message):
    if message.chat.id != ADMIN_ID:
        bot.reply_to(message, "ပိုင်ရှင်ရှိတယ်")
        return
    awaiting_broadcast[str(message.chat.id)] = True
    bot.reply_to(
        message,
        "📢 Broadcast ပို့ချင်တဲ့ စာသားကို ရိုက်ပို့ပါ။\nမလုပ်တော့ဘူးဆိုရင် အောက်က ခလုတ်ကို နှိပ်ပါ။",
        reply_markup=get_broadcast_menu()
    )


@bot.message_handler(func=lambda message: message.text == BROADCAST_CANCEL_BTN)
def cancel_broadcast(message):
    if message.chat.id != ADMIN_ID:
        return
    awaiting_broadcast[str(message.chat.id)] = False
    bot.reply_to(message, "❌ Broadcast ကို ပယ်ဖျက်လိုက်ပါပြီ။", reply_markup=get_main_menu())


@bot.message_handler(func=lambda message: message.text == "/start 🔄")
def refresh_bot(message):
    send_welcome(message)


@bot.message_handler(func=lambda message: message.text == STOP_BTN)
def stop_process(message):
    user_id = str(message.chat.id)
    proc = running_process.get(user_id)
    if proc and proc.poll() is None:
        stop_flags[user_id] = True
        proc.terminate()
        bot.reply_to(message, "⏹ မလုပ်ပေးတော့ဘူးကွာ", reply_markup=get_main_menu())
    else:
        bot.reply_to(message, "ဘာပို့ထားလို့ ရပ်ခိုင်းနေတာလဲဟ", reply_markup=get_main_menu())


def run_generator_task(chat_id, user_id, content_bytes, progress_msg_id=None):
    """Shared logic for both document uploads and plain-text cookie submissions."""
    acquired = file_lock.acquire(timeout=90)
    if not acquired:
        bot.send_message(chat_id, "ငါအလုပ်များနေပါတယ်ဟ၊ ခဏနေမှ ထပ်ကြိုးစားပေး", reply_markup=get_main_menu())
        return

    # nf-token-generator.py always reads a fixed file called "input.txt"
    # in its own working directory.
    input_path = "input.txt"

    try:
        if stop_flags.get(user_id):
            if progress_msg_id:
                bot.edit_message_text(chat_id=chat_id, message_id=progress_msg_id, text="⏹ မလုပ်ပေးတော့ဘူးကွာ")
            return

        if progress_msg_id:
            bot.edit_message_text(chat_id=chat_id, message_id=progress_msg_id, text="TXT ရပြီ Token ပြန်ပေးမယ် စောင့်နေ.")

        fixed_content = normalize_cookie_text(content_bytes)
        with open(input_path, "wb") as f:
            f.write(fixed_content)

        if stop_flags.get(user_id):
            bot.send_message(chat_id, "⏹ မလုပ်ပေးတော့ဘူးကွ", reply_markup=get_main_menu())
            return

        proc = subprocess.Popen(
            [sys.executable, 'nf-token-generator.py'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        running_process[user_id] = proc

        try:
            stdout, stderr = proc.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            bot.send_message(chat_id, "⏱️ ကြာလွန်းလို့ ရပ်လိုက်ပြီ ထပ်ကြိုးစားပေးကွာ", reply_markup=get_main_menu())
            return
        finally:
            running_process.pop(user_id, None)

        if stop_flags.get(user_id):
            bot.send_message(chat_id, "⏹ မလုပ်ပေးတော့ဘူးကွ", reply_markup=get_main_menu())
            return

        match = re.search(r'(https://netflix\.com/\?nftoken=[^\s]+)', stdout or "")
        if match:
            clean_url = match.group(1)
            reply = (
                f"ရပြီဝေ့:\n\n{clean_url}\n\n"
                "⚠️ **သတိထား** - ဒီလင့်ခ်က 15 minutes လောက်ပဲရမှာနော်"
            )
            bot.send_message(chat_id, reply, parse_mode='Markdown', reply_markup=get_main_menu())
        else:
            err_snippet = (stderr or "Cookie ပျက်နေတာထင်တယ် နောက်တစ်ခုစမ်းကွာ")[:500]
            bot.send_message(chat_id, "Token မတွေ့ဘူး နောက်တစ်ခုစမ်း", reply_markup=get_main_menu())
            bot.send_message(ADMIN_ID, f"⚠️ Token မတွေ့ဘူး (user {user_id}):\n```\n{err_snippet}\n```", parse_mode="Markdown")

    except Exception as e:
        bot.send_message(chat_id, f"Error တက်ကုန်ပြီဟ: {e}", reply_markup=get_main_menu())
    finally:
        running_process.pop(user_id, None)
        if os.path.exists(input_path):
            os.remove(input_path)
        file_lock.release()


@bot.message_handler(content_types=['document'])
def process_document(message):
    log_user(message)
    user_id = str(message.chat.id)
    stop_flags[user_id] = False
    file_name = message.document.file_name.lower()

    if not file_name.endswith('.txt'):
        bot.reply_to(message, ".txt ဖိုင်ပဲပို့ဟ", reply_markup=get_main_menu())
        return

    progress_msg = bot.reply_to(message, "ဖိုင်ငါရပြီ - အစဉ်လိုက်ပဲသွားမယ်ကွ(Queue)...")

    def task():
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        run_generator_task(message.chat.id, user_id, downloaded_file, progress_msg.message_id)

    Thread(target=task).start()


# --- Handles plain text: broadcast content (admin) OR cookie text (normal user) ---
@bot.message_handler(func=lambda message: message.content_type == 'text' and not message.text.startswith('/')
                      and message.text not in ["/start 🔄", STOP_BTN, BROADCAST_CANCEL_BTN])
def handle_text(message):
    log_user(message)
    chat_id = message.chat.id
    user_id = str(chat_id)

    # --- Admin sending a broadcast message ---
    if chat_id == ADMIN_ID and awaiting_broadcast.get(user_id):
        awaiting_broadcast[user_id] = False
        broadcast_text = message.text
        sent, failed = 0, 0
        for uid in active_users.keys():
            try:
                bot.send_message(int(uid), broadcast_text)
                sent += 1
            except Exception:
                failed += 1
        bot.send_message(
            chat_id,
            f"📢 Broadcast ပို့ပြီးပါပြီ။\n✅ အောင်မြင်: {sent}\n❌ မအောင်မြင်: {failed}",
            reply_markup=get_main_menu()
        )
        return

    # --- Normal user: treat pasted text as the cookie content ---
    stop_flags[user_id] = False
    progress_msg = bot.reply_to(message, "စာသားရပြီ အစဉ်လိုက်ပဲသွားမယ်ကွ(Queue)...")

    def task():
        run_generator_task(chat_id, user_id, message.text.encode('utf-8'), progress_msg.message_id)

    Thread(target=task).start()


if __name__ == "__main__":
    Thread(target=run_web).start()
    print("Bot စတင် အလုပ်လုပ်နေပါပြီ (Queue စနစ်ဖြင့်)...")
    bot.infinity_polling()

