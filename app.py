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
    return "🤖 Net-Profit Scalping Bot (Profit: 3%, SL: 2%) is running live!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

SPOT_BASE = "https://testnet.binance.vision"
SPOT_API_KEY = os.environ.get("SPOT_API_KEY", "EGMDZzNYcF8aHKsKGxWurbK63sLFdKA42cDEZC3zd8IPkyD3JDEH7btCt4D34aWV")
SPOT_SECRET_KEY = os.environ.get("SPOT_SECRET_KEY", "YfGOumNKz4MMbZ9MBy7aMB3R6CWxSjVljJvreup8k3BGL5pi1pqc73ieCpOghM8R")

TELEGRAM_BOT_TOKEN = "8849579856:AAF7kWMMgtCswjY-Vcog-oa0ur16c60dJio"
TELEGRAM_CHAT_ID = "6127362073"

client = Client(SPOT_API_KEY, SPOT_SECRET_KEY, testnet=True)
client.API_URL = f"{SPOT_BASE}/api"

COINS = [
    "LTCUSDT", "BCHUSDT", "ETCUSDT", "NEARUSDT", 
    "ATOMUSDT", "SOLUSDT", "ARBUSDT", "OPUSDT", 
    "FILUSDT", "ICPUSDT"
]

CAPITAL_PER_ORDER = 10.0  
PROFIT_TARGET_PCT = 0.03   
STOP_LOSS_PCT = 0.02       

