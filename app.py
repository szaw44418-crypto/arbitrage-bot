import os
import math
import threading
import hmac
import hashlib
import time
import requests
from urllib.parse import urlencode
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
# 🔑 BINANCE API KEYS
# ==========================================
SPOT_BASE = "https://testnet.binance.vision"
FUTURES_BASE = "https://demo-fapi.binance.com"

SPOT_API_KEY = os.environ.get("SPOT_API_KEY", "EGMDZzNYcF8aHKsKGxWurbK63sLFdKA42cDEZC3zd8IPkyD3JDEH7btCt4D34aWV")
SPOT_SECRET_KEY = os.environ.get("SPOT_SECRET_KEY", "YfGOumNKz4MMbZ9MBy7aMB3R6CWxSjVljJvreup8k3BGL5pi1pqc73ieCpOghM8R")

FUTURES_API_KEY = os.environ.get("FUTURES_API_KEY", "TGSwnTW3ukJ7z8fXKeZd4Iz6MBttW6bRA2ODX5rwXC90YWsv5srgcwcL7Bl8XQeA")
FUTURES_SECRET_KEY = os.environ.get("FUTURES_SECRET_KEY", "b64gEodONh8DMFPsX7Kaj1QRhGdgRM8iCYy8gVPVAO8VNAzWL88DmvZhrVE330Ed")

TRADE_AMOUNT_USDT = 100.0
LEVERAGE = 1  

# 🛡️ Advanced Safety & Profit Thresholds
MIN_NET_PROFIT_THRESHOLD = 0.15  
MIN_24H_VOLUME_USDT = 5000000    

def truncate_qty(qty, precision):
    if precision == 0:
        return float(int(qty))
    qty_str = f"{float(qty):.10f}"
    idx = qty_str.find('.')
    if idx == -1:
        return float(qty)
    return float(qty_str[:idx + precision + 1])

def get_futures_server_time():
    try:
        res = requests.get(f"{FUTURES_BASE}/fapi/v1/time", timeout=10)
        data = res.json()
        if isinstance(data, dict) and "serverTime" in data:
            return data["serverTime"]
    except Exception:
        pass
    return int(time.time() * 1000)

def get_spot_server_time():
    try:
        res = requests.get(f"{SPOT_BASE}/api/v3/time", timeout=10)
        data = res.json()
        if isinstance(data, dict) and "serverTime" in data:
            return data["serverTime"]
    except Exception:
        pass
    return int(time.time() * 1000)

def send_signed_request(base_url, endpoint, api_key, secret_key, method="POST", params=None):
    if params is None:
        params = {}
    params['recvWindow'] = 60000
    
    if base_url == SPOT_BASE:
        params['timestamp'] = get_spot_server_time()
    else:
        params['timestamp'] = get_futures_server_time()
        
    query_string = urlencode(params)
    signature = hmac.new(secret_key.encode('utf-8'), query_string.encode('utf-8'), hashlib.sha256).hexdigest()

    url = f"{base_url}{endpoint}?{query_string}&signature={signature}"
    headers = {'X-MBX-APIKEY': api_key}
    
    time.sleep(0.2)
    return requests.post(url, headers=headers).json() if method == "POST" else requests.get(url, headers=headers).json()

def set_margin_and_leverage(symbol, leverage):
    print(f"⚙️ Setting Margin Type to ISOLATED for {symbol}...")
    margin_res = send_signed_request(FUTURES_BASE, "/fapi/v1/marginType", FUTURES_API_KEY, FUTURES_SECRET_KEY, "POST", {
        'symbol': symbol,
        'marginType': 'ISOLATED'
    })
    if margin_res.get('code') == -4046 or margin_res.get('msg') == 'success':
        print(f"✅ Margin Type correctly set/verified as ISOLATED.")
    else:
        print(f"⚠️ Margin Type warning: {margin_res}")

    print(f"⚙️ Setting Leverage to {leverage}x for {symbol}...")
    lev_res = send_signed_request(FUTURES_BASE, "/fapi/v1/leverage", FUTURES_API_KEY, FUTURES_SECRET_KEY, "POST", {
        'symbol': symbol,
        'leverage': leverage
    })
    if 'leverage' in lev_res:
        print(f"✅ Leverage successfully set to {lev_res['leverage']}x.")
    else:
        print(f"⚠️ Leverage warning: {lev_res}")

