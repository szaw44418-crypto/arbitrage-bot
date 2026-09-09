import os
import time
import math
import requests
from threading import Thread
from flask import Flask
import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException

# 1. Flask Dummy Web Server (Render Free Web Service အတွက်)
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Spot DCA Cycle Bot is running live!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# 2. Credentials & Configuration
SPOT_BASE = "https://testnet.binance.vision"
SPOT_API_KEY = os.environ.get("SPOT_API_KEY", "EGMDZzNYcF8aHKsKGxWurbK63sLFdKA42cDEZC3zd8IPkyD3JDEH7btCt4D34aWV")
SPOT_SECRET_KEY = os.environ.get("SPOT_SECRET_KEY", "YfGOumNKz4MMbZ9MBy7aMB3R6CWxSjVljJvreup8k3BGL5pi1pqc73ieCpOghM8R")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8652275832:AAGxdVX66q7tQP_v3kNVAyslSYD3FsAWz60").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6127362073").strip()

client = Client(SPOT_API_KEY, SPOT_SECRET_KEY, testnet=True)
client.API_URL = f"{SPOT_BASE}/api"

SYMBOL = "ETHUSDT"
TOTAL_CAPITAL = 100.0  # စုစုပေါင်း ရန်ပုံငွေ ($100)
DCA_STEPS = 3          # DCA အလွှာ ၃ ဆင့်ခွဲဝေမည်
CAPITAL_PER_STEP = TOTAL_CAPITAL / DCA_STEPS
PROFIT_TARGET_PCT = 0.015  # အမြတ် ၁.၅% တင်မည်

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

def get_usdt_balance():
    try:
        account = client.get_account()
        for b in account['balances']:
            if b['asset'] == 'USDT':
                return float(b['free'])
    except Exception as e:
        print(f"Balance Check Error: {e}")
    return 0.0

# 3. Technical Analysis (RSI Calculation)
def calculate_rsi(symbol, interval=Client.KLINE_INTERVAL_15MINUTE, period=14):
    try:
        klines = client.get_klines(symbol=symbol, interval=interval, limit=50)
        if not klines or len(klines) < period + 5:
            return 50.0
        df = pd.DataFrame(klines, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'quote_asset_volume', 'num_trades', 'taker_buy_base', 'taker_buy_quote', 'ignore'])
        df['close'] = df['close'].astype(float)
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        latest_rsi = rsi.iloc[-1]
        return 50.0 if pd.isna(latest_rsi) else latest_rsi
    except Exception as e:
        print(f"RSI Error: {e}")
        return 50.0

# 4. Main DCA Cycle Logic Bot
def run_dca_cycle_bot():
    start_msg = f"🚀 *Spot DCA Cycle Bot Started* for `{SYMBOL}` with `${TOTAL_CAPITAL}` Capital..."
    print(start_msg)
    send_telegram(start_msg)
    
    while True:
        try:
            rsi = calculate_rsi(SYMBOL)
            print(f"Current {SYMBOL} RSI (15M): {rsi:.2f}")
            
            # အခြေအနေ (၁): ဈေးကွက်စစ်ဆေးခြင်း (Sideways သို့မဟုတ် Support ကျချိန် RSI < 45)
            if rsi <= 45:
                print("📉 RSI is low. Starting DCA Cycle 1 (Base Buy)...")
                
                initial_balance = get_usdt_balance()
                ticker = client.get_symbol_ticker(symbol=SYMBOL)
                current_price = float(ticker['price'])
                
                # Step 1: ပထမအလွှာ ဝယ်ယူခြင်း (Base Buy)
                buy_qty = format_quantity(SYMBOL, CAPITAL_PER_STEP / current_price)
                order1 = client.create_order(
                    symbol=SYMBOL, side='BUY', type='MARKET', quantity=buy_qty, recvWindow=60000
                )
                executed_price_1 = float(order1.get('fills', [{}])[0].get('price', current_price))
                total_coins = float(order1['executedQty'])
                total_cost = total_coins * executed_price_1
                
                msg = f"🟢 *DCA Step 1 Executed*: Bought `{total_coins}` ETH at `{executed_price_1}`"
                print(msg)
                send_telegram(msg)
                
                # Step 2: DCA 2nd Layer (ဈေး ၁% ထပ်ကျပါက ဒုတိယအလွှာ ထပ်ဝယ်မည်)
                dca_target_price = executed_price_1 * 0.99
                dca_filled = False
                
                start_time = time.time()
                while time.time() - start_time < 3600:  # ၁ နာရီအတွင်း စောင့်မည်
                    curr_ticker = float(client.get_symbol_ticker(symbol=SYMBOL)['price'])
                    if curr_ticker <= dca_target_price:
                        buy_qty_2 = format_quantity(SYMBOL, CAPITAL_PER_STEP / curr_ticker)
                        order2 = client.create_order(
                            symbol=SYMBOL, side='BUY', type='MARKET', quantity=buy_qty_2, recvWindow=60000
                        )
                        coins_2 = float(order2['executedQty'])
                        price_2 = float(order2.get('fills', [{}])[0].get('price', curr_ticker))
                        
                        total_coins += coins_2
                        total_cost += (coins_2 * price_2)
                        executed_price_1 = total_cost / total_coins  # Average Price အသစ်
                        
                        dca_msg = f"🟡 *DCA Step 2 (Dip Buy) Executed*! New Avg Price: `{executed_price_1:.2f}`"
                        print(dca_msg)
                        send_telegram(dca_msg)
                        dca_filled = True
                        break
                    time.sleep(15)
                
                # Step 3: အမြတ်တင်ပြီး ပြန်ရောင်းချခြင်း (Take Profit Sell Limit)
                target_sell_price = format_price(SYMBOL, executed_price_1 * (1 + PROFIT_TARGET_PCT))
                sell_msg = f"🎯 *Placing Take-Profit Sell Order* at `{target_sell_price}` (Target +1.5%)"
                print(sell_msg)
                send_telegram(sell_msg)
                
                # Sell Order တင်ပြီး ပြီးဆုံးသည်အထိ စောင့်ဆိုင်းခြင်း
                sell_order = client.create_order(
                    symbol=SYMBOL, side='SELL', type='LIMIT', timeInForce='GTC',
                    quantity=format_quantity(SYMBOL, total_coins), price=str(target_sell_price), recvWindow=60000
                )
                
                order_id = sell_order['orderId']
                while True:
                    check_order = client.get_order(symbol=SYMBOL, orderId=order_id)
                    if check_order['status'] == 'FILLED':
                        break
                    time.sleep(10)
                
                # Step 4: Balance Check & PnL Report ထုတ်ခြင်း
                new_balance = get_usdt_balance()
                net_profit = new_balance - initial_balance
                
                report = (
                    f"✅ *Cycle Finished Successfully!*\n"
                    f"💰 Initial Balance: `{initial_balance:.2f} USDT`\n"
                    f"💵 New Balance: `{new_balance:.2f} USDT`\n"
                    f"📈 Net Profit: `+{net_profit:.2f} USDT`\n"
                    f"-----------------------------------"
                )
                print(report)
                send_telegram(report)
                
            else:
                print("⏳ Waiting for favorable RSI (<= 45) to start new DCA cycle...")
                
        except Exception as e:
            print(f"Cycle Error: {e}")
            
        time.sleep(300)

if __name__ == "__main__":
    bot_thread = Thread(target=run_ta_grid_bot if 'run_ta_grid_bot' in globals() else run_dca_cycle_bot)
    bot_thread.daemon = True
    bot_thread.start()
    run_web()
