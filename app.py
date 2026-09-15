import os
import time
import math
import requests
import datetime
import json
from threading import Thread, Lock
from flask import Flask
import pandas as pd
from binance.client import Client

app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Pure WebSocket 5-Strategy Multi-Bot is running successfully!"

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
TOTAL_MARGIN = 30.0  
DAILY_PROFIT_LIMIT = 5.0    
DAILY_LOSS_LIMIT = -2.0     
MAX_ACTIVE_TRADES = 3      

symbol_info_cache = {}
active_trades = {}  
latest_prices = {}
price_history = {coin: [] for coin in COINS}
last_checked_candle_time = {}

DATA_FILE = "advanced_5_strategy_data.json"
api_lock = Lock()

def load_data():
    default_strategies = {
        "50EMA_RSI_Pullback": {"signals": 0, "win": 0, "loss": 0, "pnl": 0.0},
        "20EMA_StochRSI": {"signals": 0, "win": 0, "loss": 0, "pnl": 0.0},
        "BB_Middle_Pullback": {"signals": 0, "win": 0, "loss": 0, "pnl": 0.0},
        "MACD_Zero_EMA10": {"signals": 0, "win": 0, "loss": 0, "pnl": 0.0},
        "SuperTrend_EMA10": {"signals": 0, "win": 0, "loss": 0, "pnl": 0.0}
    }
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                if "strategies" not in data:
                    data["strategies"] = default_strategies
                else:
                    for s_key in default_strategies:
                        if s_key not in data["strategies"]:
                            data["strategies"][s_key] = default_strategies[s_key]
                return data
        except:
            pass
    return {
        "date": str(datetime.date.today()), 
        "daily_pnl": 0.0, 
        "strategies": default_strategies,
        "history": []
    }

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

def update_daily_pnl(amount, strat_name=None, is_win=None):
    data = load_data()
    today_str = str(datetime.date.today())
    if data.get("date") != today_str:
        data["date"] = today_str
        data["daily_pnl"] = 0.0
    
    data["daily_pnl"] = data.get("daily_pnl", 0.0) + amount
    
    if strat_name and strat_name in data["strategies"]:
        if is_win is not None:
            if is_win:
                data["strategies"][strat_name]["win"] += 1
            else:
                data["strategies"][strat_name]["loss"] += 1
        data["strategies"][strat_name]["pnl"] += amount
        
    data["history"].append({"timestamp": str(datetime.datetime.now()), "pnl": amount})
    save_data(data)

def record_signal(strat_name):
    data = load_data()
    if strat_name in data["strategies"]:
        data["strategies"][strat_name]["signals"] += 1
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
            with api_lock:
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
        with api_lock:
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

# STRATEGY FUNCTIONS
def strategy_50ema_rsi(df, c_close, c_low, c_high, is_green_reversal, is_red_reversal, recent_low, recent_high):
    rsi_curr = df['RSI'].iloc[-2]
    ema200_val = df['EMA200'].iloc[-2]
    ema50_val = df['EMA50'].iloc[-2]

    if (c_close > ema200_val) and (c_low <= ema50_val or c_close <= ema50_val) and (35 <= rsi_curr <= 45) and is_green_reversal:
        return "LONG", recent_low, df['EMA10'].iloc[-2], "50EMA_RSI_Pullback"
    if (c_close < ema200_val) and (c_high >= ema50_val or c_close >= ema50_val) and (55 <= rsi_curr <= 65) and is_red_reversal:
        return "SHORT", recent_high, df['EMA10'].iloc[-2], "50EMA_RSI_Pullback"
    return None, 0, 0, ""

