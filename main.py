import os
import json
import time
import requests
import gc  # 引入垃圾回收模組
import urllib3
from flask import Flask
import threading
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from datetime import datetime, timedelta, timezone
from google.genai import types
from google import genai

# 關閉 SSL 警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)

last_scraped_data = None

def get_dynamic_pdf_url():
    chrome_options = Options()
    # 基本無頭設定
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--ignore-certificate-errors")
    
    # --- 記憶體優化參數 ---
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-dev-tools")
    chrome_options.add_argument("--proxy-server='direct://'")
    chrome_options.add_argument("--proxy-bypass-list=*")
    # 禁用圖片載入是省記憶體的關鍵
    chrome_options.add_experimental_option("prefs", {"profile.managed_default_content_settings.images": 2})
    chrome_options.add_argument("--blink-settings=imagesEnabled=false")
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
        base_url = "https://selcrs.nsysu.edu.tw/"
        print(f"正在訪問: {base_url}")
        driver.get(base_url)
        
        wait = WebDriverWait(driver, 20)
        # 等待連結出現
        link_element = wait.until(EC.presence_of_element_located((By.PARTIAL_LINK_TEXT, "選課須知")))
        next_url = link_element.get_attribute("href")
        
        print(f"跳轉至: {next_url}")
        driver.get(next_url)
        
        # 稍微等待頁面加載文字
        time.sleep(3) 
        
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
        # 確保無論如何都會關閉瀏覽器釋放記憶體
        if 'driver' in locals():
            driver.quit()

def process_and_save():
    global last_scraped_data
    gc.collect()  # 主動呼叫垃圾回收，釋放記憶體
    print("🚀 開始執行自動化流程...")
    
    # 1. 抓取 PDF URL
    pdf_url = get_dynamic_pdf_url()
    if not pdf_url: 
        print("❌ 無法取得 PDF URL")
        return None
    
    # 2. 下載 PDF
    pdf_filename = "/tmp/latest_course_info.pdf"
    try:
        response = requests.get(pdf_url, verify=False, timeout=60)
        with open(pdf_filename, "wb") as f:
            f.write(response.content)
    except Exception as e:
        print(f"❌ PDF 下載失敗: {e}")
        return None

    # 3. AI 處理 (Gemini)
    try:
        client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
        uploaded_file = client.files.upload(file=pdf_filename)
        
        # 等待 AI 處理文件
        while uploaded_file.state.name == "PROCESSING":
            time.sleep(3)
            uploaded_file = client.files.get(name=uploaded_file.name)

        prompt = """
        請閱讀這份選課須知 PDF，提取出以下項目的具體時間（包含日期與時段），並嚴格以 JSON 格式回傳。
        如果文件中有多個時段（例如不同年級），請一併列出。
        
        需求項目：
        1.課程查詢
        2.初選一
        3.初選一公佈
        4.初選二
        5.初選二公佈
        6.加退選一
        7.加退選一公佈
        8.加退選二
        9.加退選二公佈
        10.異常處理
        11.選課確認
        12.棄選時間  
        13.必修課程確認
        14.系所輔導學生選課
        15.超修學分申請
        (就以上15個，不要其他的)
        "課程查詢"的這個標題前面可以保留學年度，例如"110-1 課程查詢"
        然後每一項就都有開始時間，結束時間，若是只有其中一個那就是開始時間有，然後結束時間就空白
        
        範例格式：
        {
          "114-2 課程查詢": { "開始時間": "115年1/6(二) 13:00", "結束時間": "" },
          "初選一": { "開始時間": "1/30(五) 09:00", "結束時間": "2/2(一) 17:00" }
        }(日期間不要有空白 只有星期後和時間前可以有一個空白)
        最後加上一個"更新時間"欄位，填入目前的日期時間。
        """

        response = client.models.generate_content(
            model="gemini-flash-lite-latest", # 使用最新快速模型
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_uri(file_uri=uploaded_file.uri, mime_type=uploaded_file.mime_type),
                        types.Part.from_text(text=prompt),
                    ],
                ),
            ],
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )

        data_dict = json.loads(response.text.strip())
        result = {
            "data": data_dict,
            "source_url": pdf_url,
            "update_time": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
        }
        last_scraped_data = result  # 將結果存入全域變數
        gc.collect()
        print("✅ 資料已存入暫存區")
        return result
    except Exception as e:
        print(f"❌ AI 處理失敗: {e}")
        return None

@app.route('/test')
def index():
    print("已啟動")
    return "Course Scraper is online. Use /run to trigger."

# @app.route('/run')
# def run_scraper():
#     try:
#         data = process_and_save() 
#         if data:
#             return json.dumps(data, ensure_ascii=False), 200, {'Content-Type': 'application/json'}
#         else:
#             return "Failed to extract data", 500
#     except Exception as e:
#         return str(e), 500


@app.route('/run')
def run_scraper():
    # 改回非同步：立刻回傳，讓爬蟲在背景跑
    threading.Thread(target=process_and_save).start()
    print("開始run")
    return "Task Started", 202
    
@app.route('/get_data')
def get_data():
    print("已要求資料")
    global last_scraped_data
    if last_scraped_data:
        print("已給資料")
        return json.dumps(last_scraped_data, ensure_ascii=False), 200, {'Content-Type': 'application/json'}
    print("未給資料")
    return "Data not ready yet", 404

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)







