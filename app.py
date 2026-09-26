import requests
from flask import Flask, render_template_string
from concurrent.futures import ThreadPoolExecutor

app = Flask(__name__)

# Binance Futures REST API Endpoints
KLINE_URL = "https://fapi.binance.com/fapi/v1/klines"
OI_HIST_URL = "https://fapi.binance.com/futures/data/openInterestHist"
TICKER_24H_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"

# Crypto Sector အလိုက် Coins များ (ထပ်မံတိုးချဲ့ထားသည်)
SECTOR_MAP = {
    'Layer 1 / L1': ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'SUIUSDT', 'ADAUSDT', 'AVAXUSDT', 'APTUSDT', 'SEIUSDT', 'TONUSDT', 'DOTUSDT', 'NEARUSDT', 'TRXUSDT', 'BCHUSDT', 'LTCUSDT', 'XRPUSDT', 'ATOMUSDT', 'FTMUSDT', 'INJUSDT'],
    'AI & Big Data': ['NEARUSDT', 'RENDERUSDT', 'FETUSDT', 'TAOUSDT', 'GRTUSDT', 'WLDUSDT', 'ARKMUSDT', 'AGIXUSDT', 'THETAUSDT', 'AKTUSDT'],
    'DeFi & DEX': ['UNIUSDT', 'AAVEUSDT', 'PENDLEUSDT', 'ENAUSDT', 'MKRUSDT', 'CRVUSDT', 'LDOUSDT', 'SNXUSDT', 'COMPUSDT', 'JUPUSDT', 'RAYUSDT', 'DYDXUSDT'],
    'Meme Coins': ['DOGEUSDT', 'PEPEUSDT', 'SHIBUSDT', 'WIFUSDT', 'BONKUSDT', 'FLOKIUSDT', 'MEMEUSDT', 'BOMEUSDT', 'POPCATUSDT', 'NEIROUSDT'],
    'Layer 2 / L2': ['OPUSDT', 'ARBUSDT', 'STRKUSDT', 'POLUSDT', 'IMXUSDT', 'MANTAUSDT', 'METISUSDT', 'ZKUSDT'],
    'Gaming & Meta': ['GALAUSDT', 'AXSUSDT', 'SANDUSDT', 'MANAUSDT', 'BEAMXUSDT', 'PIXELUSDT', 'YGGUSDT'],
    'RWA & Storage': ['ONDOUSDT', 'OMUSDT', 'FILUSDT', 'ARUSDT', 'LINKUSDT', 'TIAUSDT'],
    'TradFi & Commodities': ['XAUUSDT', 'XAGUSDT', 'CLUSDT', 'SOXLUSDT']
}

def format_number(num):
    """ ကိန်းဂဏန်းများကို M (Million), B (Billion) ဖြင့် ပြသခြင်း """
    if num >= 1e9:
        return f"${num/1e9:.2f}B"
    elif num >= 1e6:
        return f"${num/1e6:.1f}M"
    else:
        return f"${num:,.2f}"

def get_all_futures_tickers():
    """ Binance Futures ဒေတာ အားလုံး ရယူခြင်း """
    try:
        res = requests.get(TICKER_24H_URL, timeout=10).json()
        return {item['symbol']: item for item in res if item['symbol'].endswith('USDT')}
    except Exception as e:
        print(f"Error fetching tickers: {e}")
        return {}

def calculate_sector_flow(tickers_dict):
    sector_summary = []
    for sector_name, symbols in SECTOR_MAP.items():
        total_vol = 0.0
        change_sum = 0.0
        count = 0
        top_gainer_symbol = "-"
        max_change = -999.0
        
        for sym in symbols:
            if sym in tickers_dict:
                t = tickers_dict[sym]
                vol = float(t['quoteVolume'])
                chg = float(t['priceChangePercent'])
                
                total_vol += vol
                change_sum += chg
                count += 1
                
                if chg > max_change:
                    max_change = chg
                    top_gainer_symbol = f"{sym.replace('USDT','')} ({chg:+.1f}%)"
                    
        avg_change = (change_sum / count) if count > 0 else 0.0
        sector_summary.append({
            'name': sector_name,
            'vol_formatted': format_number(total_vol),
            'vol': total_vol,
            'avg_change': avg_change,
            'top_gainer': top_gainer_symbol
        })
        
    return sorted(sector_summary, key=lambda x: x['vol'], reverse=True)

def calculate_ema(data, period):
    if len(data) < period:
        return []
    alpha = 2 / (period + 1)
    ema = [sum(data[:period]) / period]
    for val in data[period:]:
        ema.append((val * alpha) + (ema[-1] * (1 - alpha)))
    return ema

