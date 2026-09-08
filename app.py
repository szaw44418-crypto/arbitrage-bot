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

SPOT_API_KEY = os.environ.get("SPOT_API_KEY", "EGMDZzNYcF8aHKsKGxWurbK63sLFdKA42cDEZC3zd8IPkyD3JDEH7btCt4D34aWV")
SPOT_SECRET_KEY = os.environ.get("SPOT_SECRET_KEY", "YfGOumNKz4MMbZ9MBy7aMB3R6CWxSjVljJvreup8k3BGL5pi1pqc73ieCpOghM8R")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8652275832:AAGxdVX66q7tQP_v3kNVAyslSYD3FsAWz60").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6127362073").strip()

# Binance Client Setup (Spot Testnet)
client = Client(SPOT_API_KEY, SPOT_SECRET_KEY, testnet=True)
client.API_URL = f"{SPOT_BASE}/api"

# ---------------------------------------------------------
# 3. Helper Functions (Telegram & Time Sync)
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

def sync_server_time():
    """Binance Server နှင့် Local Time ကွာဟချက်ကို ညှိပေးသည် (-1021 Error ကာကွယ်ရန်)"""
    try:
        server_time = client.get_server_time()
        local_time = int(time.time() * 1000)
        client.TIMESTAMP_OFFSET = server_time['serverTime'] - local_time
    except Exception as e:
        print(f"Time Sync Error: {e}")