symbol_info_cache = {}
active_trades = {}  

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
        if not klines or len(klines) < 50: return False, None
        
        df = pd.DataFrame(klines, columns=['t','open','high','low','close','v','ct','qav','nt','tb','tq','ig'])
        
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)
        df['volume'] = df['v'].astype(float)
        
        df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['EMA9'] = df['close'].ewm(span=9, adjust=False).mean()
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        df['RSI'] = 100 - (100 / (1 + (gain / loss)))
        
        bb_mid = df['close'].rolling(window=20).mean()
        bb_std = df['close'].rolling(window=20).std()
        df['BB_Lower'] = bb_mid - (2 * bb_std)
        df['BB_Upper'] = bb_mid + (2 * bb_std)
        
        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = exp1 - exp2
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['Vol_SMA20'] = df['volume'].rolling(window=20).mean()

        curr_open, prev_open = df['open'].iloc[-1], df['open'].iloc[-2]
        curr_close, prev_close = df['close'].iloc[-1], df['close'].iloc[-2]
        curr_high, prev_high = df['high'].iloc[-1], df['high'].iloc[-2]
        curr_low, prev_low = df['low'].iloc[-1], df['low'].iloc[-2]
        
        curr_rsi, prev_rsi = df['RSI'].iloc[-1], df['RSI'].iloc[-2]
        curr_macd, prev_macd = df['MACD'].iloc[-1], df['MACD'].iloc[-2]
        curr_sig, prev_sig = df['MACD_Signal'].iloc[-1], df['MACD_Signal'].iloc[-2]
        
        curr_vol = df['volume'].iloc[-1]
        vol_sma = df['Vol_SMA20'].iloc[-1]
        
        bb_lower_curr = df['BB_Lower'].iloc[-1]
        bb_mid_curr, bb_mid_prev = bb_mid.iloc[-1], bb_mid.iloc[-2]
        
        ema9_curr, ema9_prev = df['EMA9'].iloc[-1], df['EMA9'].iloc[-2]
        ema20_curr, ema20_prev = df['EMA20'].iloc[-1], df['EMA20'].iloc[-2]
        ema50_curr = df['EMA50'].iloc[-1]

        pinbar = ((min(curr_open, curr_close) - curr_low) > (abs(curr_open - curr_close) * 2))

        # တောင်းဆိုထားသော Combination Set ၃၀
        set_1 = (curr_rsi > prev_rsi) and (curr_close > ema50_curr) and (curr_macd > curr_sig) and (curr_vol > vol_sma * 1.5)
        set_2 = (curr_rsi < 35) and (curr_rsi > prev_rsi) and (ema9_prev <= ema20_prev and ema9_curr > ema20_curr) and (curr_vol > vol_sma * 1.5)
        set_3 = (prev_close <= bb_lower_curr) and (curr_close > bb_lower_curr) and (curr_rsi > prev_rsi) and (curr_macd > curr_sig)
        set_4 = (curr_rsi < 25) and (curr_rsi > prev_rsi) and (curr_close > ema20_curr) and (curr_vol > vol_sma * 1.5)
        set_5 = (ema9_prev <= ema20_prev and ema9_curr > ema20_curr) and (curr_macd > curr_sig) and (curr_vol > vol_sma * 1.5)
        set_6 = (curr_close > ema50_curr) and (curr_rsi > prev_rsi) and pinbar
        set_7 = (prev_close <= bb_lower_curr) and (curr_close > bb_lower_curr) and pinbar and (curr_rsi < 35 and curr_rsi > prev_rsi)
        set_8 = (df['close'].iloc[-3] < df['open'].iloc[-3]) and (prev_close < prev_open) and (curr_close > curr_open) and (curr_rsi > prev_rsi) and (curr_vol > vol_sma * 1.5)
        set_9 = (prev_close <= bb_mid_prev and curr_close > bb_mid_curr) and (curr_rsi > 50) and (curr_macd > curr_sig)
        set_10 = (curr_close > ema20_curr) and (prev_rsi >= 35 and prev_rsi <= 45 and curr_rsi > prev_rsi) and (ema9_prev <= ema20_prev and ema9_curr > ema20_curr)
        set_11 = (curr_rsi < 35) and (curr_rsi > prev_rsi) and (curr_macd > curr_sig) and (curr_vol > vol_sma * 1.5)
        set_12 = (curr_rsi < 25) and (curr_rsi > prev_rsi) and pinbar and (curr_macd > curr_sig)
        set_13 = (curr_close > ema50_curr) and (prev_close <= bb_lower_curr and curr_close > bb_lower_curr) and (curr_rsi > prev_rsi)
        set_14 = (ema9_prev <= ema20_prev and ema9_curr > ema20_curr) and (prev_rsi >= 35 and prev_rsi <= 45 and curr_rsi > prev_rsi) and (curr_vol > vol_sma * 1.5)
        set_15 = (prev_close <= bb_lower_curr and curr_close > bb_lower_curr) and (curr_close > ema20_curr) and (curr_vol > vol_sma * 1.5)
        set_16 = (df['close'].iloc[-3] < df['open'].iloc[-3]) and (prev_close < prev_open) and (curr_close > curr_open) and pinbar and (curr_rsi > prev_rsi) and (curr_vol > vol_sma * 1.5)
        set_17 = (curr_close > ema50_curr) and (ema9_prev <= ema20_prev and ema9_curr > ema20_curr) and (curr_macd > curr_sig)
        set_18 = (curr_rsi < 25) and (curr_rsi > prev_rsi) and (prev_close <= bb_lower_curr and curr_close > bb_lower_curr) and (curr_vol > vol_sma * 1.5)
        set_19 = (prev_close <= bb_mid_prev and curr_close > bb_mid_curr) and (curr_close > ema20_curr) and (curr_macd > curr_sig) and (curr_vol > vol_sma * 1.5)
        set_20 = (curr_close > ema50_curr) and (curr_rsi > prev_rsi) and (ema9_prev <= ema20_prev and ema9_curr > ema20_curr) and (curr_macd > curr_sig) and (curr_vol > vol_sma * 1.5)
        set_21 = (curr_rsi > prev_rsi) and (prev_close <= bb_lower_curr and curr_close > bb_lower_curr) and (curr_close > ema20_curr)
        set_22 = (curr_rsi < 30) and (curr_rsi > prev_rsi) and (curr_macd > curr_sig) and (curr_vol > vol_sma * 1.5)
        set_23 = (curr_close > ema50_curr) and (prev_close <= bb_mid_prev and curr_close > bb_mid_curr) and (curr_rsi > 50)
        set_24 = (ema9_prev <= ema20_prev and ema9_curr > ema20_curr) and pinbar and (curr_vol > vol_sma * 1.5)
        set_25 = (curr_rsi < 35) and (curr_rsi > prev_rsi) and (prev_close <= bb_lower_curr and curr_close > bb_lower_curr) and pinbar and (curr_vol > vol_sma * 1.5)
        set_26 = (curr_close > ema50_curr) and (curr_macd > curr_sig) and (curr_vol > vol_sma * 1.5)
        set_27 = (curr_rsi < 25) and (curr_rsi > prev_rsi) and (ema9_prev <= ema20_prev and ema9_curr > ema20_curr) and pinbar
        set_28 = (prev_close <= bb_lower_curr and curr_close > bb_lower_curr) and (ema9_prev <= ema20_prev and ema9_curr > ema20_curr) and (curr_macd > curr_sig)
        set_29 = (df['close'].iloc[-3] < df['open'].iloc[-3]) and (prev_close < prev_open) and (curr_close > curr_open) and (curr_rsi < 35 and curr_rsi > prev_rsi) and (curr_close > ema20_curr) and (curr_vol > vol_sma * 1.5)
        set_30 = (curr_close > ema50_curr) and (curr_rsi > prev_rsi) and (prev_close <= bb_lower_curr and curr_close > bb_lower_curr) and (curr_macd > curr_sig) and (curr_vol > vol_sma * 1.5)

        if set_1: return True, "Set 1 (RSI Rebound + EMA50 + MACD + Vol)"
        if set_2: return True, "Set 2 (RSI <35 + EMA9/20 + Vol)"
        if set_3: return True, "Set 3 (BB Lower + RSI Rebound + MACD)"
        if set_4: return True, "Set 4 (RSI <25 + EMA20 + Vol)"
        if set_5: return True, "Set 5 (EMA9/20 + MACD + Vol)"
        if set_6: return True, "Set 6 (EMA50 + RSI Rebound + Pinbar)"
        if set_7: return True, "Set 7 (BB Lower + Pinbar + RSI <35)"
        if set_8: return True, "Set 8 (3 Red Rev + RSI + Vol)"
        if set_9: return True, "Set 9 (BB Mid + RSI >50 + MACD)"
        if set_10: return True, "Set 10 (EMA20 + RSI 35-45 + EMA9/20)"
        if set_11: return True, "Set 11 (RSI <35 + MACD + Vol)"
        if set_12: return True, "Set 12 (RSI <25 + Pinbar + MACD)"
        if set_13: return True, "Set 13 (EMA50 + BB Lower + RSI Rec)"
        if set_14: return True, "Set 14 (EMA9/20 + RSI 35-45 + Vol)"
        if set_15: return True, "Set 15 (BB Lower + EMA20 + Vol)"
        if set_16: return True, "Set 16 (3 Red Rev + Pinbar + RSI + Vol)"
        if set_17: return True, "Set 17 (EMA50 + EMA9/20 + MACD)"
        if set_18: return True, "Set 18 (RSI <25 + BB Lower + Vol)"
        if set_19: return True, "Set 19 (BB Mid + EMA20 + MACD + Vol)"
        if set_20: return True, "Set 20 (EMA50 + RSI + EMA9/20 + MACD + Vol)"
        if set_21: return True, "Set 21 (RSI Rebound + BB Lower + EMA20)"
        if set_22: return True, "Set 22 (RSI <30 + MACD + Vol)"
        if set_23: return True, "Set 23 (EMA50 + BB Mid + RSI >50)"
        if set_24: return True, "Set 24 (EMA9/20 + Pinbar + Vol)"
        if set_25: return True, "Set 25 (RSI <35 + BB Lower + Pinbar + Vol)"
        if set_26: return True, "Set 26 (EMA50 + MACD + Vol)"
        if set_27: return True, "Set 27 (RSI <25 + EMA9/20 + Pinbar)"
        if set_28: return True, "Set 28 (BB Lower + EMA9/20 + MACD)"
        if set_29: return True, "Set 29 (3 Red + RSI <35 + EMA20 + Vol)"
        if set_30: return True, "Set 30 (EMA50 + RSI + BB Lower + MACD + Vol)"

        return False, None
        
    except Exception as e:
        print(f"Condition check error [{symbol}]: {e}")
        return False, None

