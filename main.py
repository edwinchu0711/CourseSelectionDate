import os
import json
import time
import requests
import threading
import urllib3
from flask import Flask
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore
from google.genai import types
from google import genai
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from datetime import datetime, timedelta, timezone

# 關閉 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

# --- 初始化 Firebase ---
def init_firebase():
    if not firebase_admin._apps:
        fb_config = os.environ.get("FIREBASE_CONFIG_JSON", "").strip()
        if not fb_config:
            print("❌ 錯誤：找不到 FIREBASE_CONFIG_JSON")
            return None
        try:
            cred_dict = json.loads(fb_config)
            cred = credentials.Certificate(cred_dict)
            firebase_admin.initialize_app(cred)
        except Exception as e:
            print(f"❌ Firebase 初始化失敗: {e}")
            return None
    return firestore.client()

def get_dynamic_pdf_url():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--ignore-certificate-errors")
    
    # 在 Docker 環境中，Chrome 路徑通常是固定的
    driver = webdriver.Chrome(options=chrome_options)
    
    base_url = "https://selcrs.nsysu.edu.tw/"
    try:
        print(f"正在訪問: {base_url}")
        driver.get(base_url)
        wait = WebDriverWait(driver, 15)
        link_element = wait.until(EC.presence_of_element_located((By.PARTIAL_LINK_TEXT, "選課須知")))
        next_url = link_element.get_attribute("href")
        
        print(f"跳轉至: {next_url}")
        driver.get(next_url)
        time.sleep(5) 
        
        pdf_links = driver.find_elements(By.TAG_NAME, "a")
        for link in pdf_links:
            href = link.get_attribute("href")
            text = link.text
            if href and ".pdf" in href.lower() and "選課須知" in text:
                print(f"✅ 找到 PDF: {href}")
                return href
        return None
    except Exception as e:
        print(f"❌ Selenium 錯誤: {e}")
        return None
    finally:
        driver.quit()

def process_and_save():
    print("🚀 開始執行自動化流程...")
    
    # 1. 抓取與下載
    pdf_url = get_dynamic_pdf_url()
    if not pdf_url: return
    
    pdf_filename = "/tmp/latest_course_info.pdf" # Render 建議存放在 /tmp
    response = requests.get(pdf_url ,verify=False)
    with open(pdf_filename, "wb") as f:
        f.write(response.content)

    # 2. AI 處理
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
    uploaded_file = client.files.upload(file=pdf_filename)
    
    while uploaded_file.state.name == "PROCESSING":
        time.sleep(5)
        uploaded_file = client.files.get(name=uploaded_file.name)

    prompt = """
    請閱讀這份選課須知 PDF，提取出以下項目的具體時間（包含日期與時段），並嚴格以 JSON 格式回傳。
    如果文件中有多個時段（例如不同年級），請一併列出。
    
    需求項目：
    課程查詢、初選一、初選一公佈、初選二、初選二公佈、加退選一、加退選一公佈、加退選二、加退選二公佈、
    異常處理、超修單列印、棄選時間、選課確認、必修課程確認、系所輔導學生選課、超修學分申請。(不要加其他的)
    "課程查詢"的這個標題前面可以保留學年度，例如"110-1 課程查詢"
    然後每一項就都有開始時間，結束時間，若是只有其中一個那就是開始時間有，然後結束時間就空白
    以下為範例
    {
      "114-2 課程查詢": {
        "開始時間": "115年1/6(二) 13:00",
        "結束時間": "" 
      },
      "必修課程確認": {
        "開始時間": "1/30(五) 09:00",
        "結束時間": "2/25(三) 17:00"
      },
      "初選一": {
        "開始時間": "1/30(五) 09:00",
        "結束時間": "2/2(一) 17:00"
      }}
      不需要其他資訊，就這樣簡單就好，每個項目裡面就只有時間，不要有其他子元素
      並且最後再加上一個"更新時間":{
      (現在的日期和時間(UTC +8))
      }
    """

    response = client.models.generate_content(
        model="gemini-flash-lite-latest",
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_uri(file_uri=uploaded_file.uri, mime_type=uploaded_file.mime_type),
                    types.Part.from_text(text=prompt),
                ],
            ),
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
        )
    )


    try:
        raw_text = response.text.strip()
        data_dict = json.loads(raw_text)
        # 加上來源資訊
        result = {
            "data": data_dict,
            "source_url": pdf_url,
            "update_time": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
        }
        return result
    except:
        return None

@app.route('/test')
def index():
    return "Course Scraper is online. Use /run to trigger."

@app.route('/run')
def run_scraper():
    # 移除 threading，改為直接執行並取得結果
    try:
        data = process_and_save() 
        if data:
            return json.dumps(data, ensure_ascii=False), 200, {'Content-Type': 'application/json'}
        else:
            return "Failed to extract data", 500
    except Exception as e:
        return str(e), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)






