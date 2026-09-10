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
    return "🤖 10-Coin Scalping DCA Bot is running live!"

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

# ထိပ်တန်းကွိုင် ၁၀ မျိုး (တားမြစ်ထားသော XRP, DOGE, PEPE, SUI, AVAX, LINK, TRX များ မပါဝင်ပါ)
COINS = [
    "LTCUSDT", "BCHUSDT", "ETCUSDT", "NEARUSDT", 
    "ATOMUSDT", "FTMUSDT", "ARBUSDT", "OPUSDT", 
    "FILUSDT", "ICPUSDT"
]

TOTAL_CAPITAL = 100.0  
CAPITAL_PER_ORDER = 10.0  # Binance Min Notional $10 ပြည့်ရန် တစ်ကြိမ်လျှင် $10 သုံးမည်
PROFIT_TARGET_PCT = 0.01  # အမြတ် ၁%
STOP_LOSS_PCT = 0.04      # ၄% ကျပါက အရှုံးခံထွက်ရန်

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

def get_usdt_balance():
    try:
        acc = client.get_account()
        for b in acc['balances']:
            if b['asset'] == 'USDT': return float(b['free'])
    except Exception: pass
    return 0.0

def check_market_conditions(symbol):
    try:
        klines_15m = client.get_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_15MINUTE, limit=20)
        if not klines_15m or len(klines_15m) < 15: return 50.0
        df_15m = pd.DataFrame(klines_15m, columns=['t','open','high','low','close','v','ct','qav','nt','tb','tq','ig'])
        df_15m['close'] = df_15m['close'].astype(float)
        delta = df_15m['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rsi = 100 - (100 / (1 + (gain / loss)))
        latest_rsi = rsi.iloc[-1]
        return (50.0 if pd.isna(latest_rsi) else latest_rsi)
    except Exception:
        return 50.0

def coin_trade_worker(symbol):
    print(f"🔄 Worker started for {symbol}...")
    while True:
        try:
            rsi = check_market_conditions(symbol)
            print(f"[{symbol}] RSI (15M): {rsi:.2f}")
            
            # RSI ၅၅ အောက်ရောက်ပါက အမြန်ဝယ်ယူရန်
            if rsi <= 55:
                initial_balance = get_usdt_balance()
                curr_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
                buy_qty = format_quantity(symbol, CAPITAL_PER_ORDER / curr_price)
                
                order = client.create_order(symbol=symbol, side='BUY', type='MARKET', quantity=buy_qty, recvWindow=60000)
                exec_price = float(order.get('fills', [{}])[0].get('price', curr_price))
                total_coins = float(order['executedQty'])
                
                send_telegram(f"🟢 *Scalp Buy ({symbol})*: Bought at `{exec_price}`")
                
                target_sell = format_price(symbol, exec_price * (1 + PROFIT_TARGET_PCT))
                stop_loss_price = format_price(symbol, exec_price * (1 - STOP_LOSS_PCT))
                
                sell_order = client.create_order(
                    symbol=symbol, side='SELL', type='LIMIT', timeInForce='GTC',
                    quantity=format_quantity(symbol, total_coins), price=str(target_sell), recvWindow=60000
                )
                
                order_id = sell_order['orderId']
                while True:
                    chk = client.get_order(symbol=symbol, orderId=order_id)
                    if chk['status'] == 'FILLED':
                        break
                    live_p = float(client.get_symbol_ticker(symbol=symbol)['price'])
                    if live_p <= stop_loss_price:
                        client.cancel_order(symbol=symbol, orderId=order_id)
                        client.create_order(symbol=symbol, side='SELL', type='MARKET', quantity=format_quantity(symbol, total_coins), recvWindow=60000)
                        send_telegram(f"🚨 *Stop-Loss Hit ({symbol})* at `{live_p}`")
                        break
                    time.sleep(10)
                
                new_balance = get_usdt_balance()
                net_profit = new_balance - initial_balance
                send_telegram(f"✅ *Cycle Done ({symbol})* | Profit: `{net_profit:+.2f} USDT`")
        except Exception as e:
            print(f"Error in worker {symbol}: {e}")
        
        time.sleep(25)

def run_concurrent_bots():
    msg = f"🚀 *10-Coin Scalping Bot Started* (Coins: {len(COINS)} selected)"
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