def calculate_rsi(closes, period=14):
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    if len(gains) < period:
        return []
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi = [100 - (100 / (1 + (avg_gain / (avg_loss if avg_loss != 0 else 1e-10))))]
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi.append(100 - (100 / (1 + (avg_gain / (avg_loss if avg_loss != 0 else 1e-10)))))
    return rsi

def calculate_obv(closes, volumes):
    obv = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i-1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    return obv

def calculate_dmi(highs, lows, closes, period=14):
    tr_list, dmp_list, dmn_list = [], [], []
    for i in range(1, len(closes)):
        h, l, p_c = highs[i], lows[i], closes[i-1]
        tr = max(h - l, abs(h - p_c), abs(l - p_c))
        up_move = h - highs[i-1]
        down_move = lows[i-1] - l
        dmp = up_move if (up_move > down_move and up_move > 0) else 0
        dmn = down_move if (down_move > up_move and down_move > 0) else 0
        tr_list.append(tr)
        dmp_list.append(dmp)
        dmn_list.append(dmn)
    if len(tr_list) < period:
        return None, None
    smooth_tr = sum(tr_list[:period])
    smooth_dmp = sum(dmp_list[:period])
    smooth_dmn = sum(dmn_list[:period])
    for i in range(period, len(tr_list)):
        smooth_tr = smooth_tr - (smooth_tr / period) + tr_list[i]
        smooth_dmp = smooth_dmp - (smooth_dmp / period) + dmp_list[i]
        smooth_dmn = smooth_dmn - (smooth_dmn / period) + dmn_list[i]
    pos_di = 100 * (smooth_dmp / smooth_tr) if smooth_tr != 0 else 0
    neg_di = 100 * (smooth_dmn / smooth_tr) if smooth_tr != 0 else 0
    return pos_di, neg_di

def get_timeframe_trend(symbol, interval):
    try:
        res = requests.get(KLINE_URL, params={'symbol': symbol, 'interval': interval, 'limit': 30}, timeout=4).json()
        if not isinstance(res, list) or len(res) < 25:
            return "NEUTRAL"
        closes = [float(k[4]) for k in res]
        ema7 = calculate_ema(closes, 7)
        ema25 = calculate_ema(closes, 25)
        if ema7 and ema25:
            if closes[-1] > ema7[-1] > ema25[-1]:
                return "BULLISH"
            elif closes[-1] < ema7[-1] < ema25[-1]:
                return "BEARISH"
    except Exception:
        pass
    return "NEUTRAL"

def get_oi_change(symbol):
    try:
        res = requests.get(OI_HIST_URL, params={'symbol': symbol, 'period': '1h', 'limit': 2}, timeout=4).json()
        if isinstance(res, list) and len(res) >= 2:
            prev_oi = float(res[0]['sumOpenInterest'])
            curr_oi = float(res[1]['sumOpenInterest'])
            if prev_oi > 0:
                return round(((curr_oi - prev_oi) / prev_oi) * 100, 2)
    except Exception:
        pass
    return 0.0

