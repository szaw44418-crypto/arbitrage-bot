import os
import time
import math
import requests
from threading import Thread
from flask import Flask
import pandas as pd
from binance.client import Client

# 1. Flask Dummy Web Server (Render Free Web Service)
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Fast Scalping Multi-Coin DCA Bot is running live!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# 2. Credentials & Configuration
SPOT_BASE = "https://testnet.binance.vision"
SPOT_API_KEY = os.environ.get("SPOT_API_KEY", "EGMDZzNYcF8aHKsKGxWurbK63sLFdKA42cDEZC3zd8IPkyD3JDEH7btCt4D34aWV")
SPOT_SECRET_KEY = os.environ.get("SPOT_SECRET_KEY", "YfGOumNKz4MMbZ9MBy7aMB3R6CWxSjVljJvreup8k3BGL5pi1pqc73ieCpOghM8R")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

client = Client(SPOT_API_KEY, SPOT_SECRET_KEY, testnet=True)
client.API_URL = f"{SPOT_BASE}/api"

COINS = ["ETHUSDT", "BTCUSDT", "BNBUSDT", "SOLUSDT", "ADAUSDT"]
TOTAL_CAPITAL = 100.0  
DCA_STEPS = 5          
CAPITAL_PER_STEP = TOTAL_CAPITAL / DCA_STEPS
PROFIT_TARGET_PCT = 0.01  # အမြတ် ၁% သို့ လျှော့ချထားသည် (အမြန်ရောင်းထွက်ရန်)
STOP_LOSS_PCT = 0.05      # ၅% ကျပါက အရှုံးခံထွက်ရန်

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