# ---------------------------------------------------------
# 4. LOT_SIZE Precision Helper Function (-1013 Error ပြင်ဆင်ရန်)
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
    sync_server_time()
    send_telegram("🚀 *Binance Arbitrage Bot (Dynamic Capital Mode) စတင်လည်ပတ်နေပါပြီ။*")
    
    FEE_FACTOR = 0.999           # Binance Spot Fee 0.1% (Trade တိုင်းအတွက် 99.9% သာကျန်မည်)
    MIN_PROFIT_THRESHOLD = 0.02  # Net Fee နှုတ်ပြီး အနည်းဆုံး 0.02 USDT မြတ်မှ လုပ်မည်
    MIN_REQUIRED_USDT = 10.0     # Binance Minimum Order Limit (~10 USDT)

    while True:
        try:
            sync_server_time()
            total_usdt_balance = float(client.get_asset_balance(asset='USDT', recvWindow=60000)['free'])
            print(f"\nCurrent Total USDT Balance: {total_usdt_balance:.2f} USDT")

            # Binance Minimum Limit (10 USDT) အောက် နည်းပါက ခဏစောင့်မည်
            if total_usdt_balance < MIN_REQUIRED_USDT:
                print(f"USDT Balance မလုံလောက်ပါ။ လက်ရှိ: {total_usdt_balance:.2f} USDT (အနည်းဆုံး {MIN_REQUIRED_USDT} USDT လိုအပ်ပါသည်)")
                time.sleep(10)
                continue

            # Balance ၁၀၀ ထက်ကျော်ပါက ၁၀၀ အတိအကျ သုံးမည်၊ ၁၀၀ အောက်နည်းပါက ရှိသော Balance ၏ 95% ကို Capital အဖြစ် သုံးမည်
            TRADE_CAPITAL = min(100.0, total_usdt_balance * 0.95)
            print(f"Active Trade Capital: {TRADE_CAPITAL:.2f} USDT")

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

                    # Fee (0.1% x 3 legs) ပါ ထည့်သွင်းတွက်ချက်သော Net Profit Estimate
                    raw_q1 = TRADE_CAPITAL / ticker_base
                    q1 = format_quantity(t['base'], raw_q1)
                    q1_after_fee = q1 * FEE_FACTOR

                    raw_q2 = q1_after_fee / ticker_cross
                    q2 = format_quantity(t['cross'], raw_q2)
                    q2_after_fee = q2 * FEE_FACTOR

                    estimated_usdt_back = (q2_after_fee * ticker_exit) * FEE_FACTOR
                    potential_profit = estimated_usdt_back - TRADE_CAPITAL

                    print(f"Checking Path: USDT -> {t['coin1']} -> {t['coin2']} | Capital: {TRADE_CAPITAL:.2f} | Net Est. Profit: {potential_profit:.4f} USDT")

                    # အမြတ်ရှိပါက Trade စတင်မည်
                    if potential_profit > MIN_PROFIT_THRESHOLD:
                        start_time = time.time()
                        initial_usdt_balance = float(client.get_asset_balance(asset='USDT', recvWindow=60000)['free'])

                        print(f"⚡ Arbitrage Opportunity Found! Expected Net Profit: {potential_profit:.4f} USDT")

                        # Leg 1: BUY Base Coin
                        order1 = client.create_order(symbol=t['base'], side='BUY', type='MARKET', quantity=q1, recvWindow=60000)
                        print(f"[Leg 1] Executed BUY {t['base']} Qty: {q1}")

                        # Leg 2: BUY Cross Coin (အမှန်တကယ် လက်ကျန်ရှိသော Coin1 Balance ဖြင့် ဝယ်မည်)
                        time.sleep(0.2)
                        actual_coin1_bal = float(client.get_asset_balance(asset=t['coin1'], recvWindow=60000)['free'])
                        q2_formatted = format_quantity(t['cross'], actual_coin1_bal / ticker_cross)
                        order2 = client.create_order(symbol=t['cross'], side='BUY', type='MARKET', quantity=q2_formatted, recvWindow=60000)
                        print(f"[Leg 2] Executed BUY {t['cross']} Qty: {q2_formatted}")

                        # Leg 3: SELL Exit Coin (အမှန်တကယ် လက်ကျန်ရှိသော Coin2 Balance ကို ရောင်းမည်)
                        time.sleep(0.2)
                        actual_coin2_bal = float(client.get_asset_balance(asset=t['coin2'], recvWindow=60000)['free'])
                        q3_formatted = format_quantity(t['exit'], actual_coin2_bal)
                        order3 = client.create_order(symbol=t['exit'], side='SELL', type='MARKET', quantity=q3_formatted, recvWindow=60000)
                        print(f"[Leg 3] Executed SELL {t['exit']} Qty: {q3_formatted}")

                        # Cycle Summary & စာရင်းစစ်ခြင်း
                        time.sleep(0.5)
                        end_time = time.time()
                        final_usdt_balance = float(client.get_asset_balance(asset='USDT', recvWindow=60000)['free'])
                        
                        realized_profit = final_usdt_balance - initial_usdt_balance
                        profit_percentage = (realized_profit / TRADE_CAPITAL) * 100
                        duration = end_time - start_time

                        # Telegram Cycle Report
                        report_msg = (
                            f"📊 *Arbitrage Cycle Summary Report*\n"
                            f"----------------------------------\n"
                            f"🔄 *Trade Path:* `USDT ➔ {t['coin1']} ➔ {t['coin2']} ➔ USDT`\n"
                            f"💰 *Capital Used:* `{TRADE_CAPITAL:.2f} USDT`\n"
                            f"📈 *Expected Profit:* `+{potential_profit:.4f} USDT`\n"
                            f"💵 *Actual Net Profit:* `{realized_profit:+.4f} USDT` ({profit_percentage:+.2f}%)\n"
                            f"🏦 *New Total Balance:* `{final_usdt_balance:.2f} USDT`\n"
                            f"⏱ *Execution Time:* `{duration:.2f} seconds`\n"
                            f"----------------------------------\n"
                            f"✅ *Cycle Finished Successfully!*"
                        )
                        print(report_msg)
                        send_telegram(report_msg)

                    else:
                        print("No profitable opportunity found after fees.")

                except BinanceAPIException as e:
                    err_msg = f"⚠️ *Binance Trade Error:* `{e.message}`"
                    print(err_msg)
                    send_telegram(err_msg)
                
                time.sleep(1)

        except Exception as e:
            print(f"Unexpected Loop Error: {e}")

        time.sleep(3)

# ---------------------------------------------------------
# 6. Render Web Service & Background Bot Execution
# ---------------------------------------------------------
if __name__ == "__main__":
    bot_thread = threading.Thread(target=run_arbitrage_bot)
    bot_thread.daemon = True
    bot_thread.start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