def get_spot_balance(symbol):
    asset = symbol.replace("USDT", "")
    try:
        res = send_signed_request(SPOT_BASE, "/api/v3/account", SPOT_API_KEY, SPOT_SECRET_KEY, "GET")
        for b in res.get('balances', []):
            if b.get('asset') == asset:
                return float(b.get('free', 0))
    except Exception:
        pass
    return 0.0

def get_all_perpetual_symbols():
    try:
        res = requests.get(f"{FUTURES_BASE}/fapi/v1/exchangeInfo", timeout=10)
        if res.status_code == 200:
            data = res.json()
            symbols = [
                s['symbol'] for s in data.get('symbols', [])
                if s.get('contractType') == 'PERPETUAL' and s.get('status') == 'TRADING' and s['symbol'].endswith('USDT')
            ]
            return symbols
    except Exception as e:
        print(f"⚠️ Error fetching dynamic symbols: {e}")
    return ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]

# 📊 [PIPELINE STEP: Funding History & Trend Check]
def check_funding_history(symbol):
    try:
        res = requests.get(f"{FUTURES_BASE}/fapi/v1/fundingRate", params={"symbol": symbol, "limit": 3}, timeout=5)
        if res.status_code == 200:
            rates = res.json()
            if rates and len(rates) >= 3:
                # ပြီးခဲ့သည့် ၃ ကြိမ်စလုံး အပေါင်းလက္ခဏာ (Positive) ဖြစ်မဖြစ် စစ်ဆေးခြင်း
                past_rates = [float(r['fundingRate']) for r in rates]
                if all(r > 0 for r in past_rates):
                    return True
    except Exception:
        pass
    return False # မသေချာပါက ကျော်မည်

# 💧 [PIPELINE STEP: Dynamic Slippage & Order Book Depth Simulation]
def simulate_slippage(symbol, volume_24h):
    try:
        spot_depth = requests.get(f"{SPOT_BASE}/api/v3/depth", params={"symbol": symbol, "limit": 5}, timeout=5).json()
        if 'asks' in spot_depth and spot_depth['asks']:
            best_ask = float(spot_depth['asks'][0][0])
            best_bid = float(spot_depth['bids'][0][0])
            spread_pct = ((best_ask - best_bid) / best_ask) * 100
            
            # Volume ပမာဏအပေါ်မူတည်၍ Slippage ကို တက်ကြွစွာ တွက်ချက်ခြင်း
            dynamic_slip = max(0.01, spread_pct / 2.0)
            if volume_24h < 20000000:
                dynamic_slip += 0.02
            return dynamic_slip
    except Exception:
        pass
    return 0.03 # Default fallback slip buffer