def strategy_20ema_stoch_rsi(df, c_close, c_low, c_high, is_green_reversal, is_red_reversal, recent_low, recent_high):
    stoch_k = df['StochRSI_K'].iloc[-2]
    stoch_d = df['StochRSI_D'].iloc[-2]
    prev_stoch_k = df['StochRSI_K'].iloc[-3]
    prev_stoch_d = df['StochRSI_D'].iloc[-3]
    ema200_val = df['EMA200'].iloc[-2]
    ema20_val = df['EMA20'].iloc[-2]

    if (c_close > ema200_val) and (c_low <= ema20_val or c_close <= ema20_val):
        if (prev_stoch_k < prev_stoch_d) and (stoch_k > stoch_d) and stoch_k < 20 and is_green_reversal:
            return "LONG", recent_low, df['EMA10'].iloc[-2], "20EMA_StochRSI"
    if (c_close < ema200_val) and (c_high >= ema20_val or c_close >= ema20_val):
        if (prev_stoch_k > prev_stoch_d) and (stoch_k < stoch_d) and stoch_k > 80 and is_red_reversal:
            return "SHORT", recent_high, df['EMA10'].iloc[-2], "20EMA_StochRSI"
    return None, 0, 0, ""

def strategy_bb_middle_pullback(df, c_close, c_low, c_high, is_green_reversal, is_red_reversal, recent_low, recent_high):
    rsi_curr = df['RSI'].iloc[-2]
    bb_middle = df['BB_middle'].iloc[-2]
    ema200_val = df['EMA200'].iloc[-2]

    if (c_close > ema200_val) and (c_low <= bb_middle or c_close <= bb_middle) and (rsi_curr <= 40) and is_green_reversal:
        return "LONG", recent_low, df['EMA10'].iloc[-2], "BB_Middle_Pullback"
    if (c_close < ema200_val) and (c_high >= bb_middle or c_close >= bb_middle) and (rsi_curr >= 60) and is_red_reversal:
        return "SHORT", recent_high, df['EMA10'].iloc[-2], "BB_Middle_Pullback"
    return None, 0, 0, ""

def strategy_macd_zeroline_ema10(df, c_close, c_low, c_high, is_green_reversal, is_red_reversal, recent_low, recent_high):
    macd_curr = df['MACD'].iloc[-2]
    rsi_curr = df['RSI'].iloc[-2]
    ema10_val = df['EMA10'].iloc[-2]

    if (macd_curr > 0) and (c_low <= ema10_val or c_close <= ema10_val) and (35 <= rsi_curr <= 45) and is_green_reversal:
        return "LONG", recent_low, ema10_val, "MACD_Zero_EMA10"
    if (macd_curr < 0) and (c_high >= ema10_val or c_close >= ema10_val) and (55 <= rsi_curr <= 65) and is_red_reversal:
        return "SHORT", recent_high, ema10_val, "MACD_Zero_EMA10"
    return None, 0, 0, ""

def strategy_supertrend_ema10(df, c_close, c_low, c_high, is_green_reversal, is_red_reversal, recent_low, recent_high):
    rsi_curr = df['RSI'].iloc[-2]
    ema10_val = df['EMA10'].iloc[-2]
    st_direction = df['ST_direction'].iloc[-2]

    if (st_direction == 1) and (c_low <= ema10_val or c_close <= ema10_val) and (30 <= rsi_curr <= 40) and is_green_reversal:
        return "LONG", recent_low, ema10_val, "SuperTrend_EMA10"
    if (st_direction == -1) and (c_high >= ema10_val or c_close >= ema10_val) and (60 <= rsi_curr <= 70) and is_red_reversal:
        return "SHORT", recent_high, ema10_val, "SuperTrend_EMA10"
    return None, 0, 0, ""