def coin_trade_worker(symbol):
    print(f"🔄 Worker started for {symbol}...")
    while True:
        try:
            if active_trades.get(symbol, False):
                time.sleep(30)
                continue

            should_buy, matched_set = check_market_conditions(symbol)
            
            if should_buy:
                active_trades[symbol] = True
                curr_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
                buy_qty = format_quantity(symbol, CAPITAL_PER_ORDER / curr_price)
                
                order = client.create_order(symbol=symbol, side='BUY', type='MARKET', quantity=buy_qty, recvWindow=60000)
                exec_price = float(order.get('fills', [{}])[0].get('price', curr_price))
                total_coins = float(order['executedQty'])
                total_cost = total_coins * exec_price
                
                send_telegram(f"🟢 *[{symbol}] Buy Executed*\n• Trigger: `{matched_set}`\n• Price: `{exec_price}`\n• Cost: `{total_cost:.2f} USDT`")
                
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
                        send_telegram(f"🚨 *[{symbol}] Stop-Loss Triggered*\n• Triggered by: `{matched_set}`\n• Exit Price: `{live_p}`")
                        break
                    time.sleep(10)
                
                total_revenue = total_coins * exit_price
                net_profit = total_revenue - total_cost
                
                if trade_successful:
                    send_telegram(f"✅ *[{symbol}] Cycle Completed (PROFIT)*\n• Trigger: `{matched_set}`\n• Net Profit: `+{net_profit:.2f} USDT`\n• Exit Price: `{exit_price}`")
                else:
                    send_telegram(f"❌ *[{symbol}] Cycle Stopped (LOSS)*\n• Trigger: `{matched_set}`\n• Net Loss: `{net_profit:.2f} USDT`\n• Exit Price: `{exit_price}`")
                
                active_trades[symbol] = False
                    
        except Exception as e:
            print(f"Error in worker {symbol}: {e}")
            active_trades[symbol] = False
        
        time.sleep(30)

def run_concurrent_bots():
    msg = f"🚀 *Scalping Bot Started* (Profit: 3%, SL: 2%)"
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
