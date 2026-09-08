import os
import math
import time
import threading
import requests
from flask import Flask
from binance.client import Client
from binance.exceptions import BinanceAPIException

# ---------------------------------------------------------
# 1. Render Free Web Service အတွက် Flask Server
# ---------------------------------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Binance Spot Arbitrage Bot is active and running!"

# ---------------------------------------------------------
# 2. Binance & Telegram Credentials Configuration
# ---------------------------------------------------------
SPOT_BASE = "https://testnet.binance.vision"
FUTURES_BASE = "https://demo-fapi.binance.com"

SPOT_API_KEY = os.environ.get("SPOT_API_KEY", "EGMDZzNYcF8aHKsKGxWurbK63sLFdKA42cDEZC3zd8IPkyD3JDEH7btCt4D34aWV")
SPOT_SECRET_KEY = os.environ.get("SPOT_SECRET_KEY", "YfGOumNKz4MMbZ9MBy7aMB3R6CWxSjVljJvreup8k3BGL5pi1pqc73ieCpOghM8R")

FUTURES_API_KEY = os.environ.get("FUTURES_API_KEY", "TGSwnTW3ukJ7z8fXKeZd4Iz6MBttW6bRA2ODX5rwXC90YWsv5srgcwcL7Bl8XQeA")
FUTURES_SECRET_KEY = os.environ.get("FUTURES_SECRET_KEY", "b64gEodONh8DMFPsX7Kaj1QRhGdgRM8iCYy8gVPVAO8VNAzWL88DmvZhrVE330Ed")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8652275832:AAGxdVX66q7tQP_v3kNVAyslSYD3FsAWz60").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6127362073").strip()

# Binance Client Setup (Spot Testnet)
client = Client(SPOT_API_KEY, SPOT_SECRET_KEY, testnet=True)
client.API_URL = f"{SPOT_BASE}/api"

# ---------------------------------------------------------
# 3. Telegram Helper Function
# ---------------------------------------------------------
def send_telegram(message):
    """Telegram သို့ အကြောင်းကြားစာ ပို့ပေးသည့် Function"""
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
            print(f"Telegram Sending Error: {e}")

# ---------------------------------------------------------
# 4. LOT_SIZE Precision Helper Functions (-1013 Error ပြင်ဆင်ရန်)
# ---------------------------------------------------------
symbol_info_cache = {}

def get_symbol_filter(symbol, filter_type):
    if symbol not in symbol_info_cache:
        try:
            symbol_info_cache[symbol] = client.get_symbol_info(symbol)
        except Exception as e:
            print(f"Error fetching info for {symbol}: {e}")
            return None
            
    info = symbol_info_cache.get(symbol)
    if info:
        for f in info['filters']:
            if f['filterType'] == filter_type:
                return f
    return None

def format_quantity(symbol, quantity):
    """Binance LOT_SIZE stepSize အလိုက် ပမာဏကို တိကျစွာ Round ဖြတ်ပေးသည်"""
    lot_size_filter = get_symbol_filter(symbol, 'LOT_SIZE')
    if not lot_size_filter:
        return quantity

    step_size = float(lot_size_filter['stepSize'])
    if step_size == 0:
        return quantity

    precision = int(round(-math.log10(step_size)))
    if precision <= 0:
        return float(int(quantity))
    
    factor = 10 ** precision
    return math.floor(quantity * factor) / factor

