import os
import math
import threading
import hmac
import hashlib
import time
import sys
import requests
from datetime import datetime
from urllib.parse import urlencode
from flask import Flask

app = Flask(__name__)

# ==========================================
# 📢 INSTANT LOGGING HELPER
# ==========================================
def log_info(msg):
    """Render Terminal တွင် Log များ ချက်ချင်း ပေါ်စေရန် flush=True ဖြင့် ထုတ်ပေးသည့် Helper"""
    print(msg, flush=True)
    sys.stdout.flush()

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

TRADE_AMOUNT_USDT = 60.0
LEVERAGE = 1  

# 🛡️ UPGRADED SAFETY & STRICT PROFITABILITY THRESHOLDS
MIN_NET_PROFIT_THRESHOLD = 0.45   # 🎯 အနည်းဆုံး 0.45% အသားတင်အမြတ်ကျန်မှသာ Trade လုပ်မည် (Basis Risk & Slippage ကာမိရန်)
MIN_24H_VOLUME_USDT = 50,000      # 💧 Liquidity ကောင်းမွန်သော Coin များကိုသာ သုံးမည် ($50,000+)

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

    return requests.post(url, headers=headers).json() if method == "POST" else requests.get(url, headers=headers).json()

def set_margin_and_leverage(symbol, leverage):
    log_info(f"⚙️ Setting Margin Type to ISOLATED for {symbol}...")
    margin_res = send_signed_request(FUTURES_BASE, "/fapi/v1/marginType", FUTURES_API_KEY, FUTURES_SECRET_KEY, "POST", {
        'symbol': symbol,
        'marginType': 'ISOLATED'
    })
    if margin_res.get('code') == -4046 or margin_res.get('msg') == 'success':
        log_info(f"✅ Margin Type correctly set/verified as ISOLATED.")
    else:
        log_info(f"⚠️ Margin Type warning: {margin_res}")

    log_info(f"⚙️ Setting Leverage to {leverage}x for {symbol}...")
    lev_res = send_signed_request(FUTURES_BASE, "/fapi/v1/leverage", FUTURES_API_KEY, FUTURES_SECRET_KEY, "POST", {
        'symbol': symbol,
        'leverage': leverage
    })
    if 'leverage' in lev_res:
        log_info(f"✅ Leverage successfully set to {lev_res['leverage']}x.")
    else:
        log_info(f"⚠️ Leverage warning: {lev_res}")

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
        f_res = requests.get(f"{FUTURES_BASE}/fapi/v1/exchangeInfo", timeout=10)
        f_symbols = set()
        if f_res.status_code == 200:
            f_data = f_res.json()
            f_symbols = {
                s['symbol'] for s in f_data.get('symbols', [])
                if s.get('contractType') == 'PERPETUAL' 
                and s.get('status') == 'TRADING' 
                and s['symbol'].endswith('USDT')
                and 'BTCDOM' not in s['symbol']
            }

        s_res = requests.get(f"{SPOT_BASE}/api/v3/exchangeInfo", timeout=10)
        s_symbols = set()
        if s_res.status_code == 200:
            s_data = s_res.json()
            s_symbols = {
                s['symbol'] for s in s_data.get('symbols', [])
                if s.get('status') == 'TRADING' 
                and s['symbol'].endswith('USDT')
            }

        valid_symbols = list(f_symbols.intersection(s_symbols))

        if valid_symbols:
            return valid_symbols

    except Exception as e:
        log_info(f"⚠️ Error fetching dynamic symbols: {e}")

    return ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]

def check_funding_history(symbol):
    try:
        res = requests.get(f"{FUTURES_BASE}/fapi/v1/fundingRate", params={"symbol": symbol, "limit": 3}, timeout=5)
        if res.status_code == 200:
            rates = res.json()
            if rates and len(rates) >= 3:
                past_rates = [float(r['fundingRate']) for r in rates]
                if all(r > 0 for r in past_rates):
                    return True
    except Exception:
        pass
    return False

