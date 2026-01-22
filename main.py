import os
import json
import time
import requests
import threading
from flask import Flask
from bs4 import BeautifulSoup
import firebase_admin
from firebase_admin import credentials, firestore
from google.genai import types
from google import genai

app = Flask(__name__)

# --- 初始化 Firebase ---
def init_firebase():
    if not firebase_admin._apps:
        # 從環境變數讀取 JSON 字串
        fb_config = os.environ.get("FIREBASE_CONFIG_JSON")
        if not fb_config:
            print("❌ 錯誤：找不到 FIREBASE_CONFIG_JSON 環境變數")
            return None
        cred_dict = json.loads(fb_config)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
    return firestore.client()

def get_dynamic_pdf_url():
    headers = {"User-Agent": "Mozilla/5.0"}
    base_url = "[https://selcrs.nsysu.edu.tw/](https://selcrs.nsysu.edu.tw/)"
    try:
        res = requests.get(base_url, headers=headers, timeout=10)
        res.encoding = 'utf-8'
        soup = BeautifulSoup(res.text, 'html.parser')
        
        next_link = ""
        for a in soup.find_all('a', href=True):
            if "選課須知" in a.get_text():
                next_link = a['href']
                break
        
        if not next_link:
            next_link = "[https://oaa.nsysu.edu.tw/p/405-1003-20388,c2935.php?Lang=zh-tw](https://oaa.nsysu.edu.tw/p/405-1003-20388,c2935.php?Lang=zh-tw)"
        
        res = requests.get(next_link, headers=headers, timeout=10)
        res.encoding = 'utf-8'
        soup = BeautifulSoup(res.text, 'html.parser')
        
        for a in soup.find_all('a', href=True):
            if ".pdf" in a['href'].lower() and "選課須知" in a.get_text():
                pdf_url = a['href']
                return "[https://oaa.nsysu.edu.tw](https://oaa.nsysu.edu.tw)" + pdf_url if pdf_url.startswith('/') else pdf_url
        return None
    except Exception as e:
        print(f"爬蟲錯誤: {e}")
        return None

def process_and_save():
    print("🚀 開始執行自動化流程...")
    
    # 1. 抓取與下載
    pdf_url = get_dynamic_pdf_url()
    if not pdf_url: return
    
    pdf_filename = "/tmp/latest_course_info.pdf" # Render 建議存放在 /tmp
    response = requests.get(pdf_url)
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


    # 3. 解析 JSON 並寫入 Firebase
    # 3. 解析 JSON 並寫入 Firebase
    try:
        raw_text = response.text.strip()
        clean_json = raw_text.replace("```json", "").replace("```", "").strip()
        data_dict = json.loads(clean_json)
        
        # 檢查資料完整性 (檢查 key 的數量或特定 key 是否存在)
        # 假設你定義的項目共有 16 項
        required_count = 10 # 你可以根據實際需求設定門檻
        if len(data_dict) >= required_count:
            db = init_firebase()
            if db:
                # 取得集合路徑：CourseSelectionDate
                # 這裡使用固定 ID 'current_info' 進行覆寫，達到「刪除舊的、寫入最新」的效果
                doc_ref = db.collection("CourseSelectionDate").document("latest")
                
                # 直接使用 set 會覆蓋掉該文件原本的所有內容
                doc_ref.set({
                    "data": data_dict,
                    "source_url": pdf_url,
                    "metadata": {
                        "update_time": firestore.SERVER_TIMESTAMP,
                        "item_count": len(data_dict),
                        "status": "complete"
                    }
                })
                print(f"✅ 資料完整（共 {len(data_dict)} 項），已更新至 Firebase")
        else:
            print(f"⚠️ 資料不完整（僅抓到 {len(data_dict)} 項），取消寫入以保護舊資料")
            
    except Exception as e:
        print(f"❌ 發生錯誤: {e}")

@app.route('/')
def index():
    return "Course Scraper is online. Use /run to trigger."

@app.route('/run')
def run_scraper():
    # 使用 Thread 避免 Web 請求逾時
    thread = threading.Thread(target=process_and_save)
    thread.start()
    return "Task Started!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)



