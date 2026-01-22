# 使用 Python 3.11 slim 版本作為基礎
FROM python:3.11-slim

# 更新套件庫並安裝必要的工具
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    gnupg \
    unzip \
    ca-certificates \
    --no-install-recommends

# 安裝 Google Chrome (使用最新的穩定版本安裝方式)
RUN wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/googlechrome-linux-keyring.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/googlechrome-linux-keyring.gpg] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# 設定工作目錄
WORKDIR /app

# 複製依賴文件並安裝
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製所有程式碼
COPY . .

# 設定環境變數確保 Chrome 能在 Headless 模式下正常運作
ENV PYTHONUNBUFFERED=1

# 啟動命令 (Render 環境通常使用 10000 埠)
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "main:app"]
