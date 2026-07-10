import os
import re
import uuid
import json
from curl_cffi import requests

INPUT_FILE = "input.txt"

def extract_etp_rt(text):
    val = None
    try:
        cookies = json.loads(text)
        if isinstance(cookies, list):
            for cookie in cookies:
                if cookie.get("name") == "etp_rt":
                    val = cookie.get("value")
    except json.JSONDecodeError:
        pass 

    if not val:
        for line in text.splitlines():
            if "etp_rt" in line:
                parts = line.strip().split("\t")
                if len(parts) >= 7 and parts[5] == "etp_rt":
                    val = parts[6]

    if not val:
        match = re.search(r'etp_rt=([^;,\s]+)', text)
        if match:
            val = match.group(1)
            
        match_json = re.search(r'"name"\s*:\s*"etp_rt"\s*,\s*"value"\s*:\s*"([^"]+)"', text)
        if match_json:
            val = match_json.group(1)

    # Space တွေပါလာရင် ဖယ်ရှားပေးရန် .strip() ထည့်ထားပါသည်
    return val.strip() if val else None

def fetch_cr_token(etp_rt_value):
    # Method 1: Web API + etp_rt_cookie (မူလနည်းလမ်း)
    url_web = "https://www.crunchyroll.com/auth/v1/token"
    headers_web = {
        "Authorization": "Basic Y3Jfd2ViOg==",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data_web = {"grant_type": "etp_rt_cookie"}
    cookies_web = {"etp_rt": etp_rt_value}

    r1 = requests.post(url_web, headers=headers_web, data=data_web, cookies=cookies_web, impersonate="chrome")
    if r1.status_code == 200:
        return r1.json()

    # Method 2: Mobile API + refresh_token (etp_rt ကို Refresh token အဖြစ် တိုက်ရိုက်သုံးနည်း)
    url_beta = "https://beta-api.crunchyroll.com/auth/v1/token"
    data_beta = {
        "grant_type": "refresh_token",
        "refresh_token": etp_rt_value
    }
    r2 = requests.post(url_beta, headers=headers_web, data=data_beta, impersonate="chrome")
    if r2.status_code == 200:
        return r2.json()

    # Method 3: Nintendo Switch Client + refresh_token (အကြမ်းခံဆုံး နည်းလမ်း)
    headers_switch = {
        "Authorization": "Basic bm9haWhkZXZtXzZpeWcwYThsMHE6",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data_switch = {
        "grant_type": "refresh_token",
        "refresh_token": etp_rt_value,
        "device_id": str(uuid.uuid4()),
        "device_type": "Nintendo Switch"
    }
    r3 = requests.post(url_beta, headers=headers_switch, data=data_switch, impersonate="chrome")
    if r3.status_code == 200:
        return r3.json()

    # နည်းလမ်း (၃) ခုလုံး အလုပ်မလုပ်ပါက Error ပြန်ထုတ်ပေးမည်
    raise Exception(f"\nM1 Error: {r1.status_code} - {r1.text}\nM2 Error: {r2.status_code} - {r2.text}")

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
            print(f"Token: {access_token}") 
            print("====================")
        else:
            print("Error: Token ထုတ်ယူလို့ မရပါဘူး။ Response:", token_data)
            
    except Exception as e:
        print(f"Error ဖြစ်သွားပါတယ် ခင်ဗျာ: {e}")

if __name__ == "__main__":
    main()
