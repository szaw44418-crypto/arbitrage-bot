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
    return "Binance Spot Arbitrage Bot ($100 Fixed Capital & Optimized Auto-Sweep) is active and running!"

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

# ---------------------------------------------------------
# 3. Helper Functions
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

def sync_server_time():
    try:
        server_time = client.get_server_time()
        local_time = int(time.time() * 1000)
        client.TIMESTAMP_OFFSET = server_time['serverTime'] - local_time
    except Exception as e:
        print(f"Time Sync Error: {e}")

symbol_info_cache = {}

def get_symbol_filter(symbol, filter_type):
    if symbol not in symbol_info_cache:
        try:
            symbol_info_cache[symbol] = client.get_symbol_info(symbol)
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

def emergency_rollback(asset_to_sell, target_symbol):
    try:
        time.sleep(0.5)
        bal = float(client.get_asset_balance(asset=asset_to_sell, recvWindow=60000)['free'])
        if bal > 0:
            formatted_qty = format_quantity(target_symbol, bal * 0.99)
            if formatted_qty > 0:
                client.create_order(symbol=target_symbol, side='SELL', type='MARKET', quantity=formatted_qty, recvWindow=60000)
                msg = f"🚨 *Emergency Rollback Executed:* Sold {formatted_qty} of `{asset_to_sell}` back to USDT via `{target_symbol}`"
                print(msg)
                send_telegram(msg)
    except Exception as err:
        print(f"Rollback failed for {asset_to_sell}: {err}")

def sweep_to_usdt():
    """USDT မဟုတ်သော အခြား Coin ကျန်ကြွင်း Balance များကို USDT သို့ အလိုအလျောက် ပြန်ရောင်းပေးသည်"""
    try:
        account_info = client.get_account(recvWindow=60000)
        balances = account_info.get('balances', [])
        
        for item in balances:
            asset = item['asset']
            free_bal = float(item['free'])
            
            if asset != 'USDT' and free_bal > 0:
                symbol = f"{asset}USDT"
                try:
                    ticker = float(client.get_symbol_ticker(symbol=symbol)['price'])
                    total_usdt_val = free_bal * ticker
                    
                    if total_usdt_val >= 5.0:
                        qty_to_sell = format_quantity(symbol, free_bal * 0.99)
                        if qty_to_sell > 0:
                            client.create_order(
                                symbol=symbol,
                                side='SELL',
                                type='MARKET',
                                quantity=qty_to_sell,
                                recvWindow=60000
                            )
                            print(f"🧹 Cleaned leftover balance: Sold {qty_to_sell} {asset} back to USDT")
                except Exception:
                    pass
    except Exception as e:
        print(f"Sweep Balances Error: {e}")

