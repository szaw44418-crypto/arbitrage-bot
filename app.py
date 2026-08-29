import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Render Port စစ်ဆေးမှုကို အောင်မြင်စေရန် အသေးစား Web Server ဖန်တီးခြင်း
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def start_health_check_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()

# Web Server ကို နောက်ကွယ် (Background) တွင် Run ထားခြင်း
threading.Thread(target=start_health_check_server, daemon=True).start()

# ==========================================
# အောက်တွင် သင့် မူလ Trading Bot ကုဒ်များကို ဆက်လက် ထားရှိပါ
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
    res = requests.get(f"{FUTURES_BASE}/fapi/v1/time", timeout=10)
    return res.json()["serverTime"]

def scan_entire_market():
    print("\n🔍 [SCANNING ENTIRE BINANCE FUTURES MARKET...]")
    try:
        url = "https://fapi.binance.com/fapi/v1/premiumIndex"
        all_market_data = requests.get(url, timeout=10).json()
        
        sorted_market = sorted(all_market_data, key=lambda x: float(x.get('lastFundingRate', 0)), reverse=True)
        absolute_best = sorted_market[0]
        abs_symbol = absolute_best['symbol']
        abs_rate = float(absolute_best['lastFundingRate']) * 100
        next_funding_time = int(absolute_best['nextFundingTime'])
        
        print(f"🌟 [TOP COIN]: {abs_symbol} | Rate: {abs_rate:.4f}%")
        
        testnet_supported = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
        
        if abs_symbol in testnet_supported:
            return abs_symbol, abs_rate, next_funding_time
        else:
            for item in sorted_market:
                if item['symbol'] in testnet_supported:
                    t_rate = float(item['lastFundingRate']) * 100
                    t_funding_time = int(item['nextFundingTime'])
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
    symbol, funding_rate, _ = scan_entire_market()
    
    # 1. Spot Buy
    print(f"\n⏳ Executing Spot Buy (${TRADE_AMOUNT_USDT} USDT) on {symbol}...")
    spot_res = send_signed_request(SPOT_BASE, "/api/v3/order", SPOT_API_KEY, SPOT_SECRET_KEY, "POST", {
        'symbol': symbol, 'side': 'BUY', 'type': 'MARKET', 'quoteOrderQty': TRADE_AMOUNT_USDT
    })
    
    if 'orderId' not in spot_res:
        print(f"❌ [SPOT ERROR]: {spot_res}")
        return False
    print(f"✅ [SPOT SUCCESS] Order ID: {spot_res['orderId']}")

    # 2. Futures Short
    print(f"⏳ Executing Futures Short (${TRADE_AMOUNT_USDT} USDT) on {symbol}...")
    ticker = requests.get(f"{SPOT_BASE}/api/v3/ticker/price?symbol={symbol}", timeout=10).json()
    price = float(ticker['price'])
    
    precision = get_futures_qty_precision(symbol)
    raw_qty = float(TRADE_AMOUNT_USDT) / price
    futures_qty = round(raw_qty + (10 ** (-precision)), precision)
    
    futures_res = send_signed_request(FUTURES_BASE, "/fapi/v1/order", FUTURES_API_KEY, FUTURES_SECRET_KEY, "POST", {
        'symbol': symbol, 'side': 'SELL', 'type': 'MARKET', 'quantity': futures_qty
    })
    
    if 'orderId' in futures_res:
        print(f"✅ [FUTURES SUCCESS] Order ID: {futures_res['orderId']}")
        print(f"🎉 Position Opened for {symbol} | Rate: {funding_rate:.4f}%")
        return symbol, futures_qty
    else:
        print(f"❌ [FUTURES ERROR]: {futures_res}")
        return False

def close_positions(symbol, futures_qty):
    print(f"\n🔒 [CLOSING POSITIONS] Unwinding trades for {symbol}...")
    
    # 1. Close Futures Short (BUY back)
    f_close = send_signed_request(FUTURES_BASE, "/fapi/v1/order", FUTURES_API_KEY, FUTURES_SECRET_KEY, "POST", {
        'symbol': symbol, 'side': 'BUY', 'type': 'MARKET', 'quantity': futures_qty
    })
    print(f"📦 Futures Closed: {f_close}")
    
    # 2. Sell Spot Holdings
    ticker = requests.get(f"{SPOT_BASE}/api/v3/ticker/price?symbol={symbol}", timeout=10).json()
    price = float(ticker['price'])
    spot_qty = round(float(TRADE_AMOUNT_USDT) / price, 3)
    
    s_close = send_signed_request(SPOT_BASE, "/api/v3/order", SPOT_API_KEY, SPOT_SECRET_KEY, "POST", {
        'symbol': symbol, 'side': 'SELL', 'type': 'MARKET', 'quantity': spot_qty
    })
    print(f"📦 Spot Sold: {s_close}")
    print("✨ Arbitrage Cycle Completed Successfully!")

def start_smart_bot():
    print("="*60)
    print("🤖 CLOUD-READY FUNDING FEE SNIPING & ARBITRAGE BOT")
    print("="*60)
    
    while True:
        try:
            # 1. ဈေးကွက်ကိုစကင်ဖတ်ပြီး နောက်လာမည့် Funding အချိန်ကို ယူခြင်း
            _, _, next_funding_time = scan_entire_market()
            server_time = get_server_time()
            
            # မည်မျှလိုသေးကြောင်း တွက်ချက်ခြင်း (စက္ကန့်)
            countdown_seconds = (next_funding_time - server_time) / 1000.0
            
            print(f"⏳ Time to next funding: {countdown_seconds / 60:.1f} minutes remaining.")
            
            # 2. Funding မတိုင်မီ ၁၅ မိနစ် (၉၀၀ စက္ကန့်) အလိုသို့ ရောက်ရှိလာပါက
            if countdown_seconds <= 900:
                print("🎯 Target entry window reached (15 mins left)! Executing trade...")
                
                # အခွင့်အကောင်းဆုံး Coin ဖြင့် Order ဝင်ခြင်း
                trade_result = execute_arbitrage()
                
                if trade_result:
                    symbol, futures_qty = trade_result
                    
                    # 3. Funding ဖြတ်ပြီးသည်အထိ (ဥပမာ - ၂ မိနစ်ခန့်) စောင့်ပြီး Position ပိတ်ခြင်း (အမြတ်ယူထွက်ရန်)
                    print("⏳ Holding position through funding fee collection...")
                    time.sleep(120) 
                    
                    # Position ပိတ်ခြင်း
                    close_positions(symbol, futures_qty)
                
                # လာမည့် ၄ နာရီပတ်အတွက် အလွန်အကျွံ မစစ်ဘဲ ခေတ္တရပ်နားရန်
                print("💤 Sleeping for 3 hours before next cycle preparation...")
                time.sleep(10800)
            else:
                # အချိန်အများကြီး လိုသေးပါက မိနစ်အနည်းငယ်စီ ခြား၍ စစ်ဆေးရန် (Cloud မှာ CPU မစားစေရန်)
                sleep_time = min(600, (countdown_seconds - 900))
                time.sleep(max(sleep_time, 10))
                
        except Exception as e:
            print(f"⚠️ Main Loop Error: {e}")
            time.sleep(30)

start_smart_bot()