def simulate_slippage(symbol, volume_24h):
    try:
        spot_depth = requests.get(f"{SPOT_BASE}/api/v3/depth", params={"symbol": symbol, "limit": 5}, timeout=5).json()
        if 'asks' in spot_depth and spot_depth['asks']:
            best_ask = float(spot_depth['asks'][0][0])
            best_bid = float(spot_depth['bids'][0][0])
            spread_pct = ((best_ask - best_bid) / best_ask) * 100

            dynamic_slip = max(0.01, spread_pct / 2.0)
            if volume_24h < 20000000:
                dynamic_slip += 0.02
            return dynamic_slip
    except Exception:
        pass
    return 0.03

# 📊 Robust Execution Price Extraction Helper
def parse_fill_price(order_res, symbol, market_type="SPOT"):
    try:
        if isinstance(order_res, dict) and 'fills' in order_res and len(order_res['fills']) > 0:
            total_val = sum(float(f['price']) * float(f['qty']) for f in order_res['fills'])
            total_qty = sum(float(f['qty']) for f in order_res['fills'])
            if total_qty > 0:
                return total_val / total_qty
        if isinstance(order_res, dict) and float(order_res.get('executedQty', 0)) > 0 and float(order_res.get('cummulativeQuoteQty', 0)) > 0:
            return float(order_res.get('cummulativeQuoteQty')) / float(order_res.get('executedQty'))
        if isinstance(order_res, dict) and float(order_res.get('avgPrice', 0)) > 0:
            return float(order_res.get('avgPrice'))
    except Exception:
        pass

    try:
        if market_type == "SPOT":
            ticker = requests.get(f"{SPOT_BASE}/api/v3/ticker/price", params={"symbol": symbol}, timeout=5).json()
        else:
            ticker = requests.get(f"{FUTURES_BASE}/fapi/v1/ticker/price", params={"symbol": symbol}, timeout=5).json()
        return float(ticker.get('price', 0))
    except Exception:
        return 0.0

