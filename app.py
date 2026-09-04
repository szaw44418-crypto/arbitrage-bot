import os
import time
import sys
import threading
import requests
from datetime import datetime
from flask import Flask

app = Flask(__name__)

def log_info(msg):
    print(msg, flush=True)
    sys.stdout.flush()

@app.route('/', methods=['GET', 'HEAD'])
def health_check():
    return "Mainnet Funding Scanner & Reporter is Running!", 200

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

threading.Thread(target=run_web_server, daemon=True).start()

SPOT_BASE = "https://api.binance.com"
FUTURES_BASE = "https://fapi.binance.com"

# 🌐 Browser အဖြစ် ဟန်ဆောင်ရန် Header ထည့်သွင်းခြင်း
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

SIMULATED_CAPITAL_USDT = float(os.environ.get("SIMULATED_CAPITAL_USDT", 100.0))
MIN_NET_PROFIT_THRESHOLD = 0.25
MIN_24H_VOLUME_USDT = 1000000

def get_all_perpetual_symbols():
    try:
        f_res = requests.get(f"{FUTURES_BASE}/fapi/v1/exchangeInfo", headers=HEADERS, timeout=10)
        f_symbols = set()
        if f_res.status_code == 200:
            for s in f_res.json().get('symbols', []):
                if s.get('contractType') == 'PERPETUAL' and s.get('status') == 'TRADING' and s['symbol'].endswith('USDT'):
                    f_symbols.add(s['symbol'])

        s_res = requests.get(f"{SPOT_BASE}/api/v3/exchangeInfo", headers=HEADERS, timeout=10)
        s_symbols = set()
        if s_res.status_code == 200:
            for s in s_res.json().get('symbols', []):
                if s.get('status') == 'TRADING' and s['symbol'].endswith('USDT'):
                    s_symbols.add(s['symbol'])

        return list(f_symbols.intersection(s_symbols))
    except Exception as e:
        log_info(f"⚠️ Symbol ဖတ်ယူမှု အမှားအယွင်း: {e}")
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]

def check_funding_history(symbol):
    try:
        res = requests.get(f"{FUTURES_BASE}/fapi/v1/fundingRate", headers=HEADERS, params={"symbol": symbol, "limit": 3}, timeout=5)
        if res.status_code == 200:
            rates = res.json()
            if rates and len(rates) >= 3:
                past_rates = [float(r['fundingRate']) for r in rates]
                return all(r > 0 for r in past_rates)
    except Exception:
        pass
    return False

def calculate_slippage(symbol):
    try:
        spot_depth = requests.get(f"{SPOT_BASE}/api/v3/depth", headers=HEADERS, params={"symbol": symbol, "limit": 5}, timeout=5).json()
        if 'asks' in spot_depth and spot_depth['asks'] and 'bids' in spot_depth and spot_depth['bids']:
            best_ask = float(spot_depth['asks'][0][0])
            best_bid = float(spot_depth['bids'][0][0])
            spread_pct = ((best_ask - best_bid) / best_ask) * 100
            return max(0.01, spread_pct / 2.0)
    except Exception:
        pass
    return 0.03