# 🌟 [ADVANCED FULL-PIPELINE MARKET SCANNER & RISK SCORING]
def advanced_market_scanner():
    print("\n🔍 [FULL PIPELINE SCANNING: History, Depth, Fees & Risk Scoring...]")
    try:
        res_prem = requests.get(f"{FUTURES_BASE}/fapi/v1/premiumIndex", timeout=10)
        if res_prem.status_code in [418, 429]:
            print(f"🚨 Rate Limited! Sleeping for 15 minutes...")
            time.sleep(900)
            return None, 0, 0, 0
            
        if res_prem.status_code != 200:
            return None, 0, 0, 0
            
        prem_data = {item['symbol']: item for item in res_prem.json() if isinstance(item, dict)}
        res_ticker = requests.get(f"{FUTURES_BASE}/fapi/v1/ticker/24hr", timeout=10)
        ticker_data = {item['symbol']: item for item in res_ticker.json() if isinstance(item, dict)}

        all_symbols = get_all_perpetual_symbols()
        candidates = []

        for symbol in all_symbols:
            if symbol in prem_data and symbol in ticker_data:
                p_info = prem_data[symbol]
                t_info = ticker_data[symbol]

                funding_rate = float(p_info.get('lastFundingRate', 0)) * 100
                next_funding_time = int(p_info.get('nextFundingTime', time.time() * 1000 + 3600000))
                volume_24h = float(t_info.get('quoteVolume', 0))

                # 1. Volume Filter
                if volume_24h < MIN_24H_VOLUME_USDT:
                    continue

                # 2. Funding History Check (Trend Verification)
                if not check_funding_history(symbol):
                    continue

                # 3. Open Interest (OI) Check
                oi_res = requests.get(f"{FUTURES_BASE}/fapi/v1/openInterest?symbol={symbol}", timeout=5)
                open_interest = float(oi_res.json().get('openInterest', 0)) if oi_res.status_code == 200 else 0.0

                # 4. Simulation & All Fees Calculation
                estimated_total_fees = 0.16  # Spot & Futures Maker/Taker Fees
                estimated_slippage = simulate_slippage(symbol, volume_24h)
                
                expected_net_profit = funding_rate - (estimated_total_fees + estimated_slippage)

                # 5. Net Profit Filter
                if expected_net_profit < MIN_NET_PROFIT_THRESHOLD:
                    continue

                # 6. Advanced Risk Score Calculation
                vol_score = min(volume_24h / 200000000.0, 1.0) * 30
                oi_score = min(open_interest / 2000000.0, 1.0) * 30
                profit_score = min(expected_net_profit, 5.0) * 30
                slippage_penalty = estimated_slippage * 10

                risk_composite_score = vol_score + oi_score + profit_score - slippage_penalty

                candidates.append({
                    'symbol': symbol,
                    'funding_rate': funding_rate,
                    'net_profit': expected_net_profit,
                    'volume_24h': volume_24h,
                    'open_interest': open_interest,
                    'next_funding_time': next_funding_time,
                    'score': risk_composite_score
                })

        if not candidates:
            print("⚠️ No coins met the strict pipeline criteria right now.")
            return None, 0, 0, 0

        candidates.sort(key=lambda x: x['score'], reverse=True)
        best = candidates[0]

        print(f"🌟 [BEST OPPORTUNITY FOUND]: {best['symbol']}")
        print(f"   📊 Funding Rate: {best['funding_rate']:.4f}% | Expected Net Profit: {best['net_profit']:.4f}%")
        print(f"   💧 Volume: ${best['volume_24h']:,.2f} | OI: {best['open_interest']:,.2f}")
        print(f"   🏆 Risk-Adjusted Score: {best['score']:.2f}")

        return best['symbol'], best['funding_rate'], best['net_profit'], best['next_funding_time']

    except Exception as e:
        print(f"⚠️ Pipeline Scanner Error: {e}")
        time.sleep(30)

    return None, 0, 0, 0

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
    try:
        res = requests.get(f"{SPOT_BASE}/api/v3/exchangeInfo", timeout=10).json()
        for s in res.get('symbols', []):
            if s['symbol'] == symbol:
                for f in s.get('filters', []):
                    if f.get('filterType') == 'LOT_SIZE':
                        step_size = f.get('stepSize', '0.01')
                        if '.' in step_size:
                            return len(step_size.split('.')[1].rstrip('0'))
                        return 0
    except Exception:
        pass
    return 2

