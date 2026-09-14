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
    return "🤖 Rate-Limit Free 6-Strategy Multi-Bot is running successfully!"

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

# User Configurations
LEVERAGE = 5
TOTAL_MARGIN = 30.0  
DAILY_PROFIT_LIMIT = 5.0    
DAILY_LOSS_LIMIT = -2.0     
MAX_ACTIVE_TRADES = 3      

symbol_info_cache = {}
active_trades = {}  
latest_prices = {}
klines_cache = {}
last_kline_fetch_time = {}
last_checked_candle_time = {}

DATA_FILE = "advanced_6_strategy_data.json"
api_lock = Lock()

def load_data():
    default_strategies = {
        "Volume_Profile_POC": {"signals": 0, "win": 0, "loss": 0, "pnl": 0.0},
        "BB_Squeeze_Breakout": {"signals": 0, "win": 0, "loss": 0, "pnl": 0.0},
        "StochRSI_Pullback": {"signals": 0, "win": 0, "loss": 0, "pnl": 0.0},
        "MACD_Crossover": {"signals": 0, "win": 0, "loss": 0, "pnl": 0.0},
        "RSI_Reversal": {"signals": 0, "win": 0, "loss": 0, "pnl": 0.0},
        "EMA10_RSI_Pullback": {"signals": 0, "win": 0, "loss": 0, "pnl": 0.0}
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

def send_daily_summary():
    data = load_data()
    strategies = data.get("strategies", {})
    
    msg = "📊 *DAILY 6-STRATEGY PERFORMANCE REPORT*\n"
    msg += f"📅 Date: `{data.get('date')}`\n\n"
    
    idx = 1
    for s_name, stats in strategies.items():
        signals = stats["signals"]
        wins = stats["win"]
        losses = stats["loss"]
        total_closed = wins + losses
        win_rate = (wins / total_closed * 100) if total_closed > 0 else 0.0
        
        net_pnl_usdt = stats["pnl"]
        net_pnl_pct = (net_pnl_usdt / TOTAL_MARGIN) * 100 if TOTAL_MARGIN > 0 else 0.0
        
        sign_char = "+" if net_pnl_pct >= 0 else ""
        msg += f"*Strategy #{idx:02d} ({s_name})*\n"
        msg += f"Signals: `{signals}` | Win: `{wins}` | Loss: `{losses}`\n"
        msg += f"Win Rate: `{round(win_rate, 1)}%`\n"
        msg += f"Net P&L: `{sign_char}{round(net_pnl_pct, 1)}%` ({round(net_pnl_usdt, 2)} USDT)\n\n"
        idx += 1
        
    send_telegram(msg)

def daily_report_scheduler():
    while True:
        try:
            now = datetime.datetime.now()
            if now.hour == 23 and now.minute == 59:
                send_daily_summary()
                time.sleep(120)
        except Exception as e:
            print(f"Scheduler error: {e}")
        time.sleep(30)

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

def get_cached_klines(symbol):
    current_time = time.time()
    # Cache ကို 1800 စက္ကန့် (မိနစ် 30) အထိ သတ်မှတ်ပေးခြင်းဖြင့် Request Rate Limit ကို ကာကွယ်ပါမည်
    if symbol in klines_cache and (current_time - last_kline_fetch_time.get(symbol, 0)) < 1800:
        return klines_cache[symbol]
    
    try:
        with api_lock:
            klines = client.futures_klines(symbol=symbol, interval=Client.KLINE_INTERVAL_1HOUR, limit=250)
        if klines:
            klines_cache[symbol] = klines
            last_kline_fetch_time[symbol] = current_time
            return klines
    except Exception as e:
        print(f"Kline Fetch Error for {symbol}: {e}")
    
    return klines_cache.get(symbol, None)

def check_all_strategies_signal(symbol):
    try:
        klines = get_cached_klines(symbol)
        if not klines or len(klines) < 210: return None, 0, 0, ""
        
        last_candle_open_time = klines[-2][0]
        if last_checked_candle_time.get(symbol) == last_candle_open_time:
            return None, 0, 0, ""
        
        df = pd.DataFrame(klines, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
        ])
        
        df['open'] = df['open'].astype(float)
        df['high'] = df['high'].astype(float)
        df['low'] = df['low'].astype(float)
        df['close'] = df['close'].astype(float)
        df['volume'] = df['volume'].astype(float)
        
        df['EMA200'] = df['close'].ewm(span=200, adjust=False).mean()
        df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
        df['EMA10'] = df['close'].ewm(span=10, adjust=False).mean()
        
        df['BB_middle'] = df['close'].rolling(window=20).mean()
        df['BB_std'] = df['close'].rolling(window=20).std()
        df['BB_upper'] = df['BB_middle'] + (2 * df['BB_std'])
        df['BB_lower'] = df['BB_middle'] - (2 * df['BB_std'])
        
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).ewm(com=13, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0)).ewm(com=13, adjust=False).mean()
        df['RSI'] = 100 - (100 / (1 + (gain / loss)))
        
        stoch_rsi_window = 14
        df['RSI_min'] = df['RSI'].rolling(window=stoch_rsi_window).min()
        df['RSI_max'] = df['RSI'].rolling(window=stoch_rsi_window).max()
        df['StochRSI'] = (df['RSI'] - df['RSI_min']) / (df['RSI_max'] - df['RSI_min'] + 1e-10)
        df['StochRSI_K'] = df['StochRSI'].rolling(window=3).mean() * 100
        df['StochRSI_D'] = df['StochRSI_K'].rolling(window=3).mean()

        exp1 = df['close'].ewm(span=12, adjust=False).mean()
        exp2 = df['close'].ewm(span=26, adjust=False).mean()
        df['MACD'] = exp1 - exp2
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        
        vp_df = df.iloc[-100:].copy()
        price_bins = pd.cut(vp_df['close'], bins=20)
        poc_bin = vp_df.groupby(price_bins, observed=False)['volume'].sum().idxmax()
        poc_price = (poc_bin.left + poc_bin.right) / 2
        
        c_close = df['close'].iloc[-2]  
        c_open = df['open'].iloc[-2]
        c_high = df['high'].iloc[-2]
        c_low = df['low'].iloc[-2]
        
        p_close = df['close'].iloc[-3]  
        p_open = df['open'].iloc[-3]
        
        is_green_reversal = (p_close < p_open) and (c_close > c_open)
        is_red_reversal = (p_close > p_open) and (c_close < c_open)
        
        recent_low = df['low'].iloc[-10:-1].min()
        recent_high = df['high'].iloc[-10:-1].max()

        last_checked_candle_time[symbol] = last_candle_open_time

        if abs(c_low - poc_price) / poc_price < 0.005 and is_green_reversal:
            return "LONG", recent_low, df['EMA10'].iloc[-2], "Volume_Profile_POC"
        if abs(c_high - poc_price) / poc_price < 0.005 and is_red_reversal:
            return "SHORT", recent_high, df['EMA10'].iloc[-2], "Volume_Profile_POC"

        bb_width = (df['BB_upper'].iloc[-2] - df['BB_lower'].iloc[-2]) / df['BB_middle'].iloc[-2]
        is_squeeze = bb_width < 0.03
        
        if is_squeeze:
            if c_close > df['EMA200'].iloc[-2] and c_close > df['BB_upper'].iloc[-2]:
                return "LONG", recent_low, df['EMA10'].iloc[-2], "BB_Squeeze_Breakout"
            if c_close < df['EMA200'].iloc[-2] and c_close < df['BB_lower'].iloc[-2]:
                return "SHORT", recent_high, df['EMA10'].iloc[-2], "BB_Squeeze_Breakout"

        stoch_k = df['StochRSI_K'].iloc[-2]
        stoch_d = df['StochRSI_D'].iloc[-2]
        prev_stoch_k = df['StochRSI_K'].iloc[-3]
        prev_stoch_d = df['StochRSI_D'].iloc[-3]
        
        if c_close > df['EMA50'].iloc[-2] and (c_low <= df['EMA50'].iloc[-2] or c_close <= df['EMA10'].iloc[-2]):
            if (prev_stoch_k < prev_stoch_d) and (stoch_k > stoch_d) and stoch_k < 20 and is_green_reversal:
                return "LONG", recent_low, df['EMA10'].iloc[-2], "StochRSI_Pullback"
                
        if c_close < df['EMA50'].iloc[-2] and (c_high >= df['EMA50'].iloc[-2] or c_close >= df['EMA10'].iloc[-2]):
            if (prev_stoch_k > prev_stoch_d) and (stoch_k < stoch_d) and stoch_k > 80 and is_red_reversal:
                return "SHORT", recent_high, df['EMA10'].iloc[-2], "StochRSI_Pullback"

        macd_curr = df['MACD'].iloc[-2]
        macd_sig_curr = df['MACD_Signal'].iloc[-2]
        macd_prev = df['MACD'].iloc[-3]
        macd_sig_prev = df['MACD_Signal'].iloc[-3]

        if (macd_prev <= macd_sig_prev) and (macd_curr > macd_sig_curr) and (c_close > df['EMA50'].iloc[-2]):
            return "LONG", recent_low, df['EMA10'].iloc[-2], "MACD_Crossover"

        if (macd_prev >= macd_sig_prev) and (macd_curr < macd_sig_curr) and (c_close < df['EMA50'].iloc[-2]):
            return "SHORT", recent_high, df['EMA10'].iloc[-2], "MACD_Crossover"

        rsi_curr = df['RSI'].iloc[-2]
        rsi_prev = df['RSI'].iloc[-3]

        if (rsi_prev < 30) and (rsi_curr >= 30) and is_green_reversal:
            return "LONG", recent_low, df['EMA10'].iloc[-2], "RSI_Reversal"

        if (rsi_prev > 70) and (rsi_curr <= 70) and is_red_reversal:
            return "SHORT", recent_high, df['EMA10'].iloc[-2], "RSI_Reversal"

        ema200_val = df['EMA200'].iloc[-2]
        ema10_val = df['EMA10'].iloc[-2]

        if (c_close > ema200_val) and (c_low <= ema10_val or c_close <= ema10_val) and (30 <= rsi_curr < 40) and is_green_reversal:
            return "LONG", recent_low, ema10_val, "EMA10_RSI_Pullback"

        if (c_close < ema200_val) and (c_high >= ema10_val or c_close >= ema10_val) and (60 < rsi_curr <= 70) and is_red_reversal:
            return "SHORT", recent_high, ema10_val, "EMA10_RSI_Pullback"

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

    print(f"📡 Monitoring {symbol} {side} [{strat_name}] position...")
    while True:
        try:
            is_stopped, current_pnl = check_daily_limit()
            if is_stopped:
                close_all_positions("Daily PnL Boundary Crossed inside Monitor")
                break

            curr_price = latest_prices.get(symbol, 0.0)
            if curr_price == 0.0:
                with api_lock:
                    curr_price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
            
            klines = get_cached_klines(symbol)
            df_check = pd.DataFrame(klines, columns=[
                'open_time', 'open', 'high', 'low', 'close', 'volume',
                'close_time', 'quote_asset_volume', 'number_of_trades',
                'taker_buy_base_asset_volume', 'taker_buy_quote_asset_volume', 'ignore'
            ])
            df_check['close'] = df_check['close'].astype(float)
            
            delta = df_check['close'].diff()
            gain = (delta.where(delta > 0, 0)).ewm(com=13, adjust=False).mean()
            loss = (-delta.where(delta < 0, 0)).ewm(com=13, adjust=False).mean()
            df_check['RSI'] = 100 - (100 / (1 + (gain / loss)))
            
            last_rsi = df_check['RSI'].iloc[-2]
            last_candle_close = df_check['close'].iloc[-2]
            ema10_curr = df_check['close'].ewm(span=10, adjust=False).mean().iloc[-2]

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
    print("🔄 Rate-Limit Free 6-Strategy Market Scanner active...")
    try:
        with api_lock:
            for symbol in COINS:
                try:
                    client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
                except:
                    pass
                time.sleep(1)
    except:
        pass

    while True:
        try:
            is_stopped, current_pnl = check_daily_limit()
            if is_stopped:
                time.sleep(60)
                continue

            active_count = sum(1 for s in COINS if active_trades.get(s, False))
            if active_count >= MAX_ACTIVE_TRADES:
                time.sleep(10)
                continue

            for symbol in COINS:
                if active_trades.get(symbol, False):
                    continue

                active_count = sum(1 for s in COINS if active_trades.get(s, False))
                if active_count >= MAX_ACTIVE_TRADES:
                    break

                side, swing_val, ema10_val, strat_name = check_all_strategies_signal(symbol)
                if side:
                    record_signal(strat_name) 
                    active_trades[symbol] = True
                    curr_price = latest_prices.get(symbol, 0.0)
                    if curr_price == 0.0:
                        with api_lock:
                            curr_price = float(client.futures_symbol_ticker(symbol=symbol)['price'])
                    
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
                        f"🚀 *6-STRATEGY SIGNAL MATCHED [{strat_name}]*\n"
                        f"Pair: `{symbol}` | Side: `{side}` | Entry: `{exec_price}`\n"
                        f"TP1 (1:1): `{tp1_price}` | SL: `{stop_loss_price}`"
                    )
                    
                    t = Thread(target=monitor_trade_execution, args=(symbol, side, exec_price, tp1_price, stop_loss_price, total_qty, strat_name))
                    t.daemon = True
                    t.start()
                
                # Coin တစ်ခုချင်းစီ စစ်ဆေးအပြီးတွင် Request Weight မပိစေရန် 3 စက္ကန့်စီ ခေတ္တရပ်ပေးပါ
                time.sleep(3)
                
        except Exception as e:
            print(f"Error in scanner loop: {e}")
        
        time.sleep(30)