# 🌟 [HIGH-PROFIT TARGETED MARKET SCANNER]
def advanced_market_scanner(target_funding_time):
    log_info("\n🔍 [FULL PIPELINE SCANNING: Checking Upcoming Window Coins...]")
    try:
        res_prem = requests.get(f"{FUTURES_BASE}/fapi/v1/premiumIndex", timeout=10)
        if res_prem.status_code in [418, 429]:
            log_info(f"🚨 Rate Limited! Sleeping for 15 minutes...")
            time.sleep(900)
            return None, 0, 0, 0, 8

        if res_prem.status_code != 200:
            return None, 0, 0, 0, 8

        prem_data = {item['symbol']: item for item in res_prem.json() if isinstance(item, dict)}
        res_ticker = requests.get(f"{FUTURES_BASE}/fapi/v1/ticker/24hr", timeout=10)
        ticker_data = {item['symbol']: item for item in res_ticker.json() if isinstance(item, dict)}

        all_symbols = get_all_perpetual_symbols()
        candidates = []

        curr_time = get_futures_server_time()

        for symbol in all_symbols:
            if symbol in prem_data and symbol in ticker_data:
                p_info = prem_data[symbol]
                t_info = ticker_data[symbol]

                raw_nft = int(p_info.get('nextFundingTime', 0))
                
                # 🎯 ရှေ့လာမည့် ၂၀ မိနစ်အတွင်း Funding Fee ပေးရတော့မည့် Coin များကိုသာ သီးသန့်ရွေးချယ်မည်
                if raw_nft <= 0 or abs(raw_nft - target_funding_time) > 1200000:
                    continue

                funding_rate = float(p_info.get('lastFundingRate', 0)) * 100
                volume_24h = float(t_info.get('quoteVolume', 0))

                if volume_24h < MIN_24H_VOLUME_USDT:
                    continue

                if not check_funding_history(symbol):
                    continue

                oi_res = requests.get(f"{FUTURES_BASE}/fapi/v1/openInterest?symbol={symbol}", timeout=5)
                open_interest = float(oi_res.json().get('openInterest', 0)) if oi_res.status_code == 200 else 0.0

                estimated_total_fees = 0.16  
                estimated_slippage = simulate_slippage(symbol, volume_24h)

                expected_net_profit = funding_rate - (estimated_total_fees + estimated_slippage)

                if expected_net_profit < MIN_NET_PROFIT_THRESHOLD:
                    continue

                vol_score = min(volume_24h / 200000000.0, 1.0) * 30
                oi_score = min(open_interest / 2000000.0, 1.0) * 30
                profit_score = min(expected_net_profit, 5.0) * 40
                slippage_penalty = estimated_slippage * 10

                risk_composite_score = vol_score + oi_score + profit_score - slippage_penalty

                funding_interval = 8

                candidates.append({
                    'symbol': symbol,
                    'funding_rate': funding_rate,
                    'net_profit': expected_net_profit,
                    'volume_24h': volume_24h,
                    'open_interest': open_interest,
                    'next_funding_time': raw_nft,
                    'funding_interval': funding_interval,
                    'score': risk_composite_score
                })

        if not candidates:
            log_info("⚠️ No profit-generating coins met the criteria in this window.")
            return None, 0, 0, 0, 8

        candidates.sort(key=lambda x: x['score'], reverse=True)
        best = candidates[0]

        log_info(f"🌟 [BEST OPPORTUNITY FOUND]: {best['symbol']}")
        log_info(f"   📊 Funding Rate: {best['funding_rate']:.4f}% | Expected Net Profit: {best['net_profit']:.4f}%")
        log_info(f"   💧 Volume: ${best['volume_24h']:,.2f} | OI: {best['open_interest']:,.2f}")
        log_info(f"   🏆 Risk-Adjusted Score: {best['score']:.2f}")

        return best['symbol'], best['funding_rate'], best['net_profit'], best['next_funding_time'], best['funding_interval']

    except Exception as e:
        log_info(f"⚠️ Pipeline Scanner Error: {e}")
        time.sleep(30)

    return None, 0, 0, 0, 8

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

