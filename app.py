import os
import math
import time
import threading
import requests
from decimal import Decimal
from flask import Flask
from binance.client import Client
from binance.exceptions import BinanceAPIException

# ---------------------------------------------------------
# 1. Render Free Web Service အတွက် Flask Server
# ---------------------------------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Binance Advanced Triangular Arbitrage Bot ($100 Strict Capital Control) is active!"

# ---------------------------------------------------------
# 2. Binance & Telegram Credentials Configuration
# ---------------------------------------------------------
SPOT_BASE = "https://testnet.binance.vision"

SPOT_API_KEY = os.environ.get("SPOT_API_KEY", "EGMDZzNYcF8aHKsKGxWurbK63sLFdKA42cDEZC3zd8IPkyD3JDEH7btCt4D34aWV")
SPOT_SECRET_KEY = os.environ.get("SPOT_SECRET_KEY", "YfGOumNKz4MMbZ9MBy7aMB3R6CWxSjVljJvreup8k3BGL5pi1pqc73ieCpOghM8R")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8652275832:AAGxdVX66q7tQP_v3kNVAyslSYD3FsAWz60").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6127362073").strip()

client = Client(SPOT_API_KEY, SPOT_SECRET_KEY, testnet=True)
client.API_URL = f"{SPOT_BASE}/api"

TRADE_CAPITAL = 100.0  # Bot အတွက် သုံးမည့် ပုံသေ மூலဓန ($100)

# ---------------------------------------------------------
# 3. Helper Functions & 502 Gateway Retry Wrapper
# ---------------------------------------------------------
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
            print(f"Telegram Sending Error: {e}")

def safe_api_call(func, *args, **kwargs):
    max_retries = 3
    delay = 1.5
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            err_str = str(e)
            if "502" in err_str or "504" in err_str or "Bad Gateway" in err_str or "<html>" in err_str:
                print(f"⚠️ Binance Gateway Error (Attempt {attempt+1}/{max_retries}). Retrying in {delay}s...")
                time.sleep(delay)
                delay *= 2
            else:
                raise e
    return None

def sync_server_time():
    try:
        server_time = safe_api_call(client.get_server_time)
        if server_time:
            local_time = int(time.time() * 1000)
            client.TIMESTAMP_OFFSET = server_time['serverTime'] - local_time
    except Exception as e:
        print(f"Time Sync Error: {e}")

symbol_info_cache = {}