# ---------------------------------------------------------
# 4. Triangular Arbitrage Core Logic
# ---------------------------------------------------------
def run_arbitrage_bot():
    print("Starting Binance Spot Arbitrage Bot ($100 Fixed Capital)...")
    sync_server_time()
    
    # Bot စဖွင့်ချိန်တွင် အကောင့်ထဲရှိ Coin အဟောင်းများကို ၁ ကြိမ်သာ အပြီးရှင်းမည်
    print("🧹 Initializing account cleanup...")
    sweep_to_usdt()
    
    send_telegram("🚀 *Binance Arbitrage Bot ($100 Fixed Capital Mode) စတင်လည်ပတ်နေပါပြီ။*")
    
    FEE_FACTOR = 0.999           # Binance Spot Fee 0.1%
    MIN_PROFIT_THRESHOLD = 0.02  # Net Fee နှုတ်ပြီး အနည်းဆုံး 0.02 USDT မြတ်မှ လုပ်မည်
    TRADE_CAPITAL = 100.0        # Trade တိုင်းအတွက် $100 USDT ပုံသေ သတ်မှတ်ခြင်း

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
            
            total_usdt_balance = float(client.get_asset_balance(asset='USDT', recvWindow=60000)['free'])
            print(f"\nCurrent Total USDT Balance: {total_usdt_balance:.2f} USDT | Active Trade Capital: {TRADE_CAPITAL:.2f} USDT")

            if total_usdt_balance < TRADE_CAPITAL:
                print(f"USDT Balance မလုံလောက်ပါ။ အနည်းဆုံး {TRADE_CAPITAL} USDT ရှိရန် လိုအပ်ပါသည်။")
                time.sleep(10)
                continue

            for t in triangles:
                try:
                    ticker_base = float(client.get_symbol_ticker(symbol=t['base'])['price'])
                    ticker_cross = float(client.get_symbol_ticker(symbol=t['cross'])['price'])
                    ticker_exit = float(client.get_symbol_ticker(symbol=t['exit'])['price'])

                    raw_q1 = TRADE_CAPITAL / ticker_base
                    q1 = format_quantity(t['base'], raw_q1)
                    q1_after_fee = q1 * FEE_FACTOR

                    raw_q2 = q1_after_fee / ticker_cross
                    q2 = format_quantity(t['cross'], raw_q2)
                    q2_after_fee = q2 * FEE_FACTOR

                    estimated_usdt_back = (q2_after_fee * ticker_exit) * FEE_FACTOR
                    potential_profit = estimated_usdt_back - TRADE_CAPITAL

                    print(f"Checking Path: USDT -> {t['coin1']} -> {t['coin2']} | Capital: {TRADE_CAPITAL:.2f} | Net Est. Profit: {potential_profit:.4f} USDT")

                    if potential_profit > MIN_PROFIT_THRESHOLD:
                        start_time = time.time()
                        initial_usdt_balance = float(client.get_asset_balance(asset='USDT', recvWindow=60000)['free'])

                        print(f"⚡ Arbitrage Opportunity Found! Expected Net Profit: {potential_profit:.4f} USDT")

                        # Leg 1 Execution (BUY BTC with $100 USDT)
                        order1 = client.create_order(
                            symbol=t['base'], 
                            side='BUY', 
                            type='MARKET', 
                            quoteOrderQty=TRADE_CAPITAL, 
                            recvWindow=60000
                        )
                        print(f"[Leg 1] Executed BUY {t['base']} with {TRADE_CAPITAL} USDT")

                        # Leg 2 Execution (BUY Cross Asset using BTC balance)
                        try:
                            time.sleep(0.4)
                            actual_btc_bal = float(client.get_asset_balance(asset=t['coin1'], recvWindow=60000)['free'])
                            btc_to_spend = round(actual_btc_bal * 0.985, 8) 
                            
                            order2 = client.create_order(
                                symbol=t['cross'], 
                                side='BUY', 
                                type='MARKET', 
                                quoteOrderQty=btc_to_spend, 
                                recvWindow=60000
                            )
                            print(f"[Leg 2] Executed BUY {t['cross']} QuoteQty: {btc_to_spend} BTC")
                        except Exception as e2:
                            err_msg = f"⚠️ *Leg 2 Failed:* `{e2}`. Reverting Leg 1..."
                            print(err_msg)
                            send_telegram(err_msg)
                            emergency_rollback(t['coin1'], t['base'])
                            continue

                        # Leg 3 Execution (SELL Coin2 for USDT)
                        try:
                            time.sleep(0.4)
                            actual_coin2_bal = float(client.get_asset_balance(asset=t['coin2'], recvWindow=60000)['free'])
                            q3_formatted = format_quantity(t['exit'], actual_coin2_bal * 0.99)
                            order3 = client.create_order(symbol=t['exit'], side='SELL', type='MARKET', quantity=q3_formatted, recvWindow=60000)
                            print(f"[Leg 3] Executed SELL {t['exit']} Qty: {q3_formatted}")
                        except Exception as e3:
                            err_msg = f"⚠️ *Leg 3 Failed:* `{e3}`. Reverting Leg 2..."
                            print(err_msg)
                            send_telegram(err_msg)
                            emergency_rollback(t['coin2'], t['exit'])
                            continue

                        # Summary
                        time.sleep(0.5)
                        end_time = time.time()
                        final_usdt_balance = float(client.get_asset_balance(asset='USDT', recvWindow=60000)['free'])
                        
                        realized_profit = final_usdt_balance - initial_usdt_balance
                        profit_percentage = (realized_profit / TRADE_CAPITAL) * 100
                        duration = end_time - start_time

                        report_msg = (
                            f"📊 *Arbitrage Cycle Summary Report*\n"
                            f"----------------------------------\n"
                            f"🔄 *Trade Path:* `USDT ➔ {t['coin1']} ➔ {t['coin2']} ➔ USDT`\n"
                            f"💰 *Capital Used:* `{TRADE_CAPITAL:.2f} USDT`\n"
                            f"📈 *Expected Profit:* `+{potential_profit:.4f} USDT`\n"
                            f"💵 *Actual Net Profit:* `{realized_profit:+.4f} USDT` ({profit_percentage:+.2f}%)\n"
                            f"🏦 *New Total Balance:* `{final_usdt_balance:.2f} USDT`\n"
                            f"⏱ *Execution Time:* `{duration:.2f} seconds`\n"
                            f"----------------------------------\n"
                            f"✅ *Cycle Finished Successfully!*"
                        )
                        print(report_msg)
                        send_telegram(report_msg)

                        # Trade တစ်ခု အောင်မြင်စွာ ပြီးဆုံးသွားမှ ကျန်ခဲ့သော Coin Balance အကြွင်းအကျန်ကို ရှင်းထုတ်မည်
                        sweep_to_usdt()

                    else:
                        print("No profitable opportunity found after fees.")

                except BinanceAPIException as e:
                    err_msg = f"⚠️ *Binance Trade Error:* `{e.message}`"
                    print(err_msg)
                    send_telegram(err_msg)
                
                time.sleep(1)

        except Exception as e:
            print(f"Unexpected Loop Error: {e}")

        time.sleep(3)

# ---------------------------------------------------------
# 5. Render Web Service Launch & Background Threading Fix
# ---------------------------------------------------------
def start_bot_thread():
    bot_thread = threading.Thread(target=run_arbitrage_bot)
    bot_thread.daemon = True
    bot_thread.start()

# Global scope တွင် ခေါ်ပေးခြင်းဖြင့် Render က App ကို ပွင့်သည်နှင့် Bot Thread စတင်ပါမည်
start_bot_thread()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
