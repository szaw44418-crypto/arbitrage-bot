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
    return "🤖 Scalping Bot (30 Combinations with Daily Performance Report) is running!"

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
PROFIT_TARGET_PCT = 0.02   # TP: +2.0%
STOP_LOSS_PCT = 0.01       # SL: -1.0%

symbol_info_cache = {}
active_trades = {}  
DATA_FILE = "signal_performance.json"

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    return {"signal_counter": 0, "history": []}

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)

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
        if not klines or len(klines) < 50: return False, None, None
        
        df = pd.DataFrame(klines, columns=['t','open','high','low','close','v','ct','qav','nt','tb','tq','ig'])
        df['open'] = df['open'].astype(float)
        df['close'] = df['close'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
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

        curr_open = df['open'].iloc[-1]
        curr_close = df['close'].iloc[-1]
        curr_rsi = df['RSI'].iloc[-1]
        prev_rsi = df['RSI'].iloc[-2]
        curr_macd = df['MACD'].iloc[-1]
        curr_sig = df['MACD_Signal'].iloc[-1]
        curr_vol = df['volume'].iloc[-1]
        vol_sma = df['Vol_SMA20'].iloc[-1]
        
        bb_lower_curr = df['BB_Lower'].iloc[-1]
        bb_mid_curr = bb_mid.iloc[-1]
        bb_mid_prev = bb_mid.iloc[-2]
        
        ema9_curr = df['EMA9'].iloc[-1]
        ema9_prev = df['EMA9'].iloc[-2]
        ema20_curr = df['EMA20'].iloc[-1]
        ema20_prev = df['EMA20'].iloc[-2]
        ema50_curr = df['EMA50'].iloc[-1]

        pinbar = ((min(curr_open, curr_close) - df['low'].iloc[-1]) > (abs(curr_open - curr_close) * 2))

        # တောင်းဆိုထားသော Combination Set ၃၀
        c_sets = {
            "#01": (curr_rsi > prev_rsi) and (curr_close > ema50_curr) and (curr_macd > curr_sig) and (curr_vol > vol_sma * 1.5),
            "#02": (curr_rsi < 35) and (curr_rsi > prev_rsi) and (ema9_prev <= ema20_prev and ema9_curr > ema20_curr) and (curr_vol > vol_sma * 1.5),
            "#03": (df['close'].iloc[-2] <= bb_lower_curr) and (curr_close > bb_lower_curr) and (curr_rsi > prev_rsi) and (curr_macd > curr_sig),
            "#04": (curr_rsi < 25) and (curr_rsi > prev_rsi) and (curr_close > ema20_curr) and (curr_vol > vol_sma * 1.5),
            "#05": (ema9_prev <= ema20_prev and ema9_curr > ema20_curr) and (curr_macd > curr_sig) and (curr_vol > vol_sma * 1.5),
            "#06": (curr_close > ema50_curr) and (curr_rsi > prev_rsi) and pinbar,
            "#07": (df['close'].iloc[-2] <= bb_lower_curr) and (curr_close > bb_lower_curr) and pinbar and (curr_rsi < 35 and curr_rsi > prev_rsi),
            "#08": (df['close'].iloc[-3] < df['open'].iloc[-3]) and (df['close'].iloc[-2] < df['open'].iloc[-2]) and (curr_close > curr_open) and (curr_rsi > prev_rsi) and (curr_vol > vol_sma * 1.5),
            "#09": (df['close'].iloc[-2] <= bb_mid_prev and curr_close > bb_mid_curr) and (curr_rsi > 50) and (curr_macd > curr_sig),
            "#10": (curr_close > ema20_curr) and (prev_rsi >= 35 and prev_rsi <= 45 and curr_rsi > prev_rsi) and (ema9_prev <= ema20_prev and ema9_curr > ema20_curr),
            "#11": (curr_rsi < 35) and (curr_rsi > prev_rsi) and (curr_macd > curr_sig) and (curr_vol > vol_sma * 1.5),
            "#12": (curr_rsi < 25) and (curr_rsi > prev_rsi) and pinbar and (curr_macd > curr_sig),
            "#13": (curr_close > ema50_curr) and (df['close'].iloc[-2] <= bb_lower_curr and curr_close > bb_lower_curr) and (curr_rsi > prev_rsi),
            "#14": (ema9_prev <= ema20_prev and ema9_curr > ema20_curr) and (prev_rsi >= 35 and prev_rsi <= 45 and curr_rsi > prev_rsi) and (curr_vol > vol_sma * 1.5),
            "#15": (df['close'].iloc[-2] <= bb_lower_curr and curr_close > bb_lower_curr) and (curr_close > ema20_curr) and (curr_vol > vol_sma * 1.5),
            "#16": (df['close'].iloc[-3] < df['open'].iloc[-3]) and (df['close'].iloc[-2] < df['open'].iloc[-2]) and (curr_close > curr_open) and pinbar and (curr_rsi > prev_rsi) and (curr_vol > vol_sma * 1.5),
            "#17": (curr_close > ema50_curr) and (ema9_prev <= ema20_prev and ema9_curr > ema20_curr) and (curr_macd > curr_sig),
            "#18": (curr_rsi < 25) and (curr_rsi > prev_rsi) and (df['close'].iloc[-2] <= bb_lower_curr and curr_close > bb_lower_curr) and (curr_vol > vol_sma * 1.5),
            "#19": (df['close'].iloc[-2] <= bb_mid_prev and curr_close > bb_mid_curr) and (curr_close > ema20_curr) and (curr_macd > curr_sig) and (curr_vol > vol_sma * 1.5),
            "#20": (curr_close > ema50_curr) and (curr_rsi > prev_rsi) and (ema9_prev <= ema20_prev and ema9_curr > ema20_curr) and (curr_macd > curr_sig) and (curr_vol > vol_sma * 1.5),
            "#21": (curr_rsi > prev_rsi) and (df['close'].iloc[-2] <= bb_lower_curr and curr_close > bb_lower_curr) and (curr_close > ema20_curr),
            "#22": (curr_rsi < 30) and (curr_rsi > prev_rsi) and (curr_macd > curr_sig) and (curr_vol > vol_sma * 1.5),
            "#23": (curr_close > ema50_curr) and (df['close'].iloc[-2] <= bb_mid_prev and curr_close > bb_mid_curr) and (curr_rsi > 50),
            "#24": (ema9_prev <= ema20_prev and ema9_curr > ema20_curr) and pinbar and (curr_vol > vol_sma * 1.5),
            "#25": (curr_rsi < 35) and (curr_rsi > prev_rsi) and (df['close'].iloc[-2] <= bb_lower_curr and curr_close > bb_lower_curr) and pinbar and (curr_vol > vol_sma * 1.5),
            "#26": (curr_close > ema50_curr) and (curr_macd > curr_sig) and (curr_vol > vol_sma * 1.5),
            "#27": (curr_rsi < 25) and (curr_rsi > prev_rsi) and (ema9_prev <= ema20_prev and ema9_curr > ema20_curr) and pinbar,
            "#28": (df['close'].iloc[-2] <= bb_lower_curr and curr_close > bb_lower_curr) and (ema9_prev <= ema20_prev and ema9_curr > ema20_curr) and (curr_macd > curr_sig),
            "#29": (df['close'].iloc[-3] < df['open'].iloc[-3]) and (df['close'].iloc[-2] < df['open'].iloc[-2]) and (curr_close > curr_open) and (curr_rsi < 35 and curr_rsi > prev_rsi) and (curr_close > ema20_curr) and (curr_vol > vol_sma * 1.5),
            "#30": (curr_close > ema50_curr) and (curr_rsi > prev_rsi) and (df['close'].iloc[-2] <= bb_lower_curr and curr_close > bb_lower_curr) and (curr_macd > curr_sig) and (curr_vol > vol_sma * 1.5)
        }

        names = {
            "#01": "RSI Rebound + EMA50 Trend + MACD Crossover + Volume Spike",
            "#02": "RSI <35 Rebound + EMA9/20 Crossover + Volume Spike",
            "#03": "BB Lower Reclaim + RSI Rebound + MACD Crossover",
            "#04": "RSI <25 Recovery + EMA20 Trend + Volume Spike",
            "#05": "EMA9/20 Crossover + MACD Crossover + Volume Spike",
            "#06": "EMA50 Trend + RSI Rebound + Pinbar Reversal",
            "#07": "BB Lower Reclaim + Pinbar Reversal + RSI <35 Rising",
            "#08": "3 Red Reversal + RSI Rising + Volume Spike",
            "#09": "BB Middle Cross + RSI >50 + MACD Crossover",
            "#10": "EMA20 Trend + RSI 35–45 Recovery + EMA9/20 Crossover",
            "#11": "RSI <35 Rebound + MACD Crossover + Volume Spike",
            "#12": "RSI <25 Recovery + Pinbar Reversal + MACD Crossover",
            "#13": "EMA50 Trend + BB Lower Reclaim + RSI Recovery",
            "#14": "EMA9/20 Crossover + RSI 35–45 Recovery + Volume Spike",
            "#15": "BB Lower Reclaim + EMA20 Trend + Volume Spike",
            "#16": "3 Red Reversal + Pinbar Reversal + RSI Recovery + Volume Spike",
            "#17": "EMA50 Trend + EMA9/20 Crossover + MACD Crossover",
            "#18": "RSI <25 Recovery + BB Lower Reclaim + Volume Spike",
            "#19": "BB Middle Cross + EMA20 Trend + MACD Crossover + Volume Spike",
            "#20": "EMA50 Trend + RSI Rebound + EMA9/20 Crossover + MACD Crossover + Volume Spike",
            "#21": "RSI Rebound + BB Lower Reclaim + EMA20 Trend",
            "#22": "RSI <30 Recovery + MACD Crossover + Volume Spike",
            "#23": "EMA50 Trend + BB Middle Cross + RSI >50",
            "#24": "EMA9/20 Crossover + Pinbar Reversal + Volume Spike",
            "#25": "RSI <35 Rebound + BB Lower Reclaim + Pinbar Reversal + Volume Spike",
            "#26": "EMA50 Trend + MACD Crossover + Volume Spike",
            "#27": "RSI <25 Recovery + EMA9/20 Crossover + Pinbar Reversal",
            "#28": "BB Lower Reclaim + EMA9/20 Crossover + MACD Crossover",
            "#29": "3 Red Reversal + RSI <35 Recovery + EMA20 Trend + Volume Spike",
            "#30": "EMA50 Trend + RSI Rebound + BB Lower Reclaim + MACD Crossover + Volume Spike"
        }

        for cid, matched in c_sets.items():
            if matched:
                return True, cid, names[cid]

        return False, None, None
        
    except Exception as e:
        print(f"Condition check error [{symbol}]: {e}")
        return False, None, None

def coin_trade_worker(symbol):
    print(f"🔄 Worker started for {symbol}...")
    while True:
        try:
            if active_trades.get(symbol, False):
                time.sleep(30)
                continue

            should_buy, combo_id, combo_name = check_market_conditions(symbol)
            
            if should_buy:
                active_trades[symbol] = True
                curr_price = float(client.get_symbol_ticker(symbol=symbol)['price'])
                buy_qty = format_quantity(symbol, CAPITAL_PER_ORDER / curr_price)
                
                order = client.create_order(symbol=symbol, side='BUY', type='MARKET', quantity=buy_qty, recvWindow=60000)
                exec_price = float(order.get('fills', [{}])[0].get('price', curr_price))
                total_coins = float(order['executedQty'])
                
                data = load_data()
                data["signal_counter"] += 1
                sig_id = f"#{data['signal_counter']:04d}"
                save_data(data)
                
                time_str = datetime.datetime.now().strftime('%H:%M')
                
                # Telegram သို့ Signal ပို့ခြင်း
                send_telegram(
                    f"{combo_id}\n"
                    f"`{symbol}`\n"
                    f"Combination: {combo_name}\n"
                    f"Entry: `{exec_price}`\n"
                    f"Time: `{time_str}`"
                )
                
                target_sell = format_price(symbol, exec_price * (1 + PROFIT_TARGET_PCT))
                stop_loss_price = format_price(symbol, exec_price * (1 - STOP_LOSS_PCT))
                
                sell_order = client.create_order(
                    symbol=symbol, side='SELL', type='LIMIT', timeInForce='GTC',
                    quantity=format_quantity(symbol, total_coins), price=str(target_sell), recvWindow=60000
                )
                
                order_id = sell_order['orderId']
                trade_result = None
                pnl_pct = 0.0
                
                while True:
                    chk = client.get_order(symbol=symbol, orderId=order_id)
                    if chk['status'] == 'FILLED':
                        trade_result = "WIN"
                        pnl_pct = PROFIT_TARGET_PCT * 100
                        break
                    
                    live_p = float(client.get_symbol_ticker(symbol=symbol)['price'])
                    if live_p <= stop_loss_price:
                        client.cancel_order(symbol=symbol, orderId=order_id)
                        client.create_order(symbol=symbol, side='SELL', type='MARKET', quantity=format_quantity(symbol, total_coins), recvWindow=60000)
                        trade_result = "LOSS"
                        pnl_pct = -STOP_LOSS_PCT * 100
                        break
                    time.sleep(10)
                
                # Data Collection (မှတ်တမ်းသိမ်းဆည်းခြင်း)
                lock_data = load_data()
                lock_data["history"].append({
                    "sig_id": sig_id,
                    "combo_id": combo_id,
                    "symbol": symbol,
                    "result": trade_result,
                    "pnl": pnl_pct
                })
                save_data(lock_data)
                
                # Result ကို Telegram သို့ ပို့ခြင်း
                send_telegram(
                    f"Signal ID: `{sig_id}`\n"
                    f"Pair: `{symbol}`\n"
                    f"Combination: `{combo_id}`\n"
                    f"TP: `+{PROFIT_TARGET_PCT*100}%` | SL: `-{STOP_LOSS_PCT*100}%`\n\n"
                    f"Result: `{'WIN 🎉' if trade_result == 'WIN' else 'LOSS 🚨'}`\n"
                    f"P&L: `{'+' if pnl_pct > 0 else ''}{pnl_pct:.1f}%`"
                )
                
                active_trades[symbol] = False
                    
        except Exception as e:
            print(f"Error in worker {symbol}: {e}")
            active_trades[symbol] = False
        
        time.sleep(30)

def daily_report_worker():
    """ နေ့စဉ် Combination ၃၀ လုံး၏ ရလဒ်များကို နှိုင်းယှဉ်ထုတ်ပြန်ပေးရန် """
    while True:
        now = datetime.datetime.now()
        target_time = now.replace(hour=23, minute=59, second=0, microsecond=0)
        if now > target_time:
            target_time += datetime.timedelta(days=1)
        
        time.sleep((target_time - now).total_seconds())
        
        data = load_data()
        history = data.get("history", [])
        
        report_msg = "📊 *DAILY COMBINATION PERFORMANCE REPORT* 📊\n━━━━━━━━━━━━━━━━━━━\n"
        
        for i in range(1, 31):
            cid = f"#{i:02d}"
            com_trades = [h for h in history if h["combo_id"] == cid]
            signals_count = len(com_trades)
            
            if signals_count == 0:
                continue
                
            wins = len([h for h in com_trades if h["result"] == "WIN"])
            losses = len([h for h in com_trades if h["result"] == "LOSS"])
            win_rate = (wins / signals_count) * 100 if signals_count > 0 else 0
            net_pnl = sum([h["pnl"] for h in com_trades])
            
            report_msg += (
                f"🔹 *Combination {cid}*\n"
                f"• Signals: `{signals_count}`\n"
                f"• Win: `{wins}` | Loss: `{losses}`\n"
                f"• Win Rate: `{win_rate:.1f}%`\n"
                f"• Net P&L: `{'+' if net_pnl > 0 else ''}{net_pnl:.1f}%`\n"
                f"-------------------\n"
            )
            
        send_telegram(report_msg)
        time.sleep(60)

def run_concurrent_bots():
    msg = f"🚀 *Scalping Bot & 30 Combinations Tracker Started* (TP: +2%, SL: -1%)"
    print(msg)
    send_telegram(msg)
    
    report_thread = Thread(target=daily_report_worker)
    report_thread.daemon = True
    report_thread.start()
    
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