def execute_arbitrage(symbol, net_profit, next_funding_time, funding_rate, funding_interval):
    if net_profit < MIN_NET_PROFIT_THRESHOLD:
        log_info(f"⚠️ Net Profit ({net_profit:.4f}%) is below safety threshold ({MIN_NET_PROFIT_THRESHOLD}%).")
        return False

    log_info(f"\n⏳ Executing Spot BUY (${TRADE_AMOUNT_USDT} USDT) on {symbol}...")
    spot_res = send_signed_request(SPOT_BASE, "/api/v3/order", SPOT_API_KEY, SPOT_SECRET_KEY, "POST", {
        'symbol': symbol, 'side': 'BUY', 'type': 'MARKET', 'quoteOrderQty': str(TRADE_AMOUNT_USDT)
    })

    if 'orderId' not in spot_res:
        log_info(f"❌ [SPOT ERROR]: {spot_res}")
        return False
    log_info(f"=== [SPOT ORDER EXECUTED] === | Pair: {symbol} | Order ID: {spot_res['orderId']} | Side: BUY")

    spot_entry_price = parse_fill_price(spot_res, symbol, "SPOT")

    # ⚡ Balance ချက်ချင်း ရယူခြင်း
    actual_spot_qty = get_spot_balance(symbol)

    log_info(f"⏳ Executing Futures SHORT on {symbol}...")
    set_margin_and_leverage(symbol, LEVERAGE)

    f_precision = get_futures_qty_precision(symbol)
    futures_qty = truncate_qty(actual_spot_qty, f_precision)

    if futures_qty <= 0:
        log_info("❌ [ERROR] Futures quantity calculated as zero. Selling Spot immediately.")
        s_precision = get_spot_qty_precision(symbol)
        send_signed_request(SPOT_BASE, "/api/v3/order", SPOT_API_KEY, SPOT_SECRET_KEY, "POST", {
            'symbol': symbol, 'side': 'SELL', 'type': 'MARKET', 'quantity': truncate_qty(actual_spot_qty, s_precision)
        })
        return False

    futures_res = send_signed_request(FUTURES_BASE, "/fapi/v1/order", FUTURES_API_KEY, FUTURES_SECRET_KEY, "POST", {
        'symbol': symbol, 'side': 'SELL', 'type': 'MARKET', 'quantity': futures_qty
    })

    if 'orderId' in futures_res:
        futures_entry_price = parse_fill_price(futures_res, symbol, "FUTURES")
        entry_time = time.time()

        log_info(f"=== [FUTURES ORDER EXECUTED] === | Pair: {symbol} | Order ID: {futures_res['orderId']} | Side: SELL (SHORT) | Qty: {futures_qty}")
        log_info(f"🎉 Position Opened | Expected Net Profit: {net_profit:.4f}%")

        return {
            'symbol': symbol,
            'futures_qty': futures_qty,
            'spot_qty': actual_spot_qty,
            'next_funding_time': next_funding_time,
            'spot_entry_price': spot_entry_price,
            'futures_entry_price': futures_entry_price,
            'funding_rate': funding_rate,
            'funding_interval': funding_interval,
            'entry_time': entry_time
        }
    else:
        log_info(f"❌ [FUTURES ERROR]: {futures_res}")
        log_info("⚠️ Emergency! Selling purchased Spot assets immediately...")
        s_precision = get_spot_qty_precision(symbol)
        avail_spot = get_spot_balance(symbol)
        send_signed_request(SPOT_BASE, "/api/v3/order", SPOT_API_KEY, SPOT_SECRET_KEY, "POST", {
            'symbol': symbol, 'side': 'SELL', 'type': 'MARKET', 'quantity': truncate_qty(avail_spot, s_precision)
        })
        return False

def close_positions(trade_data):
    symbol = trade_data['symbol']
    futures_qty = trade_data['futures_qty']

    log_info(f"\n🔒 [CLOSING POSITIONS & REALIZING P&L] Unwinding trades for {symbol}...")

    f_close = send_signed_request(FUTURES_BASE, "/fapi/v1/order", FUTURES_API_KEY, FUTURES_SECRET_KEY, "POST", {
        'symbol': symbol, 'side': 'BUY', 'type': 'MARKET', 'quantity': futures_qty, 'reduceOnly': 'true'
    })
    log_info(f"=== [FUTURES CLOSE ORDER EXECUTED] === | Pair: {symbol} | Response: {f_close}")
    futures_exit_price = parse_fill_price(f_close, symbol, "FUTURES")

    s_precision = get_spot_qty_precision(symbol)
    avail_spot = get_spot_balance(symbol)
    clean_spot_qty = truncate_qty(avail_spot, s_precision)

    spot_exit_price = 0.0
    if clean_spot_qty > 0:
        s_close = send_signed_request(SPOT_BASE, "/api/v3/order", SPOT_API_KEY, SPOT_SECRET_KEY, "POST", {
            'symbol': symbol, 'side': 'SELL', 'type': 'MARKET', 'quantity': clean_spot_qty
        })
        log_info(f"=== [SPOT CLOSE ORDER EXECUTED] === | Pair: {symbol} | Response: {s_close}")
        spot_exit_price = parse_fill_price(s_close, symbol, "SPOT")
    else:
        log_info("⚠️ Available Spot Quantity was zero.")

    exit_time = time.time()
    generate_trade_report(trade_data, spot_exit_price, futures_exit_price, exit_time)