def check_all_strategies_signal(symbol):
    try:
        prices = price_history.get(symbol, [])
        if len(prices) < 210: 
            return None, 0, 0, ""
        
        df = pd.DataFrame({'close': prices})
        df['open'] = df['close'].shift(1).fillna(df['close'])
        df['high'] = df['close'] * 1.001
        df['low'] = df['close'] * 0.999
        df['volume'] = 1000.0
        
        df['EMA200'] = df['close'].ewm(span=200, adjust=False).mean()
        df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
        df['EMA10'] = df['close'].ewm(span=10, adjust=False).mean()
        
        df['BB_middle'] = df['close'].rolling(window=20).mean()
        df['BB_std'] = df['close'].rolling(window=20).std()
        df['BB_upper'] = df['BB_middle'] + (2 * df['BB_std'])
        df['BB_lower'] = df['BB_middle'] - (2 * df['BB_std'])
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(com=13, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(com=13, adjust=False).mean()
        df['RSI'] = 100 - (100 / (1 + (gain / loss)))
        
        df['RSI_min'] = df['RSI'].rolling(window=14).min()
        df['RSI_max'] = df['RSI'].rolling(window=14).max()
        df['StochRSI'] = (df['RSI'] - df['RSI_min']) / (df['RSI_max'] - df['RSI_min'] + 1e-10)
        df['StochRSI_K'] = df['StochRSI'].rolling(window=3).mean() * 100
        df['StochRSI_D'] = df['StochRSI_K'].rolling(window=3).mean()

        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = exp1 - exp2
        
        high_low = df['high'] - df['low']
        high_cp = (df['high'] - df['close'].shift()).abs()
        low_cp = (df['low'] - df['close'].shift()).abs()
        df['TR'] = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
        df['ATR'] = df['TR'].rolling(window=10).mean()
        
        df['ST_mid'] = (df['high'] + df['low']) / 2
        df['ST_upper'] = df['ST_mid'] + (3 * df['ATR'])
        df['ST_lower'] = df['ST_mid'] - (3 * df['ATR'])
        
        st_dir = []
        current_dir = 1
        for i in range(len(df)):
            if i < 10:
                st_dir.append(1)
                continue
            if df['close'].iloc[i] > df['ST_upper'].iloc[i-1]:
                current_dir = 1
            elif df['close'].iloc[i] < df['ST_lower'].iloc[i-1]:
                current_dir = -1
            st_dir.append(current_dir)
        df['ST_direction'] = st_dir

        c_close, c_open = df['close'].iloc[-2], df['open'].iloc[-2]
        c_high, c_low = df['high'].iloc[-2], df['low'].iloc[-2]
        p_close, p_open = df['close'].iloc[-3], df['open'].iloc[-3]
        
        is_green_reversal = (p_close < p_open) and (c_close > c_open)
        is_red_reversal = (p_close > p_open) and (c_close < c_open)
        
        recent_low = df['low'].iloc[-10:-1].min()
        recent_high = df['high'].iloc[-10:-1].max()

        side, sl, ema10, strat = strategy_50ema_rsi(df, c_close, c_low, c_high, is_green_reversal, is_red_reversal, recent_low, recent_high)
        if side: return side, sl, ema10, strat
        
        side, sl, ema10, strat = strategy_20ema_stoch_rsi(df, c_close, c_low, c_high, is_green_reversal, is_red_reversal, recent_low, recent_high)
        if side: return side, sl, ema10, strat
        
        side, sl, ema10, strat = strategy_bb_middle_pullback(df, c_close, c_low, c_high, is_green_reversal, is_red_reversal, recent_low, recent_high)
        if side: return side, sl, ema10, strat
        
        side, sl, ema10, strat = strategy_macd_zeroline_ema10(df, c_close, c_low, c_high, is_green_reversal, is_red_reversal, recent_low, recent_high)
        if side: return side, sl, ema10, strat
        
        side, sl, ema10, strat = strategy_supertrend_ema10(df, c_close, c_low, c_high, is_green_reversal, is_red_reversal, recent_low, recent_high)
        if side: return side, sl, ema10, strat

        return None, 0, 0, ""
    except Exception as e:
        print(f"Strategy Error for [{symbol}]: {e}")
        return None, 0, 0, ""

def monitor_trade_execution(symbol, side, exec_price, tp1_price, stop_loss_price, total_qty, strat_name):
    tp_side = 'SELL' if side == 'LONG' else 'BUY'
    half_qty = format_quantity(symbol, total_qty / 2)
    tp1_hit = False
    
    try:
        with api_lock:
            tp1_order = client.futures_create_order(
                symbol=symbol, side=tp_side, type='LIMIT', timeInForce='GTC',
                quantity=half_qty, price=str(tp1_price), recvWindow=60000
            )
        tp1_order_id = tp1_order['orderId']
    except Exception as e:
        print(f"⚠️ Limit TP1 Order Error: {e}")
        active_trades[symbol] = False
        return

    while True:
        try:
            is_stopped, current_pnl = check_daily_limit()
            if is_stopped:
                close_all_positions("Daily PnL Boundary Crossed inside Monitor")
                break

            curr_price = latest_prices.get(symbol, exec_price)
            
            prices = price_history.get(symbol, [])
            if len(prices) > 20:
                df_check = pd.DataFrame({'close': prices})
                delta = df_check['close'].diff()
                gain = (delta.where(delta > 0, 0)).ewm(com=13, adjust=False).mean()
                loss = (-delta.where(delta < 0, 0)).ewm(com=13, adjust=False).mean()
                df_check['RSI'] = 100 - (100 / (1 + (gain / loss)))
                last_rsi = df_check['RSI'].iloc[-2]
                last_candle_close = df_check['close'].iloc[-2]
                ema10_curr = df_check['close'].ewm(span=10, adjust=False).mean().iloc[-2]
            else:
                last_rsi = 50
                last_candle_close = curr_price
                ema10_curr = curr_price

            if side == "LONG":
                if curr_price <= stop_loss_price:
                    with api_lock:
                        client.futures_cancel_all_open_orders(symbol=symbol)
                    loss_amount = (stop_loss_price - exec_price) * total_qty
                    update_daily_pnl(loss_amount, strat_name, is_win=False)
                    send_telegram(f"🛑 *{symbol} LONG SL Hit!* [{strat_name}] Price: `{curr_price}` | Loss: `{round(loss_amount, 2)} USDT`")
                    break
                
                if not tp1_hit:
                    with api_lock:
                        order_status = client.futures_get_order(symbol=symbol, orderId=tp1_order_id)
                    if order_status['status'] == 'FILLED':
                        tp1_hit = True
                        stop_loss_price = exec_price  
                        profit_tp1 = (tp1_price - exec_price) * half_qty
                        update_daily_pnl(profit_tp1)
                        send_telegram(f"🎯 *{symbol} LONG TP1 Hit!* [{strat_name}] SL moved to Break-even `{exec_price}`")
                else:
                    if last_rsi >= 70 or last_candle_close < ema10_curr or curr_price <= exec_price:
                        with api_lock:
                            client.futures_create_order(symbol=symbol, side=tp_side, type='MARKET', quantity=half_qty)
                        profit_tp2 = (curr_price - exec_price) * half_qty
                        update_daily_pnl(profit_tp2, strat_name, is_win=True)
                        send_telegram(f"🏁 *{symbol} LONG Exit Hit!* [{strat_name}] Closed remaining half.")
                        break

            elif side == "SHORT":
                if curr_price >= stop_loss_price:
                    with api_lock:
                        client.futures_cancel_all_open_orders(symbol=symbol)
                    loss_amount = (stop_loss_price - exec_price) * total_qty
                    update_daily_pnl(loss_amount, strat_name, is_win=False)
                    send_telegram(f"🛑 *{symbol} SHORT SL Hit!* [{strat_name}] Price: `{curr_price}` | Loss: `{round(loss_amount, 2)} USDT`")
                    break
                
                if not tp1_hit:
                    with api_lock:
                        order_status = client.futures_get_order(symbol=symbol, orderId=tp1_order_id)
                    if order_status['status'] == 'FILLED':
                        tp1_hit = True
                        stop_loss_price = exec_price  
                        profit_tp1 = (exec_price - tp1_price) * half_qty
                        update_daily_pnl(profit_tp1)
                        send_telegram(f"🎯 *{symbol} SHORT TP1 Hit!* [{strat_name}] SL moved to Break-even `{exec_price}`")
                else:
                    if last_rsi <= 30 or last_candle_close > ema10_curr or curr_price >= exec_price:
                        with api_lock:
                            client.futures_create_order(symbol=symbol, side=tp_side, type='MARKET', quantity=half_qty)
                        profit_tp2 = (exec_price - curr_price) * half_qty
                        update_daily_pnl(profit_tp2, strat_name, is_win=True)
                        send_telegram(f"🏁 *{symbol} SHORT Exit Hit!* [{strat_name}] Closed remaining half.")
                        break

            time.sleep(10)
        except Exception as e:
            print(f"Monitoring Error on {symbol}: {e}")
            time.sleep(10)
            
    try:
        with api_lock:
            client.futures_cancel_all_open_orders(symbol=symbol)
            pos_info = client.futures_position_information(symbol=symbol)
            for pos in pos_info:
                if float(pos['positionAmt']) != 0:
                    close_side = 'SELL' if float(pos['positionAmt']) > 0 else 'BUY'
                    client.futures_create_order(symbol=symbol, side=close_side, type='MARKET', quantity=abs(float(pos['positionAmt'])))
    except Exception as e:
        print(f"Cleanup error for {symbol}: {e}")
        
    active_trades[symbol] = False

def market_scanner_loop():
    print("🔄 Pure WebSocket Market Scanner active...")
    time.sleep(10) # Wait for websocket to gather initial prices
    
    while True:
        try:
            is_stopped, current_pnl = check_daily_limit()
            if is_stopped:
                time.sleep(60)
                continue

            active_count = sum(1 for s in COINS if active_trades.get(s, False))
            if active_count >= MAX_ACTIVE_TRADES:
                time.sleep(15)
                continue

            for symbol in COINS:
                if active_trades.get(symbol, False):
                    continue

                side, swing_val, ema10_val, strat_name = check_all_strategies_signal(symbol)
                if side:
                    record_signal(strat_name) 
                    active_trades[symbol] = True
                    curr_price = latest_prices.get(symbol, 0.0)
                    
                    notional_size = TOTAL_MARGIN * LEVERAGE
                    total_qty = format_quantity(symbol, notional_size / curr_price)
                    
                    order_side = 'BUY' if side == 'LONG' else 'SELL'
                    with api_lock:
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
                        f"🚀 *5-STRATEGY SIGNAL MATCHED [{strat_name}]*\n"
                        f"Pair: `{symbol}` | Side: `{side}` | Entry: `{exec_price}`\n"
                        f"TP1 (1:1): `{tp1_price}` | SL: `{stop_loss_price}`"
                    )
                    
                    t = Thread(target=monitor_trade_execution, args=(symbol, side, exec_price, tp1_price, stop_loss_price, total_qty, strat_name))
                    t.daemon = True
                    t.start()
                
                time.sleep(2)
        except Exception as e:
            print(f"Error in scanner loop: {e}")
        
        time.sleep(10)

