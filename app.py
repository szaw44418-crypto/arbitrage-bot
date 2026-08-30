import os
import threading
from flask import Flask

app = Flask(__name__)

@app.route('/', methods=['GET', 'HEAD'])
def health_check():
    return "Bot is alive!", 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web_server, daemon=True).start()

# ==========================================
# TRADING BOT CODE
# ==========================================

import hmac
import hashlib
import time
import requests
from urllib.parse import urlencode

# ==========================================
# 🔑 BINANCE TESTNET KEYS
# ==========================================
SPOT_BASE = "https://testnet.binance.vision"
FUTURES_BASE = "https://demo-fapi.binance.com"

SPOT_API_KEY = "kRh4ob0uDyRR4Iry7chAh7M0uvDVeN0LzzuHTf6kD3WtwkaIRWXUPIf793TkxOfB"
SPOT_SECRET_KEY = "SKazYnOkrrpeoTVfLD64gl5txFKPZV07PVhZN9vf7NcDHoyV3TQlzSBRtvR50Yso"

FUTURES_API_KEY = "L3hBSJiK4FhThiwlWbqcmNSs4s4JfmyKzOY0PRUukWCt4edRJMKr6odpoVGr0qky"
FUTURES_SECRET_KEY = "YZjGKhY7mFE6axwqEg00eXPMuLdEZscci1vjrfj5E4gBDMnSxzjxyMRisuCu3X3o"

TRADE_AMOUNT_USDT = "60"

def get_server_time():
    try:
        res = requests.get(f"{FUTURES_BASE}/fapi/v1/time", timeout=10)
        data = res.json()
        if isinstance(data, dict) and "serverTime" in data:
            return data["serverTime"]
    except Exception:
        pass
    return int(time.time() * 1000)

def scan_entire_market():
    print("\n🔍 [SCANNING ENTIRE BINANCE FUTURES MARKET...]")
    try:
        url = "https://fapi.binance.com/fapi/v1/premiumIndex"
        res = requests.get(url, timeout=10)
        all_market_data = res.json()

        if not isinstance(all_market_data, list):
            print(f"⚠️ Binance API Response Invalid: {all_market_data}")
            return "BTCUSDT", 0.0100, int(time.time() * 1000) + 3600000

        valid_market_data = [item for item in all_market_data if isinstance(item, dict)]

        sorted_market = sorted(valid_market_data, key=lambda x: float(x.get('lastFundingRate', 0)), reverse=True)
        if not sorted_market:
            return "BTCUSDT", 0.0100, int(time.time() * 1000) + 3600000

        absolute_best = sorted_market[0]
        abs_symbol = absolute_best.get('symbol', 'BTCUSDT')
        abs_rate = float(absolute_best.get('lastFundingRate', 0)) * 100
        next_funding_time = int(absolute_best.get('nextFundingTime', time.time() * 1000 + 3600000))

        print(f"🌟 [TOP COIN]: {abs_symbol} | Rate: {abs_rate:.4f}%")

        testnet_supported = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]

        if abs_symbol in testnet_supported:
            return abs_symbol, abs_rate, next_funding_time
        else:
            for item in sorted_market:
                if item.get('symbol') in testnet_supported:
                    t_rate = float(item.get('lastFundingRate', 0)) * 100
                    t_funding_time = int(item.get('nextFundingTime', time.time() * 1000 + 3600000))
                    print(f"💡 [TESTNET TARGET]: {item['symbol']} (Rate: {t_rate:.4f}%)")
                    return item['symbol'], t_rate, t_funding_time

    except Exception as e:
        print(f"⚠️ Market Scanner Warning: {e}")

    return "BTCUSDT", 0.0100, int(time.time() * 1000) + 3600000

def get_futures_qty_precision(symbol):
    try:
        res = requests.get(f"{FUTURES_BASE}/fapi/v1/exchangeInfo", timeout=10).json()
        for s in res.get('symbols', []):
            if s['symbol'] == symbol:
                return int(s.get('quantityPrecision', 3))
    except Exception:
        pass
    return 3

def get_spot_qty_precision(symbol):
    precisions = {
        "BTCUSDT": 5,
        "ETHUSDT": 4,
        "BNBUSDT": 3,
        "SOLUSDT": 2,
        "XRPUSDT": 1
    }
    return precisions.get(symbol, 2)