# ---------------------------------------------------------
# 5. Triangular Arbitrage Core Logic
# ---------------------------------------------------------
def run_arbitrage_bot():
    print("Starting Binance Spot Arbitrage Bot...")
    send_telegram("🚀 *Binance Arbitrage Bot (Testnet) စတင်လည်ပတ်နေပါပြီ။*")
    
    MIN_PROFIT_THRESHOLD = 0.1  # အမြတ် ၁ ခေါက်လျှင် အနည်းဆုံး 0.1 USDT ရမှ အလုပ်လုပ်မည်

    while True:
        try:
            usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])
            print(f"\nCurrent USDT Balance: {usdt_balance:.2f}")

            if usdt_balance < 10:
                print("USDT Balance နည်းလွန်းသဖြင့် ခဏစောင့်ဆိုင်းနေပါသည်...")
                time.sleep(10)
                continue

            triangles = [
                {'base': 'BTCUSDT', 'cross': 'BNBBTC', 'exit': 'BNBUSDT', 'coin1': 'BTC', 'coin2': 'BNB'},
                {'base': 'BTCUSDT', 'cross': 'ETHBTC', 'exit': 'ETHUSDT', 'coin1': 'BTC', 'coin2': 'ETH'},
                {'base': 'BTCUSDT', 'cross': 'XRPBTC', 'exit': 'XRPUSDT', 'coin1': 'BTC', 'coin2': 'XRP'},
            ]

            for t in triangles:
                try:
                    ticker_base = float(client.get_symbol_ticker(symbol=t['base'])['price'])
                    ticker_cross = float(client.get_symbol_ticker(symbol=t['cross'])['price'])
                    ticker_exit = float(client.get_symbol_ticker(symbol=t['exit'])['price'])

                    # Leg 1, 2, 3 Trade Quantity များ တွက်ချက်ခြင်း
                    raw_q1 = usdt_balance / ticker_base
                    q1 = format_quantity(t['base'], raw_q1)

                    raw_q2 = q1 / ticker_cross
                    q2 = format_quantity(t['cross'], raw_q2)

                    estimated_usdt_back = q2 * ticker_exit
                    potential_profit = estimated_usdt_back - usdt_balance

                    print(f"Verifying: USDT -> {t['coin1']} -> {t['coin2']}")

                    if potential_profit > MIN_PROFIT_THRESHOLD:
                        alert_msg = f"⚡ *Arbitrage Opportunity Found!*\nExpected Profit: `{potential_profit:.4f} USDT`"
                        print(alert_msg)
                        send_telegram(alert_msg)

                        # Leg 1: BUY Base (BTCUSDT)
                        order1 = client.create_order(symbol=t['base'], side='BUY', type='MARKET', quantity=q1)
                        print(f"[Leg 1] BUY {t['base']} Volume: {q1}")

                        # Leg 2: BUY Cross (BNBBTC)
                        executed_coin1 = float(client.get_asset_balance(asset=t['coin1'])['free'])
                        q2_formatted = format_quantity(t['cross'], executed_coin1 / ticker_cross)
                        order2 = client.create_order(symbol=t['cross'], side='BUY', type='MARKET', quantity=q2_formatted)
                        print(f"[Leg 2] BUY {t['cross']} Volume: {q2_formatted}")

                        # Leg 3: SELL Exit (BNBUSDT)
                        executed_coin2 = float(client.get_asset_balance(asset=t['coin2'])['free'])
                        q3_formatted = format_quantity(t['exit'], executed_coin2)
                        order3 = client.create_order(symbol=t['exit'], side='SELL', type='MARKET', quantity=q3_formatted)
                        print(f"[Leg 3] SELL {t['exit']} Volume: {q3_formatted}")

                        success_msg = f"✅ *Arbitrage Executed Successfully!*\nPair: USDT -> {t['coin1']} -> {t['coin2']}\nProfit: `{potential_profit:.4f} USDT`"
                        send_telegram(success_msg)

                    else:
                        print(f"No arbitrage opportunity: {potential_profit:.4f} USDT")

                except BinanceAPIException as e:
                    err_msg = f"⚠️ *Binance Trade Error:* `{e.message}`"
                    print(err_msg)
                    send_telegram(err_msg)
                
                time.sleep(1)

        except Exception as e:
            print(f"Unexpected Loop Error: {e}")

        time.sleep(5)

# ---------------------------------------------------------
# 6. Render Web Service & Background Bot Execution
# ---------------------------------------------------------
if __name__ == "__main__":
    # Bot ကို Thread အဖြစ် Background တွင် ပတ်ခိုင်းထားသည်
    bot_thread = threading.Thread(target=run_arbitrage_bot)
    bot_thread.daemon = True
    bot_thread.start()

    # Render မှ Assign လုပ်ပေးမည့် Web Port ကို ပွင့်စေသည်
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