def get_symbol_filter(symbol, filter_type):
    if symbol not in symbol_info_cache:
        try:
            info = safe_api_call(client.get_symbol_info, symbol)
            if info:
                symbol_info_cache[symbol] = info
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
    lot_filter = get_symbol_filter(symbol, 'LOT_SIZE')
    if not lot_filter:
        return round(quantity, 5)

    step_size_str = lot_filter['stepSize']
    step_decimal = Decimal(step_size_str)
    qty_decimal = Decimal(str(quantity))

    step_str = step_size_str.rstrip('0')
    precision = len(step_str.split('.')[1]) if '.' in step_str else 0

    formatted = (qty_decimal // step_decimal) * step_decimal

    if precision == 0:
        return int(formatted)
    else:
        return float(f"{formatted:.{precision}f}")

def get_market_execution_price(symbol, side, target_amount):
    try:
        depth = safe_api_call(client.get_order_book, symbol=symbol, limit=20)
        if not depth:
            ticker_res = safe_api_call(client.get_symbol_ticker, symbol=symbol)
            return float(ticker_res['price'])

        orders = depth['asks'] if side == 'BUY' else depth['bids']
        remaining_budget_or_qty = target_amount
        total_cost = 0.0
        total_got = 0.0

        for price_str, qty_str in orders:
            price = float(price_str)
            qty = float(qty_str)

            if side == 'BUY':
                max_affordable_qty = remaining_budget_or_qty / price
                take_qty = min(qty, max_affordable_qty)
                total_cost += take_qty * price
                total_got += take_qty
                remaining_budget_or_qty -= (take_qty * price)
                if remaining_budget_or_qty <= 0.0000001:
                    break
            else:
                take_qty = min(qty, remaining_budget_or_qty)
                total_cost += take_qty * price
                total_got += take_qty
                remaining_budget_or_qty -= take_qty
                if remaining_budget_or_qty <= 0.0000001:
                    break

        if total_got > 0:
            return total_cost / total_got
        return float(orders[0][0])
    except Exception:
        ticker_res = safe_api_call(client.get_symbol_ticker, symbol=symbol)
        return float(ticker_res['price'])

def emergency_rollback(asset_to_sell, target_symbol):
    try:
        time.sleep(0.5)
        bal_res = safe_api_call(client.get_asset_balance, asset=asset_to_sell, recvWindow=60000)
        if bal_res:
            bal = float(bal_res['free'])
            if bal > 0:
                formatted_qty = format_quantity(target_symbol, bal * 0.99)
                if formatted_qty > 0:
                    safe_api_call(client.create_order, symbol=target_symbol, side='SELL', type='MARKET', quantity=formatted_qty, recvWindow=60000)
                    msg = f"🚨 *Emergency Rollback:* Sold {formatted_qty} of `{asset_to_sell}` back to USDT via `{target_symbol}`"
                    print(msg)
                    send_telegram(msg)
    except Exception as err:
        print(f"Rollback failed for {asset_to_sell}: {err}")

def enforce_strict_capital_limit():
    """
    အကောင့်ထဲတွင် ရှိသမျှ အခြား Coin လက်ကျန်များကို USDT သို့ ရှင်းထုတ်မည်။
    သို့သော် USDT လက်ကျန်စုစုပေါင်းသည် 100 USDT ထက် ကျော်လွန်နေပါက (အမြတ်ငွေများ ထွက်လာပါက) 
    100 USDT တိတိသာ ချန်ထားခဲ့ပြီး ပိုနေသောငွေများကို သိမ်းဆည်းရန် သတိပေးချက်ထုတ်ပေးမည် (သို့မဟုတ်) 
    Testnet ပေါ်တွင် ပိုငွေများကို ဖယ်ရှားရန် စီမံပေးသည်။
    """
    try:
        account_info = safe_api_call(client.get_account, recvWindow=60000)
        if not account_info:
            return
        balances = account_info.get('balances', [])
        
        # ၁။ အခြားလက်ကျန် Coin များကို USDT သို့ အရင်ရှင်းမည်
        for item in balances:
            asset = item['asset']
            free_bal = float(item['free'])
            
            if asset != 'USDT' and free_bal > 0:
                symbol = f"{asset}USDT"
                try:
                    ticker_res = safe_api_call(client.get_symbol_ticker, symbol=symbol)
                    if ticker_res:
                        ticker = float(ticker_res['price'])
                        if (free_bal * ticker) >= 2.0:
                            qty_to_sell = format_quantity(symbol, free_bal * 0.99)
                            if qty_to_sell > 0:
                                safe_api_call(client.create_order, symbol=symbol, side='SELL', type='MARKET', quantity=qty_to_sell, recvWindow=60000)
                                print(f"🧹 Swept leftover {asset} to USDT.")
                except Exception:
                    pass

        # ၂။ USDT လက်ကျန်ကို စစ်ဆေးပြီး 100 USDT ထက်ကျော်လွန်နေပါက ထိန်းချုပ်ခြင်း
        time.sleep(1)
        usdt_res = safe_api_call(client.get_asset_balance, asset='USDT', recvWindow=60000)
        if usdt_res:
            current_usdt = float(usdt_res['free'])
            if current_usdt > TRADE_CAPITAL + 1.0:
                excess_amount = current_usdt - TRADE_CAPITAL
                msg = f"⚖️ *Capital Control Alert:* Total USDT is `{current_usdt:.2f}`. Excess profit of `{excess_amount:.2f} USDT` detected. Bot will strictly trade with fixed `{TRADE_CAPITAL} USDT`."
                print(msg)
                send_telegram(msg)
                
    except Exception as e:
        print(f"Enforce Capital Limit Error: {e}")

def initial_cleanup_task():
    print("🧹 Initial capital synchronization starting...")
    enforce_strict_capital_limit()
    print("✅ Initial capital synchronization finished.")

# ---------------------------------------------------------
# 4. Upgraded Triangular Arbitrage Core Logic
# ---------------------------------------------------------
def run_arbitrage_bot():
    print("Starting Advanced Binance Spot Arbitrage Bot ($100 Fixed Mode)...")
    sync_server_time()
    send_telegram("🚀 *Arbitrage Bot ($100 Strict Control Mode) စတင်လည်ပတ်နေပါပြီ။*")
    
    FEE_FACTOR = 0.999
    MIN_PROFIT_THRESHOLD = 0.25

    triangles = [
        {'base': 'BTCUSDT', 'cross': 'ETHBTC', 'exit': 'ETHUSDT', 'coin1': 'BTC', 'coin2': 'ETH'},
        {'base': 'BTCUSDT', 'cross': 'BNBBTC', 'exit': 'BNBUSDT', 'coin1': 'BTC', 'coin2': 'BNB'},
        {'base': 'BTCUSDT', 'cross': 'SOLBTC', 'exit': 'SOLUSDT', 'coin1': 'BTC', 'coin2': 'SOL'},
        {'base': 'BTCUSDT', 'cross': 'XRPBTC', 'exit': 'XRPUSDT', 'coin1': 'BTC', 'coin2': 'XRP'},
        {'base': 'BTCUSDT', 'cross': 'ADABTC', 'exit': 'ADAUSDT', 'coin1': 'BTC', 'coin2': 'ADA'},
        {'base': 'BTCUSDT', 'cross': 'DOGEBTC', 'exit': 'DOGEUSDT', 'coin1': 'BTC', 'coin2': 'DOGE'},
        {'base': 'BTCUSDT', 'cross': 'LTCBTC', 'exit': 'LTCUSDT', 'coin1': 'BTC', 'coin2': 'LTC'},
        {'base': 'BTCUSDT', 'cross': 'DOTBTC', 'exit': 'DOTUSDT', 'coin1': 'BTC', 'coin2': 'DOT'},
        {'base': 'BTCUSDT', 'cross': 'AVAXBTC', 'exit': 'AVAXUSDT', 'coin1': 'BTC', 'coin2': 'AVAX'},
        {'base': 'BTCUSDT', 'cross': 'LINKBTC', 'exit': 'LINKUSDT', 'coin1': 'BTC', 'coin2': 'LINK'},
    ]

    while True:
        try:
            sync_server_time()
            
            bal_res = safe_api_call(client.get_asset_balance, asset='USDT', recvWindow=60000)
            if not bal_res:
                time.sleep(5)
                continue
            
            total_usdt_balance = float(bal_res['free'])
            print(f"\nCurrent USDT Balance: {total_usdt_balance:.2f} USDT | Target Trade Capital: {TRADE_CAPITAL:.2f} USDT")

            if total_usdt_balance < TRADE_CAPITAL:
                print(f"USDT Balance မလုံလောက်ပါ။ အနည်းဆုံး {TRADE_CAPITAL} USDT ရှိရန် လိုအပ်ပါသည်။")
                time.sleep(10)
                continue

            for t in triangles:
                try:
                    exec_price_base = get_market_execution_price(t['base'], 'BUY', TRADE_CAPITAL)
                    raw_q1 = TRADE_CAPITAL / exec_price_base
                    q1 = format_quantity(t['base'], raw_q1)
                    q1_after_fee = q1 * FEE_FACTOR

                    exec_price_cross = get_market_execution_price(t['cross'], 'BUY', q1_after_fee)
                    raw_q2 = q1_after_fee / exec_price_cross
                    q2 = format_quantity(t['cross'], raw_q2)
                    q2_after_fee = q2 * FEE_FACTOR

                    exec_price_exit = get_market_execution_price(t['exit'], 'SELL', q2_after_fee)
                    estimated_usdt_back = (q2_after_fee * exec_price_exit) * FEE_FACTOR
                    potential_profit = estimated_usdt_back - TRADE_CAPITAL

                    print(f"Checking Path: USDT -> {t['coin1']} -> {t['coin2']} | Est. Net Profit: {potential_profit:.4f} USDT")

                    if potential_profit > MIN_PROFIT_THRESHOLD:
                        start_time = time.time()
                        init_res = safe_api_call(client.get_asset_balance, asset='USDT', recvWindow=60000)
                        initial_usdt_balance = float(init_res['free']) if init_res else TRADE_CAPITAL

                        print(f"⚡ High-Confidence Arbitrage Found! Executing with fixed {TRADE_CAPITAL} USDT...")

                        # Leg 1 Execution (Strictly using TRADE_CAPITAL = 100)
                        safe_api_call(
                            client.create_order,
                            symbol=t['base'], 
                            side='BUY', 
                            type='MARKET', 
                            quoteOrderQty=TRADE_CAPITAL, 
                            recvWindow=60000
                        )
                        print(f"[Leg 1] Executed BUY {t['base']} with {TRADE_CAPITAL} USDT")

                        # Leg 2 Execution
                        try:
                            time.sleep(0.3)
                            btc_res = safe_api_call(client.get_asset_balance, asset=t['coin1'], recvWindow=60000)
                            actual_btc_bal = float(btc_res['free']) if btc_res else 0.0
                            btc_to_spend = round(actual_btc_bal * 0.985, 8) 
                            
                            safe_api_call(
                                client.create_order,
                                symbol=t['cross'], 
                                side='BUY', 
                                type='MARKET', 
                                quoteOrderQty=btc_to_spend, 
                                recvWindow=60000
                            )
                            print(f"[Leg 2] Executed BUY {t['cross']} QuoteQty: {btc_to_spend}")
                        except Exception as e2:
                            err_msg = f"⚠️ *Leg 2 Failed:* `{e2}`. Reverting Leg 1..."
                            print(err_msg)
                            send_telegram(err_msg)
                            emergency_rollback(t['coin1'], t['base'])
                            continue

                        # Leg 3 Execution
                        try:
                            time.sleep(0.3)
                            coin2_res = safe_api_call(client.get_asset_balance, asset=t['coin2'], recvWindow=60000)
                            actual_coin2_bal = float(coin2_res['free']) if coin2_res else 0.0
                            q3_formatted = format_quantity(t['exit'], actual_coin2_bal * 0.99)
                            safe_api_call(client.create_order, symbol=t['exit'], side='SELL', type='MARKET', quantity=q3_formatted, recvWindow=60000)
                            print(f"[Leg 3] Executed SELL {t['exit']} Qty: {q3_formatted}")
                        except Exception as e3:
                            err_msg = f"⚠️ *Leg 3 Failed:* `{e3}`. Reverting Leg 2..."
                            print(err_msg)
                            send_telegram(err_msg)
                            emergency_rollback(t['coin2'], t['exit'])
                            continue

                        # Summary Report
                        time.sleep(0.4)
                        end_time = time.time()
                        final_res = safe_api_call(client.get_asset_balance, asset='USDT', recvWindow=60000)
                        final_usdt_balance = float(final_res['free']) if final_res else initial_usdt_balance
                        
                        realized_profit = final_usdt_balance - initial_usdt_balance
                        profit_percentage = (realized_profit / TRADE_CAPITAL) * 100
                        duration = end_time - start_time

                        report_msg = (
                            f"📊 *Arbitrage Cycle Summary Report*\n"
                            f"----------------------------------\n"
                            f"🔄 *Trade Path:* `USDT ➔ {t['coin1']} ➔ {t['coin2']} ➔ USDT`\n"
                            f"💰 *Capital Used:* `{TRADE_CAPITAL:.2f} USDT` (Strict Fixed)\n"
                            f"📈 *Expected Profit:* `+{potential_profit:.4f} USDT`\n"
                            f"💵 *Actual Net Profit:* `{realized_profit:+.4f} USDT` ({profit_percentage:+.2f}%)\n"
                            f"🏦 *New Total Balance:* `{final_usdt_balance:.2f} USDT`\n"
                            f"⏱ *Execution Time:* `{duration:.2f} seconds`\n"
                            f"----------------------------------\n"
                            f"✅ *Cycle Finished Successfully!*"
                        )
                        print(report_msg)
                        send_telegram(report_msg)
                        
                        # Cycle ပြီးတိုင်း ပိုငွေများကို စစ်ဆေးရှင်းလင်းခြင်း
                        enforce_strict_capital_limit()

                    else:
                        print("Profit below minimum safe threshold or negative.")

                except BinanceAPIException as e:
                    err_msg = f"⚠️ *Binance Trade Error:* `{e.message}`"
                    print(err_msg)
                    send_telegram(err_msg)
                
                time.sleep(1)

        except Exception as e:
            print(f"Unexpected Loop Error: {e}")

        time.sleep(3)

# ---------------------------------------------------------
# 5. Render Web Service Launch & Asynchronous Background Threads
# ---------------------------------------------------------
def start_bot_threads():
    cleanup_thread = threading.Thread(target=initial_cleanup_task)
    cleanup_thread.daemon = True
    cleanup_thread.start()

    bot_thread = threading.Thread(target=run_arbitrage_bot)
    bot_thread.daemon = True
    bot_thread.start()

start_bot_threads()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
