import os
import math
import time
import threading
from flask import Flask
from binance.client import Client
from binance.exceptions import BinanceAPIException

# ---------------------------------------------------------
# 1. Render Free Web Service အတွက် Flask Server
# ---------------------------------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Binance Arbitrage Bot is running active!"

# ---------------------------------------------------------
# 2. Binance API Setup
# ---------------------------------------------------------
API_KEY = os.environ.get("BINANCE_API_KEY", "")
API_SECRET = os.environ.get("BINANCE_API_SECRET", "")

# Testnet အသုံးပြုပါက testnet=True ထားပါ။ Real Account ဆိုပါက testnet=False ထားပါ
client = Client(API_KEY, API_SECRET, testnet=True)

# ---------------------------------------------------------
# 3. LOT_SIZE Filter Error (-1013) ပြင်ဆင်ပေးသည့် Helper Function များ
# ---------------------------------------------------------
symbol_info_cache = {}

def get_symbol_filter(symbol, filter_type):
    """Symbol ရဲ့ LOT_SIZE သို့မဟုတ် အခြား filter များကို Binance မှ ဆွဲယူပေးသည်"""
    if symbol not in symbol_info_cache:
        try:
            symbol_info_cache[symbol] = client.get_symbol_info(symbol)
        except Exception as e:
            print(f"Error fetching symbol info for {symbol}: {e}")
            return None
            
    info = symbol_info_cache.get(symbol)
    if info:
        for f in info['filters']:
            if f['filterType'] == filter_type:
                return f
    return None

def format_quantity(symbol, quantity):
    """LOT_SIZE စည်းကမ်းအတိုင်း Step Size ကို ဒသမနေရာ တိကျစွာ Round ဖြတ်ပေးသည်"""
    lot_size_filter = get_symbol_filter(symbol, 'LOT_SIZE')
    if not lot_size_filter:
        return quantity

    step_size = float(lot_size_filter['stepSize'])
    if step_size == 0:
        return quantity

    # stepSize ပေါ်မူတည်၍ Precision ဒသမနေရာ ရှာဖွေခြင်း
    precision = int(round(-math.log10(step_size)))
    
    if precision <= 0:
        return float(int(quantity))
    
    # Balance ထက် ပိုမဝယ်မိစေရန် / မရောင်းမိစေရန် math.floor ဖြင့် ဖြတ်ထုတ်ခြင်း
    factor = 10 ** precision
    return math.floor(quantity * factor) / factor

# ---------------------------------------------------------
# 4. Triangular Arbitrage Bot Logic
# ---------------------------------------------------------
def run_arbitrage_bot():
    print("Starting Binance Spot Arbitrage Bot...")
    MIN_PROFIT_THRESHOLD = 0.1  # USDT အနည်းဆုံး အမြတ်သတ်မှတ်ချက်

    while True:
        try:
            # USDT Balance စစ်ဆေးခြင်း
            usdt_balance = float(client.get_asset_balance(asset='USDT')['free'])
            print(f"\nCurrent USDT Balance: {usdt_balance:.2f}")

            if usdt_balance < 10:
                print("USDT Balance နည်းလွန်းသဖြင့် ခဏစောင့်ဆိုင်းနေပါသည်...")
                time.sleep(10)
                continue

            # စစ်ဆေးလိုသော Triangle Pair များ (ဥပမာ: USDT -> BTC -> BNB -> USDT)
            triangles = [
                {'base': 'BTCUSDT', 'cross': 'BNBBTC', 'exit': 'BNBUSDT', 'coin1': 'BTC', 'coin2': 'BNB'},
                {'base': 'BTCUSDT', 'cross': 'ETHBTC', 'exit': 'ETHUSDT', 'coin1': 'BTC', 'coin2': 'ETH'},
            ]

            for t in triangles:
                try:
                    # Market Price များ ရယူခြင်း
                    ticker_base = float(client.get_symbol_ticker(symbol=t['base'])['price'])
                    ticker_cross = float(client.get_symbol_ticker(symbol=t['cross'])['price'])
                    ticker_exit = float(client.get_symbol_ticker(symbol=t['exit'])['price'])

                    # Leg 1: USDT အားလုံးဖြင့် BTC ဝယ်ယူမည်ဟု တွက်ချက်ခြင်း
                    raw_q1 = usdt_balance / ticker_base
                    q1 = format_quantity(t['base'], raw_q1)

                    # Leg 2: ဝယ်ထားသော BTC ဖြင့် BNB ဝယ်ယူမည်ဟု တွက်ချက်ခြင်း
                    raw_q2 = q1 / ticker_cross
                    q2 = format_quantity(t['cross'], raw_q2)

                    # Leg 3: ရလာသော BNB ကို USDT သို့ ပြန်ရောင်းမည်
                    estimated_usdt_back = q2 * ticker_exit
                    potential_profit = estimated_usdt_back - usdt_balance

                    print(f"Verifying: USDT -> {t['coin1']} -> {t['coin2']}")

                    if potential_profit > MIN_PROFIT_THRESHOLD:
                        print(f"Applying Arbitrage: Potential Profit = {potential_profit:.4f} USDT")

                        # Leg 1 Execution
                        order1 = client.create_order(symbol=t['base'], side='BUY', type='MARKET', quantity=q1)
                        print(f"[Leg 1] BUY {t['base']} Volume: {q1}")

                        # Executed quantity မှ အမှန်တကယ် ရရှိခဲ့သော Balance ကို ရယူခြင်း
                        executed_btc = float(client.get_asset_balance(asset=t['coin1'])['free'])
                        q2_formatted = format_quantity(t['cross'], executed_btc / ticker_cross)

                        # Leg 2 Execution
                        order2 = client.create_order(symbol=t['cross'], side='BUY', type='MARKET', quantity=q2_formatted)
                        print(f"[Leg 2] BUY {t['cross']} Volume: {q2_formatted}")

                        # Leg 3 Execution
                        executed_coin2 = float(client.get_asset_balance(asset=t['coin2'])['free'])
                        q3_formatted = format_quantity(t['exit'], executed_coin2)

                        order3 = client.create_order(symbol=t['exit'], side='SELL', type='MARKET', quantity=q3_formatted)
                        print(f"[Leg 3] SELL {t['exit']} Volume: {q3_formatted}")
                        print("Arbitrage Execution Completed Successfully!")

                    else:
                        print(f"No arbitrage opportunity: {potential_profit:.4f} USDT")

                except BinanceAPIException as e:
                    print(f"Binance Trade Exception: {e}")
                
                time.sleep(1)

        except Exception as e:
            print(f"Unexpected Loop Error: {e}")

        time.sleep(5)

# ---------------------------------------------------------
# 5. Application Execution Entrypoint
# ---------------------------------------------------------
if __name__ == "__main__":
    # Bot ကို Thread ဖြင့် Background တွင် သီးသန့်ပတ်ခိုင်းမည်
    bot_thread = threading.Thread(target=run_arbitrage_bot)
    bot_thread.daemon = True
    bot_thread.start()

    # Render မှ Assign လုပ်ပေးမည့် PORT ဖြင့် Flask Server စတင်မည်
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
