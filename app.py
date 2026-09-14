import os
import time
import math
import requests
import datetime
import json
from threading import Thread
from flask import Flask
import pandas as pd
from binance.client import Client

app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Ultimate Bi-Directional Pullback Bot is running successfully!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

FUTURES_BASE = "https://testnet.binancefuture.com"
FUTURES_API_KEY = os.environ.get("FUTURES_API_KEY", "TGSwnTW3ukJ7z8fXKeZd4Iz6MBttW6bRA2ODX5rwXC90YWsv5srgcwcL7Bl8XQeA").strip()
FUTURES_SECRET_KEY = os.environ.get("FUTURES_SECRET_KEY", "b64gEodONh8DMFPsX7Kaj1QRhGdgRM8iCYy8gVPVAO8VNAzWL88DmvZhrVE330Ed").strip()

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8652275832:AAGxdVX66q7tQP_v3kNVAyslSYD3FsAWz60").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6127362073").strip()

client = Client(FUTURES_API_KEY, FUTURES_SECRET_KEY, testnet=True)
client.API_URL = f"{FUTURES_BASE}/fapi"

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "AVAXUSDT", "LINKUSDT"]

LEVERAGE = 5
TOTAL_MARGIN = 20.0  
DAILY_PROFIT_LIMIT = 5.0    
DAILY_LOSS_LIMIT = -2.0     

symbol_info_cache = {}
active_trades = {}  
DATA_FILE = "ultimate_pullback_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {"date": str(datetime.date.today()), "daily_pnl": 0.0, "history": []}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

def check_daily_limit():
    data = load_data()
    today_str = str(datetime.date.today())
    if data.get("date") != today_str:
        data["date"] = today_str
        data["daily_pnl"] = 0.0
        save_data(data)
        return False, 0.0
    
    pnl = data.get("daily_pnl", 0.0)
    if pnl >= DAILY_PROFIT_LIMIT or pnl <= DAILY_LOSS_LIMIT:
        return True, pnl
    return False, pnl

def update_daily_pnl(amount):
    data = load_data()
    today_str = str(datetime.date.today())
    if data.get("date") != today_str:
        data["date"] = today_str
        data["daily_pnl"] = 0.0
    data["daily_pnl"] = data.get("daily_pnl", 0.0) + amount
    data["history"].append({"timestamp": str(datetime.datetime.now()), "pnl": amount})
    save_data(data)

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
            info = client.futures_exchange_info()
            for s in info['symbols']:
                symbol_info_cache[s['symbol']] = s
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
    if not lf: return round(qty, 3)
    step = float(lf['stepSize'])
    return round(round(qty / step) * step, int(round(-math.log10(step))))

def close_all_positions(reason="Limit Hit"):
    try:
        for symbol in COINS:
            pos_info = client.futures_position_information(symbol=symbol)
            for pos in pos_info:
                amt = float(pos['positionAmt'])
                if amt != 0:
                    side = 'SELL' if amt > 0 else 'BUY'
                    client.futures_create_order(symbol=symbol, side=side, type='MARKET', quantity=abs(amt))
                    send_telegram(f"⚠️ *{symbol}* active position ကို အလိုအလျောက် ပိတ်လိုက်ပါပြီ။ အကြောင်းရင်း: *{reason}*")
            client.futures_cancel_all_open_orders(symbol=symbol)
    except Exception as e:
        print(f"Error during emergency close: {e}")