def send_signed_request(base_url, endpoint, api_key, secret_key, method="POST", params=None):
    if params is None:
        params = {}
    params['recvWindow'] = 60000
    params['timestamp'] = get_server_time()
    query_string = urlencode(params)
    signature = hmac.new(secret_key.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()

    url = f"{base_url}{endpoint}?{query_string}&signature={signature}"
    headers = {'X-MBX-APIKEY': api_key}
    return requests.post(url, headers=headers).json() if method == "POST" else requests.get(url, headers=headers).json()

def execute_arbitrage():
    symbol, funding_rate, next_funding_time = scan_entire_market()

    if funding_rate <= 0:
        print(f"⚠️ Funding Rate is negative/too low ({funding_rate:.4f}%). Skipping trade.")
        return False

    # 1. Spot Buy
    print(f"\n⏳ Executing Spot Buy (${TRADE_AMOUNT_USDT} USDT) on {symbol}...")
    spot_res = send_signed_request(SPOT_BASE, "/api/v3/order", SPOT_API_KEY, SPOT_SECRET_KEY, "POST", {
        'symbol': symbol, 'side': 'BUY', 'type': 'MARKET', 'quoteOrderQty': TRADE_AMOUNT_USDT
    })

    if 'orderId' not in spot_res:
        print(f"❌ [SPOT ERROR]: {spot_res}")
        return False
    print(f"✅ [SPOT SUCCESS] Order ID: {spot_res['orderId']}")

    spot_qty = float(spot_res.get('executedQty', 0))

    # 2. Futures Short
    print(f"⏳ Executing Futures Short (${TRADE_AMOUNT_USDT} USDT) on {symbol}...")
    ticker = requests.get(f"{SPOT_BASE}/api/v3/ticker/price?symbol={symbol}", timeout=10).json()
    price = float(ticker['price'])

    f_precision = get_futures_qty_precision(symbol)
    raw_qty = spot_qty if spot_qty > 0 else (float(TRADE_AMOUNT_USDT) / price)
    futures_qty = round(raw_qty, f_precision)

    futures_res = send_signed_request(FUTURES_BASE, "/fapi/v1/order", FUTURES_API_KEY, FUTURES_SECRET_KEY, "POST", {
        'symbol': symbol, 'side': 'SELL', 'type': 'MARKET', 'quantity': futures_qty
    })

    if 'orderId' in futures_res:
        print(f"✅ [FUTURES SUCCESS] Order ID: {futures_res['orderId']}")
        print(f"🎉 Position Opened for {symbol} | Rate: {funding_rate:.4f}%")
        return symbol, futures_qty, spot_qty, next_funding_time
    else:
        print(f"❌ [FUTURES ERROR]: {futures_res}")
        print("⚠️ Emergency! Selling purchased Spot assets immediately...")
        s_precision = get_spot_qty_precision(symbol)
        send_signed_request(SPOT_BASE, "/api/v3/order", SPOT_API_KEY, SPOT_SECRET_KEY, "POST", {
            'symbol': symbol, 'side': 'SELL', 'type': 'MARKET', 'quantity': round(spot_qty, s_precision)
        })
        return False

def close_positions(symbol, futures_qty, spot_qty):
    print(f"\n🔒 [CLOSING POSITIONS] Unwinding trades for {symbol}...")

    # 1. Close Futures Short (BUY back)
    f_close = send_signed_request(FUTURES_BASE, "/fapi/v1/order", FUTURES_API_KEY, FUTURES_SECRET_KEY, "POST", {
        'symbol': symbol, 'side': 'BUY', 'type': 'MARKET', 'quantity': futures_qty
    })
    print(f"📦 Futures Closed: {f_close}")

    # 2. Sell Spot Holdings
    if spot_qty <= 0:
        ticker = requests.get(f"{SPOT_BASE}/api/v3/ticker/price?symbol={symbol}", timeout=10).json()
        price = float(ticker['price'])
        spot_qty = float(TRADE_AMOUNT_USDT) / price

    s_precision = get_spot_qty_precision(symbol)
    clean_spot_qty = round(spot_qty, s_precision)
    s_close = send_signed_request(SPOT_BASE, "/api/v3/order", SPOT_API_KEY, SPOT_SECRET_KEY, "POST", {
        'symbol': symbol, 'side': 'SELL', 'type': 'MARKET', 'quantity': clean_spot_qty
    })
    print(f"📦 Spot Sold: {s_close}")
    print("✨ Arbitrage Cycle Completed Successfully!")

def start_smart_bot():
    print("="*60)
    print("🤖 CLOUD-READY FUNDING FEE SNIPING & ARBITRAGE BOT")
    print("="*60)

    while True:
        try:
            _, _, next_funding_time = scan_entire_market()
            server_time = get_server_time()

            countdown_seconds = (next_funding_time - server_time) / 1000.0
            print(f"⏳ Time to next funding: {countdown_seconds / 60:.1f} minutes remaining.")

            if countdown_seconds <= 900:
                print("🎯 Target entry window reached (15 mins left)! Executing trade...")

                trade_result = execute_arbitrage()

                if trade_result:
                    symbol, futures_qty, spot_qty, target_funding_time = trade_result

                    current_srv_time = get_server_time()
                    wait_seconds = ((target_funding_time - current_srv_time) / 1000.0) + 30

                    if wait_seconds > 0:
                        print(f"⏳ Holding position through funding fee collection (Waiting {wait_seconds / 60:.1f} mins)...")
                        time.sleep(wait_seconds)
                    else:
                        time.sleep(30)

                    close_positions(symbol, futures_qty, spot_qty)

                print("💤 Sleeping for 3 hours before next cycle preparation...")
                time.sleep(10800)
            else:
                sleep_time = min(600, (countdown_seconds - 900))
                time.sleep(max(sleep_time, 10))

        except Exception as e:
            print(f"⚠️ Main Loop Error: {e}")
            time.sleep(30)

start_smart_bot()
