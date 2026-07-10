import os
import re
import requests
import uuid

INPUT_FILE = "input.txt"
# Crunchyroll App ရဲ့ PUBLIC_TOKEN
PUBLIC_TOKEN = "d2piMV90YThta3Y3X2t4aHF6djc6MnlSWlg0Y0psX28yMzRqa2FNaXRTbXNLUVlGaUpQXzU="

def extract_etp_rt(text):
    """
    input.txt ထဲကနေ etp_rt ဆိုတဲ့ cookie တန်ဖိုးကို ရှာထုတ်မယ့် function
    """
    match = re.search(r'etp_rt=([^;,\s]+)', text)
    if match:
        return match.group(1)
    
    # Netscape format (Tab-separated) နဲ့လာရင် ရှာဖို့
    for line in text.splitlines():
        parts = line.strip().split("\t")
        if len(parts) >= 7 and parts[5] == "etp_rt":
            return parts[6]
            
    return None

def fetch_cr_token(etp_rt_value):
    """
    etp_rt cookie ကိုသုံးပြီး Crunchyroll API ကနေ Access Token လှမ်းတောင်းမယ့် function
    """
    url = "https://beta-api.crunchyroll.com/auth/v1/token"
    
    headers = {
        "Authorization": f"Basic {PUBLIC_TOKEN}",
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Crunchyroll/3.59.0 Android/13 okhttp/4.12.0",
        "Cookie": f"etp_rt={etp_rt_value}" # Cookie ကို Header မှာ ထည့်ပေးရပါတယ်
    }
    
    data = {
        "grant_type": "etp_rt_cookie", # Cookie ကနေ Token တောင်းတဲ့ Type ပါ
        "device_id": str(uuid.uuid4()), # Random ID တစ်ခုထုတ်ပေးလိုက်တာပါ
        "device_name": "RMX2170",
        "device_type": "realme RMX2170"
    }

    response = requests.post(url, headers=headers, data=data)
    response.raise_for_status() # Error တက်ရင် ရပ်သွားအောင်လို့ပါ
    
    return response.json()

def main():
    if not os.path.exists(INPUT_FILE):
        print("input.txt မတွေ့ပါဘူး ခင်ဗျာ။")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        raw_cookie = f.read().strip()

    etp_rt_value = extract_etp_rt(raw_cookie)
    
    if not etp_rt_value:
        print("etp_rt cookie ကို ရှာမတွေ့ပါဘူး ခင်ဗျာ။ Valid ဖြစ်တဲ့ Crunchyroll cookie ကို ထည့်ပေးပါ။")
        return

    try:
        token_data = fetch_cr_token(etp_rt_value)
        access_token = token_data.get("access_token")
        
        if access_token:
            print("Access Token ရရှိပါပြီ:")
            print("====================")
            # Bot.py ထဲက Regex နဲ့ ဖမ်းဖို့အတွက် "Token: " ဆိုပြီး ထုတ်ပေးတာပါ
            print(f"Token: {access_token}") 
            print("====================")
        else:
            print("Token ထုတ်ယူလို့ မရပါဘူး။ Response:", token_data)
            
    except Exception as e:
        print(f"Error ဖြစ်သွားပါတယ် ခင်ဗျာ: {e}")

if __name__ == "__main__":
    main()