# 📝 [DETAILED TRADING REPORT GENERATOR]
def generate_trade_report(trade_data, spot_exit_price, futures_exit_price, exit_time):
    now = datetime.now()
    date_str = now.strftime('%Y-%m-%d')
    time_str = now.strftime('%H:%M:%S')

    symbol = trade_data['symbol']
    capital = TRADE_AMOUNT_USDT

    spot_entry = trade_data['spot_entry_price']
    futures_entry = trade_data['futures_entry_price']
    funding_rate = trade_data['funding_rate']
    funding_interval = f"{trade_data['funding_interval']} Hours"

    nft_dt = datetime.fromtimestamp(trade_data['next_funding_time'] / 1000.0)
    next_funding_time_str = nft_dt.strftime('%Y-%m-%d %H:%M:%S')

    # Fee Calculations (Spot ~0.1%, Futures ~0.04%)
    spot_buy_val = capital
    spot_sell_val = trade_data['spot_qty'] * spot_exit_price if spot_exit_price > 0 else capital
    spot_fee = (spot_buy_val * 0.001) + (spot_sell_val * 0.001)

    futures_entry_val = trade_data['futures_qty'] * futures_entry
    futures_exit_val = trade_data['futures_qty'] * futures_exit_price if futures_exit_price > 0 else futures_entry_val

    futures_entry_fee = futures_entry_val * 0.0004
    futures_exit_fee = futures_exit_val * 0.0004

    # Slippage Estimation
    slippage = (capital * 0.0003)

    # Funding Income
    funding_income = futures_entry_val * (funding_rate / 100.0)

    # Basis P&L
    spot_pnl = (spot_exit_price - spot_entry) * trade_data['spot_qty'] if spot_exit_price > 0 else 0.0
    futures_pnl = (futures_entry - futures_exit_price) * trade_data['futures_qty'] if futures_exit_price > 0 else 0.0
    basis_pnl = spot_pnl + futures_pnl

    # Totals
    total_cost = spot_fee + futures_entry_fee + futures_exit_fee + slippage
    net_profit = funding_income + basis_pnl - total_cost

    # Holding Duration
    duration_sec = int(exit_time - trade_data['entry_time'])
    minutes, seconds = divmod(duration_sec, 60)
    holding_time_str = f"{minutes} min {seconds} sec"

    result_str = "WIN" if net_profit > 0 else "LOSS"

    log_info("\n" + "="*50)
    log_info("📋 ARBITRAGE TRADING REPORT")
    log_info("="*50)
    log_info(f"Date: {date_str}")
    log_info(f"Time: {time_str}")
    log_info(f"Coin: {symbol}")
    log_info(f"Capital: {capital:.2f} USDT")
    log_info(f"Spot Entry: {spot_entry:.4f} USDT")
    log_info(f"Futures Entry: {futures_entry:.4f} USDT")
    log_info(f"Funding Rate: {funding_rate:+.4f}%")
    log_info(f"Funding Interval: {funding_interval}")
    log_info(f"Next Funding Time: {next_funding_time_str}")
    log_info("")
    log_info(f"Spot Fee: {spot_fee:.4f} USDT")
    log_info(f"Futures Entry Fee: {futures_entry_fee:.4f} USDT")
    log_info(f"Futures Exit Fee: {futures_exit_fee:.4f} USDT")
    log_info(f"Slippage: {slippage:.4f} USDT")
    log_info(f"Basis P&L: {basis_pnl:+.4f} USDT")
    log_info(f"Funding Income: {funding_income:+.4f} USDT")
    log_info("")
    log_info(f"TOTAL COST: {total_cost:.4f} USDT")
    log_info(f"NET PROFIT: {net_profit:+.4f} USDT")
    log_info("")
    log_info(f"Holding Time: {holding_time_str}")
    log_info(f"Result: {result_str}")
    log_info("="*50 + "\n")

def get_next_funding_countdown():
    try:
        res = requests.get(FUTURES_BASE + "/fapi/v1/premiumIndex", timeout=5)
        if res.status_code == 200:
            data = res.json()
            if data and isinstance(data, list):
                next_times = [int(item['nextFundingTime']) for item in data if item.get('nextFundingTime') and int(item.get('nextFundingTime', 0)) > 0]
                if next_times:
                    return min(next_times)
    except Exception:
        pass
    return int(time.time() * 1000) + 3600000