def handle_socket_message(msg):
    if msg.get('e') == 'bookTicker':
        symbol = msg.get('s')
        best_price = float(msg.get('b', 0))
        if symbol in COINS and best_price > 0:
            latest_prices[symbol] = best_price
            if len(price_history[symbol]) == 0 or price_history[symbol][-1] != best_price:
                price_history[symbol].append(best_price)
                if len(price_history[symbol]) > 300:
                    price_history[symbol].pop(0)

def start_websocket():
    from binance import ThreadedWebsocketManager
    twm = ThreadedWebsocketManager(api_key=FUTURES_API_KEY, api_secret=FUTURES_SECRET_KEY, testnet=True)
    twm.start()
    
    for symbol in COINS:
        twm.start_symbol_book_ticker_socket(callback=handle_socket_message, symbol=symbol)
    print("📡 Binance Futures WebSocket Stream Connected (No REST API calls).")

def run_concurrent_bots():
    msg = f"🚀 *Pure WebSocket 5-Strategy Bot Running* (IP Ban Protected)"
    print(msg)
    send_telegram(msg)
    
    start_websocket()
    
    scanner_thread = Thread(target=market_scanner_loop)
    scanner_thread.daemon = True
    scanner_thread.start()
    scanner_thread.join()

if __name__ == "__main__":
    bot_thread = Thread(target=run_concurrent_bots)
    bot_thread.daemon = True
    bot_thread.start()
    run_web()