def process_symbol_data(ticker):
    symbol = ticker['symbol']
    price = float(ticker['lastPrice'])
    price_change = float(ticker['priceChangePercent'])
    volume_24h = float(ticker['quoteVolume'])
    
    # Volume အလွန်နည်းသော Coin များကို ခေတ္တချန်လှပ်၍ Speed မြှင့်ခြင်း (ဥပမာ $1M Volume အောက်)
    if volume_24h < 1000000:
        return None
    
    try:
        res = requests.get(KLINE_URL, params={'symbol': symbol, 'interval': '1h', 'limit': 100}, timeout=5).json()
        if not isinstance(res, list) or len(res) < 60:
            return None
            
        highs = [float(k[2]) for k in res]
        lows = [float(k[3]) for k in res]
        closes = [float(k[4]) for k in res]
        volumes = [float(k[5]) for k in res]
        
        rsi_vals = calculate_rsi(closes, 14)
        rsi_ema_vals = calculate_ema(rsi_vals, 9) if rsi_vals else []
        obv_vals = calculate_obv(closes, volumes)
        obv_ema50_vals = calculate_ema(obv_vals, 50) if obv_vals else []
        pos_di, neg_di = calculate_dmi(highs, lows, closes, 14)
        oi_change_pct = get_oi_change(symbol)
        
        tf_15m = get_timeframe_trend(symbol, '15m')
        tf_1h = get_timeframe_trend(symbol, '1h')
        tf_4h = get_timeframe_trend(symbol, '4h')
        
        if not rsi_vals or not rsi_ema_vals or not obv_ema50_vals or pos_di is None:
            return None
            
        rsi_ema_check = rsi_vals[-1] > rsi_ema_vals[-1]
        rsi_50_check = rsi_vals[-1] > 50
        obv_check = obv_vals[-1] > obv_ema50_vals[-1]
        oi_rising_check = oi_change_pct > 0
        dmi_state = "YES" if pos_di > neg_di else ("NO" if pos_di < neg_di else "FLAT")

        bull_checks = [dmi_state == "YES", rsi_ema_check, rsi_50_check, obv_check, oi_rising_check]
        bear_checks = [dmi_state == "NO", not rsi_ema_check, not rsi_50_check, not obv_check, oi_rising_check]

        return {
            'symbol': symbol,
            'price': f"{price:g}",
            'price_change': price_change,
            'vol_formatted': format_number(volume_24h),
            'tf_15m': tf_15m,
            'tf_1h': tf_1h,
            'tf_4h': tf_4h,
            'dmi': dmi_state,
            'rsi_ema': "YES" if rsi_ema_check else "NO",
            'rsi_50': "YES" if rsi_50_check else "NO",
            'obv': "YES" if obv_check else "NO",
            'oi_rising': "YES" if oi_rising_check else "NO",
            'oi_pct': f"{'+' if oi_change_pct > 0 else ''}{oi_change_pct:.2f}%",
            'oi_pct_val': oi_change_pct,
            'bull_score': sum(bull_checks),
            'bear_score': sum(bear_checks)
        }
    except Exception:
        return None

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>All Cryptos Trend & Sector Radar</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
    <style>
        body { background-color: #0b0e11; color: #eaecef; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        .card-custom { background-color: #181a20; border: 1px solid #2b313a; border-radius: 8px; }
        .card-stat h6 { color: #848e9c; font-size: 0.75rem; text-transform: uppercase; margin-bottom: 5px; }
        .card-stat h3 { margin: 0; font-weight: bold; }
        .table-custom { background-color: #181a20; border-radius: 8px; overflow: hidden; border: 1px solid #2b313a; }
        .table-custom table { color: #eaecef; margin-bottom: 0; }
        .table-custom th { background-color: #181a20; color: #848e9c; font-size: 0.75rem; font-weight: 600; text-transform: uppercase; border-bottom: 1px solid #2b313a; padding: 12px 8px; }
        .table-custom td { padding: 10px 8px; border-bottom: 1px solid #2b313a; font-size: 0.85rem; vertical-align: middle; }
        
        .badge-yes, .tf-BULLISH { background-color: #0ecb81; color: #000; font-weight: bold; padding: 3px 6px; border-radius: 4px; font-size: 0.75rem; }
        .badge-no, .tf-BEARISH { background-color: #f6465d; color: #fff; font-weight: bold; padding: 3px 6px; border-radius: 4px; font-size: 0.75rem; }
        .badge-flat, .tf-NEUTRAL { background-color: #474d57; color: #eaecef; padding: 3px 6px; border-radius: 4px; font-size: 0.75rem; }
        
        .text-green { color: #0ecb81 !important; }
        .text-red { color: #f6465d !important; }
        .score-box { font-weight: bold; padding: 2px 6px; border-radius: 4px; display: inline-block; }
        .score-bull { background-color: rgba(14, 203, 129, 0.2); color: #0ecb81; }
        .score-bear { background-color: rgba(246, 70, 93, 0.2); color: #f6465d; }
        .section-title { color: #f0b90b; font-size: 1.1rem; font-weight: bold; margin-bottom: 12px; }
    </style>
</head>
<body class="p-2 p-md-4">
    <div class="container-fluid">
        <!-- Header -->
        <div class="d-flex justify-content-between align-items-center mb-3">
            <div>
                <h4 class="fw-bold mb-0 text-white">All Coins Trend & Sector Radar</h4>
                <small class="text-secondary">Binance Futures — Full Market Scanner (All Pairs)</small>
            </div>
            <a href="/" class="btn btn-warning btn-sm fw-bold">⚡ Refresh All</a>
        </div>

        <!-- Summary Cards -->
        <div class="row g-2 mb-4">
            <div class="col-6 col-md-3">
                <div class="card-custom p-3 card-stat">
                    <h6>PAIRS SCANNED</h6>
                    <h3 class="text-white">{{ stats.total }}</h3>
                </div>
            </div>
            <div class="col-6 col-md-3">
                <div class="card-custom p-3 card-stat">
                    <h6>BULLISH ALIGNED (4-5/5)</h6>
                    <h3 class="text-green">{{ stats.bullish }}</h3>
                </div>
            </div>
            <div class="col-6 col-md-3">
                <div class="card-custom p-3 card-stat">
                    <h6>BEARISH ALIGNED (4-5/5)</h6>
                    <h3 class="text-red">{{ stats.bearish }}</h3>
                </div>
            </div>
            <div class="col-6 col-md-3">
                <div class="card-custom p-3 card-stat">
                    <h6>OI RISING</h6>
                    <h3 class="text-warning">{{ stats.oi_rising }}</h3>
                </div>
            </div>
        </div>

        <!-- Section 1: SECTOR FLOW RADAR -->
        <div class="mb-4">
            <div class="section-title">📊 SECTOR FLOW RADAR</div>
            <div class="table-custom shadow-lg">
                <div class="table-responsive">
                    <table class="table text-center align-middle">
                        <thead>
                            <tr>
                                <th class="text-start ps-3">SECTOR CATEGORY</th>
                                <th>24H TOTAL VOLUME</th>
                                <th>AVG 24H CHANGE</th>
                                <th>TOP GAINER IN SECTOR</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for sec in sectors %}
                            <tr>
                                <td class="text-start ps-3"><strong>{{ sec.name }}</strong></td>
                                <td class="fw-bold text-white">{{ sec.vol_formatted }}</td>
                                <td class="{{ 'text-green' if sec.avg_change >= 0 else 'text-red' }} fw-bold">
                                    {{ '+' if sec.avg_change >= 0 else '' }}{{ "%.2f"|format(sec.avg_change) }}%
                                </td>
                                <td class="text-warning fw-bold">{{ sec.top_gainer }}</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Section 2: TREND SCANNER FOR ALL PAIRS -->
        <div>
            <div class="section-title">🔍 FULL MARKET TREND SCANNER</div>
            <div class="table-custom shadow-lg">
                <div class="table-responsive">
                    <table class="table text-center align-middle">
                        <thead>
                            <tr>
                                <th class="text-start ps-3">PAIR</th>
                                <th>PRICE</th>
                                <th>24H %</th>
                                <th>15M</th>
                                <th>1H</th>
                                <th>4H</th>
                                <th>DMI</th>
                                <th>RSI>EMA</th>
                                <th>OBV>EMA50</th>
                                <th>OI Δ (1H)</th>
                                <th>BULL</th>
                                <th>BEAR</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for item in results %}
                            <tr>
                                <td class="text-start ps-3"><strong>{{ item.symbol }}</strong></td>
                                <td>${{ item.price }}</td>
                                <td class="{{ 'text-green' if item.price_change >= 0 else 'text-red' }}">
                                    {{ '+' if item.price_change >= 0 else '' }}{{ "%.2f"|format(item.price_change) }}%
                                </td>
                                <td><span class="tf-{{ item.tf_15m }}">{{ item.tf_15m }}</span></td>
                                <td><span class="tf-{{ item.tf_1h }}">{{ item.tf_1h }}</span></td>
                                <td><span class="tf-{{ item.tf_4h }}">{{ item.tf_4h }}</span></td>
                                <td><span class="badge {{ 'badge-yes' if item.dmi == 'YES' else ('badge-no' if item.dmi == 'NO' else 'badge-flat') }}">{{ item.dmi }}</span></td>
                                <td><span class="badge {{ 'badge-yes' if item.rsi_ema == 'YES' else 'badge-no' }}">{{ item.rsi_ema }}</span></td>
                                <td><span class="badge {{ 'badge-yes' if item.obv == 'YES' else 'badge-no' }}">{{ item.obv }}</span></td>
                                <td class="{{ 'text-green' if item.oi_pct_val > 0 else 'text-red' }} fw-bold">{{ item.oi_pct }}</td>
                                <td><span class="score-box score-bull">{{ item.bull_score }}/5</span></td>
                                <td><span class="score-box score-bear">{{ item.bear_score }}/5</span></td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

    </div>
</body>
</html>
"""

@app.route('/')
def home():
    tickers_dict = get_all_futures_tickers()
    sectors_data = calculate_sector_flow(tickers_dict)
    
    # 🟢 Binance Futures USDT Pairs အားလုံးကို ဆွဲယူခြင်း (Limit ဖျက်ထားသည်)
    all_tickers = list(tickers_dict.values())
    
    # ⚡ Multithreading (ThreadPoolExecutor) သုံး၍ အပြိုင်စစ်ဆေးခြင်းဖြင့် မြန်ဆန်စေခြင်း
    results = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        scanned_data = list(executor.map(process_symbol_data, all_tickers))
        results = [d for d in scanned_data if d is not None]
            
    # Bull Score အမြင့်ဆုံးမှ အနိမ့်ဆုံးသို့ စီစဉ်ခြင်း
    results = sorted(results, key=lambda x: x['bull_score'], reverse=True)
    
    stats = {
        'total': len(results),
        'bullish': sum(1 for r in results if r['bull_score'] >= 4),
        'bearish': sum(1 for r in results if r['bear_score'] >= 4),
        'oi_rising': sum(1 for r in results if r['oi_rising'] == 'YES')
    }
    
    return render_template_string(HTML_TEMPLATE, results=results, sectors=sectors_data, stats=stats)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