def check_signal(symbol):
    try:
        klines = client.futures_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_1HOUR, limit=250)
        if not klines or len(klines) < 210: return None, 0, 0
        
        df = pd.DataFrame(klines, columns=['t','open','high','low','close','v','ct','qav','nt','tb','tq','ig'])
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)
        
        df['EMA200'] = df['close'].ewm(span=200, adjust=False).mean()
        df['EMA10'] = df['close'].ewm(span=10, adjust=False).mean()
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(com=13, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(com=13, adjust=False).mean()
        df['RSI'] = 100 - (100 / (1 + (gain / loss)))
        
        c_close = df['close'].iloc[-2]
        c_open = df['open'].iloc[-2]
        p_close = df['close'].iloc[-3]
        p_open = df['open'].iloc[-3]
        
        curr_ema200 = df['EMA200'].iloc[-2]
        curr_ema10 = df['EMA10'].iloc[-2]
        curr_rsi = df['RSI'].iloc[-2]
        
        recent_swing_low = df['low'].iloc[-10:-1].min()
        recent_swing_high = df['high'].iloc[-10:-1].max()
        
        is_long_trend = c_close > curr_ema200
        is_long_pullback = (df['low'].iloc[-2] <= curr_ema10 or c_close <= curr_ema10)
        is_long_rsi = (30 <= curr_rsi <= 40)
        is_long_reversal = (p_close < p_open) and (c_close > c_open)
        
        if is_long_trend and is_long_pullback and is_long_rsi and is_long_reversal:
            return "LONG", recent_swing_low, curr_ema10
            
        is_short_trend = c_close < curr_ema200
        is_short_pullback = (df['high'].iloc[-2] >= curr_ema10 or c_close >= curr_ema10)
        is_short_rsi = (60 <= curr_rsi <= 70)
        is_short_reversal = (p_close > p_open) and (c_close < c_open)
        
        if is_short_trend and is_short_pullback and is_short_rsi and is_short_reversal:
            return "SHORT", recent_swing_high, curr_ema10
            
        return None, 0, 0
    except Exception as e:
        print(f"Signal check error [{symbol}]: {e}")
        return None, 0, 0

def monitor_trade_execution(symbol, side, exec_price, tp1_price, stop_loss_price, total_qty):
    tp_side = 'SELL' if side == 'LONG' else 'BUY'
    half_qty = format_quantity(symbol, total_qty / 2)
    tp1_hit = False
    
    try:
        tp1_order = client.futures_create_order(
            symbol=symbol, side=tp_side, type='LIMIT', timeInForce='GTC',
            quantity=half_qty, price=str(tp1_price), recvWindow=60000
        )
        tp1_order_id = tp1_order['orderId']
    except Exception as e:
        print(f"⚠️ Limit TP1 Order Error: {e}")
        active_trades[symbol] = False
        return

    print(f"📡 Monitoring {symbol} {side} position...")
    while True:
        try:
            is_stopped, current_pnl = check_daily_limit()
            if is_stopped:
                close_all_positions("Daily PnL Boundary Crossed inside Monitor")
                break

            ticker = client.futures_symbol_ticker(symbol=symbol)
            curr_price = float(ticker['price'])
            
            # --- LONG POSITION MONITORING ---
            if side == "LONG":
                if curr_price <= stop_loss_price:
                    client.futures_cancel_all_open_orders(symbol=symbol)
                    loss_amount = (stop_loss_price - exec_price) * total_qty
                    update_daily_pnl(loss_amount)
                    send_telegram(f"🛑 *{symbol} LONG SL Hit!* Price: `{curr_price}` | Realized Loss: `{round(loss_amount, 2)} USDT`")
                    break
                
                if not tp1_hit:
                    order_status = client.futures_get_order(symbol=symbol, orderId=tp1_order_id)
                    if order_status['status'] == 'FILLED':
                        tp1_hit = True
                        stop_loss_price = exec_price  
                        profit_tp1 = (tp1_price - exec_price) * half_qty
                        update_daily_pnl(profit_tp1)
                        send_telegram(f"🎯 *{symbol} LONG TP1 Hit!* Half closed at `{tp1_price}`. SL moved to Break-even `{exec_price}`")
                else:
                    klines = client.futures_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_1HOUR, limit=5)
                    last_candle_close = float(klines[-2][4])
                    df_check = pd.DataFrame(klines, columns=['t','open','high','low','close','v','ct','qav','nt','tb','tq','ig'])
                    ema10_curr = df_check['close'].astype(float).ewm(span=10, adjust=False).mean().iloc[-2]
                    
                    if last_candle_close < ema10_curr or curr_price <= exec_price:
                        client.futures_create_order(symbol=symbol, side=tp_side, type='MARKET', quantity=half_qty)
                        profit_tp2 = (curr_price - exec_price) * half_qty
                        update_daily_pnl(profit_tp2)
                        send_telegram(f"🏁 *{symbol} LONG TP2 / Exit Hit!* Remaining half closed at `{curr_price}`.")
                        break

            # --- SHORT POSITION MONITORING ---
            elif side == "SHORT":
                if curr_price >= stop_loss_price:
                    client.futures_cancel_all_open_orders(symbol=symbol)
                    loss_amount = (stop_loss_price - exec_price) * total_qty
                    update_daily_pnl(loss_amount)
                    send_telegram(f"🛑 *{symbol} SHORT SL Hit!* Price: `{curr_price}` | Realized Loss: `{round(loss_amount, 2)} USDT`")
                    break
                
                if not tp1_hit:
                    order_status = client.futures_get_order(symbol=symbol, orderId=tp1_order_id)
                    if order_status['status'] == 'FILLED':
                        tp1_hit = True
                        stop_loss_price = exec_price  
                        profit_tp1 = (exec_price - tp1_price) * half_qty
                        update_daily_pnl(profit_tp1)
                        send_telegram(f"🎯 *{symbol} SHORT TP1 Hit!* Half closed at `{tp1_price}`. SL moved to Break-even `{exec_price}`")
                else:
                    klines = client.futures_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_1HOUR, limit=5)
                    last_candle_close = float(klines[-2][4])
                    df_check = pd.DataFrame(klines, columns=['t','open','high','low','close','v','ct','qav','nt','tb','tq','ig'])
                    ema10_curr = df_check['close'].astype(float).ewm(span=10, adjust=False).mean().iloc[-2]
                    
                    if last_candle_close > ema10_curr or curr_price >= exec_price:
                        client.futures_create_order(symbol=symbol, side=tp_side, type='MARKET', quantity=half_qty)
                        profit_tp2 = (exec_price - curr_price) * half_qty
                        update_daily_pnl(profit_tp2)
                        send_telegram(f"🏁 *{symbol} SHORT TP2 / Exit Hit!* Remaining half closed at `{curr_price}`.")
                        break

            time.sleep(5)
        except Exception as e:
            print(f"Monitoring Error on {symbol}: {e}")
            time.sleep(10)
            
    try:
        client.futures_cancel_all_open_orders(symbol=symbol)
        pos_info = client.futures_position_information(symbol=symbol)
        for pos in pos_info:
            if float(pos['positionAmt']) != 0:
                close_side = 'SELL' if float(pos['positionAmt']) > 0 else 'BUY'
                client.futures_create_order(symbol=symbol, side=close_side, type='MARKET', quantity=abs(float(pos['positionAmt'])))
    except Exception as e:
        print(f"Cleanup error for {symbol}: {e}")
        
    active_trades[symbol] = False

