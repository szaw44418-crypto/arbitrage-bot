import os
import time
import math
import requests
from threading import Thread
from flask import Flask
import pandas as pd
from binance.client import Client

app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Isolated Per-Coin P&L Scalping Bot is running live!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

SPOT_BASE = "https://testnet.binance.vision"
SPOT_API_KEY = os.environ.get("SPOT_API_KEY", "EGMDZzNYcF8aHKsKGxWurbK63sLFdKA42cDEZC3zd8IPkyD3JDEH7btCt4D34aWV")
SPOT_SECRET_KEY = os.environ.get("SPOT_SECRET_KEY", "YfGOumNKz4MMbZ9MBy7aMB3R6CWxSjVljJvreup8k3BGL5pi1pqc73ieCpOghM8R")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

client = Client(SPOT_API_KEY, SPOT_SECRET_KEY, testnet=True)
client.API_URL = f"{SPOT_BASE}/api"

COINS = [
    "LTCUSDT", "BCHUSDT", "ETCUSDT", "NEARUSDT", 
    "ATOMUSDT", "SHIBUSDT", "ARBUSDT", "OPUSDT", 
    "FILUSDT", "ICPUSDT"
]

CAPITAL_PER_ORDER = 10.0  
PROFIT_TARGET_PCT = 0.01   # အမြတ် ၁%
STOP_LOSS_PCT = 0.025      # အရှုံး ၂.၅%

symbol_info_cache = {}

def send_telegram(message):
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            print(f"Telegram Error: {e}")

def get_symbol_filter(symbol, filter_type):
    if symbol not in symbol_info_cache:
        try:
            info = client.get_symbol_info(symbol)
            if info: symbol_info_cache[symbol] = info
        except Exception: return None
    info = symbol_info_cache.get(symbol)
    if info:
        for f in info['filters']:
            if f['filterType'] == filter_type: return f
    return None

def format_price(symbol, price):
    pf = get_symbol_filter(symbol, 'PRICE_FILTER')
    if not pf: return round(price, 2)
    tick = float(pf['tickSize'])
    return round(round(price / tick) * tick, int(round(-math.log10(tick))))

def format_quantity(symbol, qty):
    lf = get_symbol_filter(symbol, 'LOT_SIZE')
    if not lf: return round(qty, 5)
    step = float(lf['stepSize'])
    return round(round(qty / step) * step, int(round(-math.log10(step))))

def check_market_conditions(symbol):
    try:
        klines = client.get_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_15MINUTE, limit=60)
        if not klines or len(klines) < 50: return False
        
        df = pd.DataFrame(klines, columns=['t','open','high','low','close','v','ct','qav','nt','tb','tq','ig'])
        df['close'] = df['close'].astype(float)
        
        df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
        is_above_ema = df['close'].iloc[-1] > df['EMA50'].iloc[-1]
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rsi = 100 - (100 / (1 + (gain / loss)))
        
        prev_rsi = rsi.iloc[-2]
        curr_rsi = rsi.iloc[-1]
        
        is_rebound = (prev_rsi < 38) and (curr_rsi > prev_rsi) and (curr_rsi < 50)
        
        return is_above_ema and is_rebound
    except Exception as e:
        print(f"Condition check error [{symbol}]: {e}")
        return False

def coin_trade_worker(symbol):
    print(f"🔄 Isolated P&L Worker started for {symbol}...")
    while True:
        try:
            should_buy = check_market_conditions(symbol)
            
            if should_buy:
                curr_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
                buy_qty = format_quantity(symbol, CAPITAL_PER_ORDER / curr_price)
                
                order = client.create_order(symbol=symbol, side='BUY', type='MARKET', quantity=buy_qty, recvWindow=60000)
                exec_price = float(order.get('fills', [{}])[0].get('price', curr_price))
                total_coins = float(order['executedQty'])
                total_cost = total_coins * exec_price
                
                send_telegram(f"🟢 *[{symbol}] Buy Executed*\n• Price: `{exec_price}`\n• Cost: `{total_cost:.2f} USDT`")
                
                target_sell = format_price(symbol, exec_price * (1 + PROFIT_TARGET_PCT))
                stop_loss_price = format_price(symbol, exec_price * (1 - STOP_LOSS_PCT))
                
                sell_order = client.create_order(
                    symbol=symbol, side='SELL', type='LIMIT', timeInForce='GTC',
                    quantity=format_quantity(symbol, total_coins), price=str(target_sell), recvWindow=60000
                )
                
                order_id = sell_order['orderId']
                trade_successful = False
                exit_price = exec_price
                
                while True:
                    chk = client.get_order(symbol=symbol, orderId=order_id)
                    if chk['status'] == 'FILLED':
                        trade_successful = True
                        exit_price = target_sell
                        break
                    
                    live_p = float(client.get_symbol_ticker(symbol=symbol)['price'])
                    if live_p <= stop_loss_price:
                        client.cancel_order(symbol=symbol, orderId=order_id)
                        client.create_order(symbol=symbol, side='SELL', type='MARKET', quantity=format_quantity(symbol, total_coins), recvWindow=60000)
                        exit_price = live_p
                        send_telegram(f"🚨 *[{symbol}] Stop-Loss Triggered*\n• Exit Price: `{live_p}`")
                        break
                    time.sleep(10)
                
                total_revenue = total_coins * exit_price
                net_profit = total_revenue - total_cost
                
                # ကွိုင်တစ်ခုချင်းစီအတွက် သီးသန့် အမြတ်/အရှုံး တိကျရှင်းလင်းစွာ ပို့ပေးခြင်း
                if trade_successful:
                    send_telegram(f"✅ *[{symbol}] Cycle Completed (PROFIT)*\n• Net Profit: `+{net_profit:.2f} USDT`\n• Exit Price: `{exit_price}`")
                else:
                    send_telegram(f"❌ *[{symbol}] Cycle Stopped (LOSS)*\n• Net Loss: `{net_profit:.2f} USDT`\n• Exit Price: `{exit_price}`")
                    
        except Exception as e:
            print(f"Error in worker {symbol}: {e}")
        
        time.sleep(20)

def run_concurrent_bots():
    msg = f"🚀 *Isolated P&L Scalping Bot Started* (Coins: {len(COINS)})"
    print(msg)
    send_telegram(msg)
    
    threads = []
    for symbol in COINS:
        t = Thread(target=coin_trade_worker, args=(symbol,))
        t.daemon = True
        t.start()
        threads.append(t)
        time.sleep(1)
        
    for t in threads:
        t.join()

if __name__ == "__main__":
    bot_thread = Thread(target=run_concurrent_bots)
    bot_thread.daemon = True
    bot_thread.start()
    run_web()