# 3. Flexible RSI Check (Trend စစ်ဆေးမှုကို ဖြုတ်ပြီး RSI ၅၅ အောက်ဆိုလျှင် ဝယ်မည်)
def check_market_conditions(symbol):
    try:
        klines_15m = client.get_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_15MINUTE, limit=30)
        if not klines_15m or len(klines_15m) < 15: return 50.0
        df_15m = pd.DataFrame(klines_15m, columns=['t','open','high','low','close','v','ct','qav','nt','tb','tq','ig'])
        df_15m['close'] = df_15m['close'].astype(float)
        delta = df_15m['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rsi = 100 - (100 / (1 + (gain / loss)))
        latest_rsi = rsi.iloc[-1]
        
        return (50.0 if pd.isna(latest_rsi) else latest_rsi)
    except Exception as e:
        print(f"Analysis Error for {symbol}: {e}")
        return 50.0

# 4. Fast Scalping DCA Bot Logic
def run_fast_dca_bot():
    msg = f"🚀 *Fast Scalping DCA Bot Started* (Scanning: {', '.join(COINS)})"
    print(msg)
    send_telegram(msg)
    
    while True:
        target_symbol = None
        
        for symbol in COINS:
            try:
                rsi = check_market_conditions(symbol)
                print(f"[{symbol}] RSI (15M): {rsi:.2f}")
                
                # Trend စစ်ဆေးမှုကို ဖြုတ်လိုက်ပြီး RSI 55 အောက် (သို့မဟုတ် ညှိလိုရ) ရှိပါက ချက်ချင်းဝင်မည်
                if rsi <= 55:
                    target_symbol = symbol
                    print(f"🎯 Target Acquired: {symbol} triggered entry (RSI: {rsi:.2f})!")
                    break
            except Exception as e:
                print(f"Error checking {symbol}: {e}")
            time.sleep(1)
            
        if target_symbol:
            try:
                print(f"📉 Starting Scalping DCA Cycle for {target_symbol}...")
                initial_balance = get_usdt_balance()
                
                curr_price = float(client.get_symbol_ticker(symbol=target_symbol)['price'])
                buy_qty = format_quantity(target_symbol, CAPITAL_PER_STEP / curr_price)
                order = client.create_order(symbol=target_symbol, side='BUY', type='MARKET', quantity=buy_qty, recvWindow=60000)
                
                exec_price = float(order.get('fills', [{}])[0].get('price', curr_price))
                total_coins = float(order['executedQty'])
                total_cost = total_coins * exec_price
                
                send_telegram(f"🟢 *DCA Step 1 ({target_symbol})*: Bought `{total_coins}` at `{exec_price}`")
                
                dca_drops = [0.01, 0.02, 0.03, 0.04] # ဈေးကျနှုန်းကို ၁%, ၂%, ၃%, ၄% သို့ အမြန်ဝယ်နိုင်ရန် လျှော့ချထားသည်
                step_count = 1
                
                while step_count < DCA_STEPS:
                    target_dip_price = exec_price * (1 - dca_drops[step_count - 1])
                    timeout = time.time() + 900 # စောင့်ဆိုင်းချိန်ကို မိနစ် ၁၅ ပုံစံသို့ လျှော့ချသည်
                    
                    bought_next = False
                    while time.time() < timeout:
                        live_price = float(client.get_symbol_ticker(symbol=target_symbol)['price'])
                        
                        avg_price_check = total_cost / total_coins
                        if live_price <= avg_price_check * (1 - STOP_LOSS_PCT):
                            send_telegram(f"🚨 *Stop-Loss Triggered for {target_symbol}!* Cutting losses at `{live_price}`")
                            client.create_order(symbol=target_symbol, side='SELL', type='MARKET', quantity=format_quantity(symbol=target_symbol, qty=total_coins), recvWindow=60000)
                            bought_next = True
                            break
                        
                        if live_price <= target_dip_price:
                            q2 = format_quantity(target_symbol, CAPITAL_PER_STEP / live_price)
                            o2 = client.create_order(symbol=target_symbol, side='BUY', type='MARKET', quantity=q2, recvWindow=60000)
                            c2 = float(o2['executedQty'])
                            p2 = float(o2.get('fills', [{}])[0].get('price', live_price))
                            
                            total_coins += c2
                            total_cost += (c2 * p2)
                            exec_price = total_cost / total_coins
                            step_count += 1
                            
                            send_telegram(f"🟡 *DCA Step {step_count} ({target_symbol}) Executed*! New Avg: `{exec_price:.2f}`")
                            bought_next = True
                            break
                        time.sleep(5)
                    if bought_next and step_count == DCA_STEPS: break
                    if not bought_next: break
                
                final_avg_price = total_cost / total_coins
                target_sell = format_price(target_symbol, final_avg_price * (1 + PROFIT_TARGET_PCT))
                stop_loss_price = format_price(target_symbol, final_avg_price * (1 - STOP_LOSS_PCT))
                
                send_telegram(f"🎯 *Placing TP Sell for {target_symbol}* at `{target_sell}` (Avg: `{final_avg_price:.2f}`)")
                
                sell_order = client.create_order(
                    symbol=target_symbol, side='SELL', type='LIMIT', timeInForce='GTC',
                    quantity=format_quantity(target_symbol, total_coins), price=str(target_sell), recvWindow=60000
                )
                
                order_id = sell_order['orderId']
                while True:
                    chk = client.get_order(symbol=target_symbol, orderId=order_id)
                    if chk['status'] == 'FILLED':
                        break
                    live_p = float(client.get_symbol_ticker(symbol=target_symbol)['price'])
                    if live_p <= stop_loss_price:
                        client.cancel_order(symbol=target_symbol, orderId=order_id)
                        client.create_order(symbol=target_symbol, side='SELL', type='MARKET', quantity=format_quantity(target_symbol, total_coins), recvWindow=60000)
                        send_telegram(f"🚨 *Emergency Stop-Loss Hit* for {target_symbol} at `{live_p}`")
                        break
                    time.sleep(10)
                
                new_balance = get_usdt_balance()
                net_profit = new_balance - initial_balance
                send_telegram(
                    f"✅ *Cycle Completed ({target_symbol})!*\n"
                    f"💰 Initial: `{initial_balance:.2f} USDT`\n"
                    f"💵 New: `{new_balance:.2f} USDT`\n"
                    f"📈 Profit/Loss: `{net_profit:+.2f} USDT`\n"
                    f"-----------------------------------"
                )
            except Exception as e:
                print(f"Cycle Execution Error: {e}")
        else:
            print("⏳ Scanning coins...")
            
        time.sleep(15)

if __name__ == "__main__":
    t = Thread(target=run_fast_dca_bot)
    t.daemon = True
    t.start()
    run_web()
