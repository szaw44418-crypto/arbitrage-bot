import os
import time
import math
import requests
from threading import Thread
from flask import Flask
from decimal import Decimal
import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException

# 1. Flask Dummy Web Server (Render Free Web Service အတွက် 24 နာရီ အိပ်မသွားအောင် ထိန်းရန်)
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 TA-based ETH Spot Grid Bot is running live!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# 2. Credentials & Configuration
SPOT_BASE = "https://testnet.binance.vision"
FUTURES_BASE = "https://demo-fapi.binance.com"

SPOT_API_KEY = os.environ.get("SPOT_API_KEY", "EGMDZzNYcF8aHKsKGxWurbK63sLFdKA42cDEZC3zd8IPkyD3JDEH7btCt4D34aWV")
SPOT_SECRET_KEY = os.environ.get("SPOT_SECRET_KEY", "YfGOumNKz4MMbZ9MBy7aMB3R6CWxSjVljJvreup8k3BGL5pi1pqc73ieCpOghM8R")

FUTURES_API_KEY = os.environ.get("FUTURES_API_KEY", "TGSwnTW3ukJ7z8fXKeZd4Iz6MBttW6bRA2ODX5rwXC90YWsv5srgcwcL7Bl8XQeA")
FUTURES_SECRET_KEY = os.environ.get("FUTURES_SECRET_KEY", "b64gEodONh8DMFPsX7Kaj1QRhGdgRM8iCYy8gVPVAO8VNAzWL88DmvZhrVE330Ed")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8652275832:AAGxdVX66q7tQP_v3kNVAyslSYD3FsAWz60").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6127362073").strip()

client = Client(SPOT_API_KEY, SPOT_SECRET_KEY, testnet=True)
client.API_URL = f"{SPOT_BASE}/api"

SYMBOL = "ETHUSDT"
TOTAL_CAPITAL = 100.0  # $100 Fixed Budget
GRID_COUNT = 5         # Grid အကွက်ရေ ၅ ခု (တစ်ကွက်လျှင် $20)
CAPITAL_PER_GRID = TOTAL_CAPITAL / GRID_COUNT

symbol_info_cache = {}

def send_telegram(message):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown"
            }
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            print(f"Telegram Error: {e}")

def get_symbol_filter(symbol, filter_type):
    if symbol not in symbol_info_cache:
        try:
            info = client.get_symbol_info(symbol)
            if info:
                symbol_info_cache[symbol] = info
        except Exception as e:
            print(f"Error fetching symbol info: {e}")
            return None
    info = symbol_info_cache.get(symbol)
    if info:
        for f in info['filters']:
            if f['filterType'] == filter_type:
                return f
    return None

def format_price(symbol, price):
    price_filter = get_symbol_filter(symbol, 'PRICE_FILTER')
    if not price_filter:
        return round(price, 2)
    tick_size = float(price_filter['tickSize'])
    precision = int(round(-math.log10(tick_size)))
    return round(round(price / tick_size) * tick_size, precision)

def format_quantity(symbol, quantity):
    lot_filter = get_symbol_filter(symbol, 'LOT_SIZE')
    if not lot_filter:
        return round(quantity, 5)
    step_size = float(lot_filter['stepSize'])
    precision = int(round(-math.log10(step_size)))
    return round(round(quantity / step_size) * step_size, precision)

# 3. Technical Analysis (RSI Calculation - Fixed with 15m Interval & NaN Handling)
def calculate_rsi(symbol, interval=Client.KLINE_INTERVAL_15MINUTE, period=14):
    try:
        klines = client.get_klines(symbol=symbol, interval=interval, limit=50)
        if not klines or len(klines) < period + 5:
            print("⚠️ Not enough klines data from Testnet. Using default Sideways RSI.")
            return 50.0  # Data မလုံလောက်ပါက Sideways (50) သတ်မှတ်ပေးမည်
            
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'num_trades', 'taker_buy_base', 'taker_buy_quote', 'ignore'])
        df['close'] = df['close'].astype(float)
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        latest_rsi = rsi.iloc[-1]
        if pd.isna(latest_rsi):
            return 50.0
        return latest_rsi
    except Exception as e:
        print(f"RSI Calculation Error: {e}")
        return 50.0

# 4. Grid Level Calculation & Order Placement
def setup_grid_orders(symbol):
    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        current_price = float(ticker['price'])
        
        lower_price = current_price * 0.97
        upper_price = current_price * 1.03
        
        price_step = (upper_price - lower_price) / (GRID_COUNT - 1)
        log_msg = f"📊 *ETH Grid Setup:* Range `{lower_price:.2f}` to `{upper_price:.2f}` (Current: `{current_price:.2f}`)"
        print(log_msg)
        send_telegram(log_msg)
        
        for i in range(GRID_COUNT):
            grid_price = lower_price + (i * price_step)
            formatted_price = format_price(symbol, grid_price)
            
            if formatted_price < current_price:
                coin_qty = CAPITAL_PER_GRID / formatted_price
                formatted_qty = format_quantity(symbol, coin_qty)
                
                try:
                    order = client.create_order(
                        symbol=symbol,
                        side='BUY',
                        type='LIMIT',
                        timeInForce='GTC',
                        quantity=formatted_qty,
                        price=str(formatted_price),
                        recvWindow=60000
                    )
                    success_msg = f"✅ Placed ETH Grid BUY Order at `{formatted_price}` (Qty: `{formatted_qty}`)"
                    print(success_msg)
                    send_telegram(success_msg)
                except BinanceAPIException as e:
                    print(f"❌ Order Error at price {formatted_price}: {e.message}")
            
            time.sleep(0.2)
            
    except Exception as e:
        print(f"Grid Setup Error: {e}")

# 5. Main Bot Loop with TA Filter
def run_ta_grid_bot():
    start_msg = f"🚀 *TA-based ETH Spot Grid Bot Started* with `${TOTAL_CAPITAL}` Capital..."
    print(start_msg)
    send_telegram(start_msg)
    
    while True:
        current_rsi = calculate_rsi(SYMBOL)
        
        if current_rsi is not None:
            print(f"Current {SYMBOL} RSI (15M): {current_rsi:.2f}")
            
            if 40 <= current_rsi <= 60:
                print("🟢 Market is Sideways. Initializing ETH Grid Strategy...")
                setup_grid_orders(SYMBOL)
                time.sleep(1800)
            else:
                print("⏳ Market is trending. Waiting for Sideways condition...")
        
        time.sleep(600)

if __name__ == "__main__":
    # Bot ကို Background Thread ထဲမှာ အလုပ်လုပ်ခိုင်းမည်
    bot_thread = Thread(target=run_ta_grid_bot)
    bot_thread.daemon = True
    bot_thread.start()
    
    # Main Thread မှာ Flask Web Server ကို ဖွင့်ထားမည် (Render Free Web Service အတွက်)
    run_web()