def scan_and_report_opportunities():
    log_info("\n" + "="*60)
    log_info(f"🔍 [MAINNET MARKET SCANNER] - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log_info("="*60)

    try:
        res_prem = requests.get(f"{FUTURES_BASE}/fapi/v1/premiumIndex", headers=HEADERS, timeout=10)
        
        # Status Code ကို မူတည်၍ Error ကြည့်ရန်
        if res_prem.status_code != 200:
            log_info(f"⚠️ Premium Index API ဖတ်ယူ၍ မရပါ။ (Status Code: {res_prem.status_code})")
            if res_prem.status_code == 451 or res_prem.status_code == 403:
                log_info("⛔ Binance မှ Render စာဗာ IP (US Region) ကို Futures API ကြည့်ရှုခွင့် ပိတ်ထားပါသည်။")
            return

        prem_data = {item['symbol']: item for item in res_prem.json() if isinstance(item, dict)}
        res_ticker = requests.get(f"{FUTURES_BASE}/fapi/v1/ticker/24hr", headers=HEADERS, timeout=10)
        ticker_data = {item['symbol']: item for item in res_ticker.json() if isinstance(item, dict)}

        all_symbols = get_all_perpetual_symbols()
        valid_candidates = []

        for symbol in all_symbols:
            if symbol in prem_data and symbol in ticker_data:
                p_info = prem_data[symbol]
                t_info = ticker_data[symbol]

                funding_rate = float(p_info.get('lastFundingRate', 0)) * 100
                volume_24h = float(t_info.get('quoteVolume', 0))
                next_funding_time = int(p_info.get('nextFundingTime', 0))

                if volume_24h < MIN_24H_VOLUME_USDT:
                    continue

                est_spot_fee_pct = 0.20
                est_futures_fee_pct = 0.08
                est_slippage_pct = calculate_slippage(symbol)
                total_costs_pct = est_spot_fee_pct + est_futures_fee_pct + est_slippage_pct

                expected_net_profit_pct = funding_rate - total_costs_pct

                if expected_net_profit_pct < MIN_NET_PROFIT_THRESHOLD:
                    continue

                if not check_funding_history(symbol):
                    continue

                valid_candidates.append({
                    'symbol': symbol,
                    'funding_rate': funding_rate,
                    'net_profit_pct': expected_net_profit_pct,
                    'total_costs_pct': total_costs_pct,
                    'volume_24h': volume_24h,
                    'next_funding_time': next_funding_time,
                    'mark_price': float(p_info.get('markPrice', 0))
                })

        if not valid_candidates:
            log_info("ℹ️ လက်ရှိအချိန်တွင် သတ်မှတ်ချက်ပြည့်မီသော Coin မတွေ့ရှိသေးပါ။")
            return

        valid_candidates.sort(key=lambda x: x['net_profit_pct'], reverse=True)
        log_info(f"🎯 သတ်မှတ်ချက် ပြည့်မီသော Coin ({len(valid_candidates)}) မျိုး ရှာဖွေတွေ့ရှိခဲ့သည်:\n")

        for idx, candidate in enumerate(valid_candidates, 1):
            generate_simulation_report(candidate, idx, SIMULATED_CAPITAL_USDT)

    except Exception as e:
        log_info(f"⚠️ Scanner Error: {e}")

def generate_simulation_report(candidate, rank, capital):
    symbol = candidate['symbol']
    funding_rate = candidate['funding_rate']
    net_profit_pct = candidate['net_profit_pct']
    total_costs_pct = candidate['total_costs_pct']
    volume = candidate['volume_24h']
    mark_price = candidate['mark_price']

    gross_funding_revenue = capital * (funding_rate / 100.0)
    estimated_total_cost = capital * (total_costs_pct / 100.0)
    net_estimated_profit_usdt = gross_funding_revenue - estimated_total_cost

    nft_dt = datetime.fromtimestamp(candidate['next_funding_time'] / 1000.0)
    time_str = nft_dt.strftime('%Y-%m-%d %H:%M:%S')

    log_info(f"==================================================")
    log_info(f"📊 [OPPORTUNITY REPORT #{rank}]: {symbol}")
    log_info(f"==================================================")
    log_info(f"🔹 Mark Price: {mark_price:.4f} USDT")
    log_info(f"🔹 24h Volume: ${volume:,.2f} USDT")
    log_info(f"⏰ Next Funding Time: {time_str}")
    log_info(f"--------------------------------------------------")
    log_info(f"📈 Current Funding Rate: {funding_rate:+.4f}%")
    log_info(f"💸 Est. Total Fees & Slippage: {total_costs_pct:.4f}%")
    log_info(f"🎯 EST. NET PROFIT MARGIN: {net_profit_pct:+.4f}%")
    log_info(f"--------------------------------------------------")
    log_info(f"💵 [SIMULATED P&L WITH ${capital:.0f} CAPITAL]:")
    log_info(f"   • Gross Funding Income: +${gross_funding_revenue:.4f} USDT")
    log_info(f"   • Est. Trading Costs:  -${estimated_total_cost:.4f} USDT")
    log_info(f"   • EST. NET PROFIT:     +${net_estimated_profit_usdt:.4f} USDT")
    log_info(f"==================================================\n")

def start_dry_run_bot():
    log_info("🤖 MAINNET SCANNER & REPORTING BOT STARTED (DRY-RUN MODE)")
    log_info("💡 No orders will be executed. Scanning for profitable funding coins...\n")

    while True:
        try:
            scan_and_report_opportunities()
            log_info("💤 Sleeping for 3 minutes before next scan...\n")
            time.sleep(180)
        except Exception as e:
            log_info(f"⚠️ Main Loop Error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    start_dry_run_bot()
