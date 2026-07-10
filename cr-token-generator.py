import os
import re
import requests
import uuid
import json

INPUT_FILE = "input.txt"
# Crunchyroll App ရဲ့ PUBLIC_TOKEN
PUBLIC_TOKEN = "d2piMV90YThta3Y3X2t4aHF6djc6MnlSWlg0Y0psX28yMzRqa2FNaXRTbXNLUVlGaUpQXzU="

def extract_etp_rt(text):
    """
    input.txt ထဲကနေ etp_rt ဆိုတဲ့ cookie တန်ဖိုးကို ရှာထုတ်မည့်အပိုင်း
    """
    # 1. JSON Format ဖြင့် လာလျှင်
    try:
        cookies = json.loads(text)
        if isinstance(cookies, list):
            for cookie in cookies:
                if cookie.get("name") == "etp_rt":
                    return cookie.get("value")
    except json.JSONDecodeError:
        pass 

    # 2. Netscape Format ဖြင့် လာလျှင်
    for line in text.splitlines():
        if "etp_rt" in line:
            parts = line.strip().split("\t")
            if len(parts) >= 7 and parts[5] == "etp_rt":
                return parts[6]

    # 3. သာမန် String / Regex ဖြင့် ရှာရန်
    match = re.search(r'etp_rt=([^;,\s]+)', text)
    if match:
        return match.group(1)
        
    match_json = re.search(r'"name"\s*:\s*"etp_rt"\s*,\s*"value"\s*:\s*"([^"]+)"', text)
    if match_json:
        return match_json.group(1)

    return None

def fetch_cr_token(etp_rt_value):
    """
    etp_rt cookie ကိုသုံးပြီး Web API မှတဆင့် Token လှမ်းတောင်းမည်
    """
    # ⚠️ အဓိက ပြင်ဆင်လိုက်သော နေရာ (URL ပြောင်းလိုက်ပါသည်)
    url = "https://www.crunchyroll.com/auth/v1/token"
    
    headers = {
        "Authorization": f"Basic {PUBLIC_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": f"etp_rt={etp_rt_value}" 
    }
    
    # ⚠️ Device Type ကို Web Browser ပုံစံ ပြောင်းလိုက်ပါသည်
    data = {
        "grant_type": "etp_rt_cookie",
        "device_id": str(uuid.uuid4()), 
        "device_name": "Chrome on Windows",
        "device_type": "Web Desktop"
    }

    response = requests.post(url, headers=headers, data=data)
    
    if response.status_code != 200:
        raise Exception(f"API Error - {response.status_code}: {response.text}")
        
    return response.json()

def main():
    if not os.path.exists(INPUT_FILE):
        print("input.txt မတွေ့ပါဘူး ခင်ဗျာ။")
        return

    with open(INPUT_FILE, "r", encoding="utf-8", errors="ignore") as f:
        raw_cookie = f.read().strip()

    etp_rt_value = extract_etp_rt(raw_cookie)
    
    if not etp_rt_value:
        print("Error: etp_rt cookie ကို ရှာမတွေ့ပါဘူး ခင်ဗျာ။ ဖိုင် Format မှားနေနိုင်ပါသည်။")
        return

    try:
        token_data = fetch_cr_token(etp_rt_value)
        access_token = token_data.get("access_token")
        
        if access_token:
            print("Access Token ရရှိပါပြီ:")
            print("====================")
            # Bot.py ကနေ ဖမ်းယူမည့် Token နေရာ
            print(f"Token: {access_token}") 
            print("====================")
        else:
            print("Error: Token ထုတ်ယူလို့ မရပါဘူး။ Response:", token_data)
            
    except Exception as e:
        print(f"Error ဖြစ်သွားပါတယ် ခင်ဗျာ: {e}")

if __name__ == "__main__":
    main()