def start_smart_bot():
    log_info("="*60)
    log_info("🤖 HIGH-PROFIT FUNDING ARBITRAGE BOT STARTED")
    log_info("="*60)

    while True:
        try:
            server_time = get_futures_server_time()
            next_funding_time = get_next_funding_countdown()
            countdown_seconds = (next_funding_time - server_time) / 1000.0

            readable_funding_time = datetime.fromtimestamp(next_funding_time / 1000.0).strftime('%Y-%m-%d %H:%M:%S')
            log_info(f"⏳ နောက်တစ်ကြိမ် Funding Fee ကောက်ခံမည့်အချိန်: {readable_funding_time} (ကျန်ချိန်: {countdown_seconds / 60:.1f} မိနစ်)")

            scan_threshold_seconds = 900  # ၁၅ မိနစ်အလို

            if countdown_seconds > scan_threshold_seconds:
                sleep_duration = countdown_seconds - scan_threshold_seconds
                log_info(f"💤 Funding အချိန်နှင့် ၁၅ မိနစ်အလိုသို့ ရောက်ရန် {sleep_duration / 60:.1f} မိနစ် အိပ်စက်ပါမည်...")
                time.sleep(sleep_duration)
                continue

            log_info("🎯 နောက် Funding အချိန်မတိုင်မီ ၁၅ မိနစ်အလို ဝင်းဒိုးသို့ ရောက်ရှိလာပြီဖြစ်ပါသဖြင့် စျေးကွက်ကို စတင်စကင်ဖတ်ပါပြီ...")
            symbol, funding_rate, net_profit, target_funding_time, funding_interval = advanced_market_scanner(next_funding_time)

            if not symbol:
                log_info("⏳ သတ်မှတ်ထားသော အမြတ်ကျန်နိုင်ခြေရှိသည့် Coin မတွေ့ရှိသေးပါ။ ၃၀ စက္ကန့်အကြာတွင် ထပ်စမ်းမည်...")
                time.sleep(30)
                continue

            log_info("🎯 သတ်မှတ်ချက်ပြည့်မီသော Coin တွေ့ရှိပြီဖြစ်၍ အော်ဒါ စတင်လုပ်ဆောင်နေပါပြီ...")
            trade_data = execute_arbitrage(symbol, net_profit, target_funding_time, funding_rate, funding_interval)

            if trade_data:
                t_funding_time = trade_data['next_funding_time']
                
                # 🛡️ Funding အချိန်ပြီးနောက် ၄၅ စက္ကန့်အထိ စောင့်ဆိုင်းပါမည် (Spread စျေးကွက် ပြန်လည်တည်ငြိမ်ပြီးမှ Position ပိတ်ရန်)
                wait_seconds = ((t_funding_time - get_futures_server_time()) / 1000.0) + 45

                if wait_seconds > 0:
                    log_info(f"⏳ Funding Fee ကောက်ခံမည့်အချိန်အထိ Position ကို ထိန်းသိမ်းထားပါမည် ({wait_seconds / 60:.1f} မိနစ် စောင့်မည်)...")
                    time.sleep(wait_seconds)
                else:
                    time.sleep(45)

                close_positions(trade_data)
                log_info("💤 Cycle ပြီးဆုံးသွားပါပြီ။ နောက်တစ်ကြိမ်အတွက် ခေတ္တ အိပ်စက်ပါမည်...")
                time.sleep(60)
            else:
                log_info("⚠️ အော်ဒါတင်၍ မရပါ။ ၃၀ စက္ကန့်အကြာတွင် ပြန်စမ်းမည်...")
                time.sleep(30)

        except Exception as e:
            log_info(f"⚠️ Main Loop Error: {e}")
            time.sleep(30)

if __name__ == "__main__":
    start_smart_bot()