def coin_trade_worker(symbol):
    print(f"🔄 Bot Worker active for {symbol}...")
    try:
        client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
    except:
        pass

    while True:
        try:
            is_stopped, current_pnl = check_daily_limit()
            if is_stopped:
                time.sleep(60)
                continue

            if active_trades.get(symbol, False):
                time.sleep(30)
                continue

            side, swing_val, ema10_val = check_signal(symbol)
            if side:
                active_trades[symbol] = True
                curr_price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
                
                notional_size = TOTAL_MARGIN * LEVERAGE
                total_qty = format_quantity(symbol, notional_size / curr_price)
                
                order_side = 'BUY' if side == 'LONG' else 'SELL'
                order = client.futures_create_order(symbol=symbol, side=order_side, type='MARKET', quantity=total_qty, recvWindow=60000)
                exec_price = float(order.get('avgPrice', curr_price))
                
                if side == 'LONG':
                    stop_loss_price = format_price(symbol, min(exec_price * 0.98, swing_val * 0.998))
                    sl_distance = exec_price - stop_loss_price
                    tp1_price = format_price(symbol, exec_price + sl_distance)
                else:
                    stop_loss_price = format_price(symbol, max(exec_price * 1.02, swing_val * 1.002))
                    sl_distance = stop_loss_price - exec_price
                    tp1_price = format_price(symbol, exec_price - sl_distance)
                
                send_telegram(
                    f"🚀 *{side} PULLBACK ENTRY MATCHED*\n"
                    f"Pair: `{symbol}` | Entry: `{exec_price}`\n"
                    f"TP1 (1:1): `{tp1_price}` | SL: `{stop_loss_price}`"
                )
                
                monitor_trade_execution(symbol, side, exec_price, tp1_price, stop_loss_price, total_qty)
                
        except Exception as e:
            print(f"Error in worker {symbol}: {e}")
            active_trades[symbol] = False
        
        time.sleep(30)

def run_concurrent_bots():
    msg = f"🚀 *Ultimate Pullback Bot Running* ($100 Capital, 5x, Target: +$5, Max Loss: -$2)"
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