def execute_arbitrage(symbol, net_profit, next_funding_time):
    if net_profit < MIN_NET_PROFIT_THRESHOLD:
        print(f"⚠️ Net Profit ({net_profit:.4f}%) is below safety threshold.")
        return False

    print(f"\n⏳ Executing Spot BUY (${TRADE_AMOUNT_USDT} USDT) on {symbol}...")
    spot_res = send_signed_request(SPOT_BASE, "/api/v3/order", SPOT_API_KEY, SPOT_SECRET_KEY, "POST", {
        'symbol': symbol, 'side': 'BUY', 'type': 'MARKET', 'quoteOrderQty': str(TRADE_AMOUNT_USDT)
    })

    if 'orderId' not in spot_res:
        print(f"❌ [SPOT ERROR]: {spot_res}")
        return False
    print(f"✅ [SPOT SUCCESS] Order ID: {spot_res['orderId']}")

    time.sleep(1)
    actual_spot_qty = get_spot_balance(symbol)

    print(f"⏳ Executing Futures SHORT on {symbol}...")
    set_margin_and_leverage(symbol, LEVERAGE)
    
    f_precision = get_futures_qty_precision(symbol)
    futures_qty = truncate_qty(actual_spot_qty, f_precision)

    if futures_qty <= 0:
        print("❌ [ERROR] Futures quantity calculated as zero. Selling Spot immediately.")
        s_precision = get_spot_qty_precision(symbol)
        send_signed_request(SPOT_BASE, "/api/v3/order", SPOT_API_KEY, SPOT_SECRET_KEY, "POST", {
            'symbol': symbol, 'side': 'SELL', 'type': 'MARKET', 'quantity': truncate_qty(actual_spot_qty, s_precision)
        })
        return False

    futures_res = send_signed_request(FUTURES_BASE, "/fapi/v1/order", FUTURES_API_KEY, FUTURES_SECRET_KEY, "POST", {
        'symbol': symbol, 'side': 'SELL', 'type': 'MARKET', 'quantity': futures_qty
    })

    if 'orderId' in futures_res:
        print(f"✅ [FUTURES SUCCESS] Order ID: {futures_res['orderId']}")
        print(f"🎉 Position Opened | Expected Net Profit: {net_profit:.4f}%")
        return symbol, futures_qty, actual_spot_qty, next_funding_time
    else:
        print(f"❌ [FUTURES ERROR]: {futures_res}")
        print("⚠️ Emergency! Selling purchased Spot assets immediately...")
        s_precision = get_spot_qty_precision(symbol)
        avail_spot = get_spot_balance(symbol)
        send_signed_request(SPOT_BASE, "/api/v3/order", SPOT_API_KEY, SPOT_SECRET_KEY, "POST", {
            'symbol': symbol, 'side': 'SELL', 'type': 'MARKET', 'quantity': truncate_qty(avail_spot, s_precision)
        })
        return False

def close_positions(symbol, futures_qty, spot_qty):
    print(f"\n🔒 [CLOSING POSITIONS & REALIZING P&L] Unwinding trades for {symbol}...")

    f_close = send_signed_request(FUTURES_BASE, "/fapi/v1/order", FUTURES_API_KEY, FUTURES_SECRET_KEY, "POST", {
        'symbol': symbol, 'side': 'BUY', 'type': 'MARKET', 'quantity': futures_qty, 'reduceOnly': 'true'
    })
    print(f"📦 Futures Closed: {f_close}")

    s_precision = get_spot_qty_precision(symbol)
    avail_spot = get_spot_balance(symbol)
    clean_spot_qty = truncate_qty(avail_spot, s_precision)

    if clean_spot_qty > 0:
        s_close = send_signed_request(SPOT_BASE, "/api/v3/order", SPOT_API_KEY, SPOT_SECRET_KEY, "POST", {
            'symbol': symbol, 'side': 'SELL', 'type': 'MARKET', 'quantity': clean_spot_qty
        })
        print(f"📦 Spot Sold: {s_close}")
    else:
        print("⚠️ Available Spot Quantity was zero.")

    print("✨ Arbitrage Cycle Completed Successfully & P&L Realized!")

def start_smart_bot():
    print("="*60)
    print("🤖 FULL PIPELINE ARBITRAGE BOT STARTED")
    print("="*60)

    while True:
        try:
            symbol, funding_rate, net_profit, next_funding_time = advanced_market_scanner()
            
            if not symbol:
                print("⏳ No target found. Retrying scan in 60 seconds...")
                time.sleep(60)
                continue

            server_time = get_futures_server_time()
            countdown_seconds = (next_funding_time - server_time) / 1000.0
            print(f"⏳ Time to next funding for {symbol}: {countdown_seconds / 60:.1f} minutes remaining.")

            if countdown_seconds <= 900: # 15 မိနစ်အလို Entry Window
                print("🎯 Target entry window reached! Executing pipeline trade...")

                trade_result = execute_arbitrage(symbol, net_profit, next_funding_time)

                if trade_result:
                    symbol, futures_qty, spot_qty, target_funding_time = trade_result

                    current_srv_time = get_futures_server_time()
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
                    print("⚠️ Trade skipped or failed. Retrying in 60 seconds...")
                    time.sleep(60)
            else:
                sleep_time = min(600, max(180, (countdown_seconds - 900)))
                print(f"💤 Far from entry window. Sleeping for {sleep_time / 60:.1f} minutes...")
                time.sleep(sleep_time)

        except Exception as e:
            print(f"⚠️ Main Loop Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    start_smart_bot()