def handle_socket_message(msg):
    if msg.get('e') == 'bookTicker':
        symbol = msg.get('s')
        best_price = float(msg.get('b', 0))
        if symbol in COINS and best_price > 0:
            latest_prices[symbol] = best_price

def start_websocket():
    from binance import ThreadedWebsocketManager
    twm = ThreadedWebsocketManager(api_key=FUTURES_API_KEY, api_secret=FUTURES_SECRET_KEY, testnet=True)
    twm.start()
    
    for symbol in COINS:
        twm.start_symbol_book_ticker_socket(callback=handle_socket_message, symbol=symbol)
    print("📡 Binance Futures WebSocket Stream Connected.")

def run_concurrent_bots():
    msg = f"🚀 *Rate-Limit Free 6-Strategy Bot Running* (Strategies: POC, BB Squeeze, StochRSI, MACD, RSI Reversal, 10EMA Pullback)"
    print(msg)
    send_telegram(msg)
    
    start_websocket()
    
    scheduler_thread = Thread(target=daily_report_scheduler)
    scheduler_thread.daemon = True
    scheduler_thread.start()
    
    scanner_thread = Thread(target=market_scanner_loop)
    scanner_thread.daemon = True
    scanner_thread.start()
    scanner_thread.join()

if __name__ == "__main__":
    bot_thread = Thread(target=run_concurrent_bots)
    bot_thread.daemon = True
    bot_thread.start()
    run_web()
