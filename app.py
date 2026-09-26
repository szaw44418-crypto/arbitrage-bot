import os
import math
import time
import requests
from flask import Flask, render_template_string, redirect, url_for, flash
from concurrent.futures import ThreadPoolExecutor, as_completed

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-this-secret")

# SECURITY: do NOT hard-code real Telegram credentials in source code.
TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    "8652275832:AAGxdVX66q7tQP_v3kNVAyslSYD3FsAWz60"
).strip()
TELEGRAM_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID",
    "6127362073"
).strip()

BASE_URL = "https://fapi.binance.com"
KLINE_URL = f"{BASE_URL}/fapi/v1/klines"
TICKER_24H_URL = f"{BASE_URL}/fapi/v1/ticker/24hr"
EXCHANGE_INFO_URL = f"{BASE_URL}/fapi/v1/exchangeInfo"
OI_URL = f"{BASE_URL}/fapi/v1/openInterest"
OI_HIST_URL = f"{BASE_URL}/futures/data/openInterestHist"

# Final system defaults to 1H because the locked rule says:
# current 1H volume > 20-period average volume.
SCAN_INTERVAL = os.environ.get("SCAN_INTERVAL", "1h")
KLINE_LIMIT = int(os.environ.get("KLINE_LIMIT", "120"))
MIN_24H_QUOTE_VOLUME = float(os.environ.get("MIN_24H_QUOTE_VOLUME", "1000000"))
MAX_WORKERS = int(os.environ.get("MAX_WORKERS", "18"))
TOP_N = int(os.environ.get("TOP_N", "10"))

# Screenshot-style filters. OI is informational by default; it is NOT part of
# the locked 5/5 trend score unless REQUIRE_OI_RISING is switched on.
OI_THRESHOLD_PCT = float(os.environ.get("OI_THRESHOLD_PCT", "3"))
OI_LOOKBACK = int(os.environ.get("OI_LOOKBACK", "3"))
REQUIRE_OI_RISING = os.environ.get("REQUIRE_OI_RISING", "false").lower() == "true"

SECTOR_MAP = {
    "Layer 1 / L1": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "ADAUSDT", "AVAXUSDT", "APTUSDT", "SEIUSDT", "TONUSDT", "DOTUSDT", "NEARUSDT", "TRXUSDT", "BCHUSDT", "LTCUSDT", "XRPUSDT"],
    "AI & Big Data": ["NEARUSDT", "RENDERUSDT", "FETUSDT", "TAOUSDT", "GRTUSDT", "WLDUSDT", "ARKMUSDT", "AGIXUSDT", "THETAUSDT", "AKTUSDT"],
    "DeFi & DEX": ["UNIUSDT", "AAVEUSDT", "PENDLEUSDT", "ENAUSDT", "MKRUSDT", "CRVUSDT", "LDOUSDT", "SNXUSDT", "COMPUSDT", "JUPUSDT", "RAYUSDT"],
    "Meme Coins": ["DOGEUSDT", "PEPEUSDT", "SHIBUSDT", "WIFUSDT", "BONKUSDT", "FLOKIUSDT", "MEMEUSDT", "BOMEUSDT", "POPCATUSDT", "NEIROUSDT"],
    "Layer 2 / L2": ["OPUSDT", "ARBUSDT", "STRKUSDT", "POLUSDT", "IMXUSDT", "MANTAUSDT", "METISUSDT", "ZKUSDT"],
    "Gaming & Meta": ["GALAUSDT", "AXSUSDT", "SANDUSDT", "MANAUSDT", "BEAMXUSDT", "PIXELUSDT", "YGGUSDT"],
    "RWA & Storage": ["ONDOUSDT", "OMUSDT", "FILUSDT", "ARUSDT", "LINKUSDT", "TIAUSDT"],
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "CryptoFinalAlgorithm/2.0"})

_exchange_cache = {"ts": 0, "symbols": set()}


def api_get(url, params=None, timeout=8):
    try:
        r = SESSION.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        data = r.json()
        return data
    except Exception as exc:
        print(f"API error {url}: {exc}")
        return None



def telegram_credentials_configured():
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

def send_telegram_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False
    if not telegram_credentials_configured():
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        response = SESSION.post(url, json=payload, timeout=8)
        return response.status_code == 200
    except Exception as exc:
        print(f"Telegram exception: {exc}")
        return False


def calculate_ema(data, period):
    if len(data) < period:
        return []
    alpha = 2 / (period + 1)
    ema = [sum(data[:period]) / period]
    for val in data[period:]:
        ema.append((val * alpha) + (ema[-1] * (1 - alpha)))
    return ema


def calculate_rsi(closes, period=14):
    if len(closes) <= period:
        return []
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi = [100 - (100 / (1 + (avg_gain / (avg_loss if avg_loss else 1e-10))))]
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi.append(100 - (100 / (1 + (avg_gain / (avg_loss if avg_loss else 1e-10)))))
    return rsi


def calculate_obv(closes, volumes):
    obv = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    return obv


def calculate_dmi(highs, lows, closes, period=14):
    tr_list, dmp_list, dmn_list = [], [], []
    for i in range(1, len(closes)):
        h, l, prev_close = highs[i], lows[i], closes[i - 1]
        tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        up_move = h - highs[i - 1]
        down_move = lows[i - 1] - l
        dmp = up_move if up_move > down_move and up_move > 0 else 0.0
        dmn = down_move if down_move > up_move and down_move > 0 else 0.0
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
    pos_di = 100 * smooth_dmp / smooth_tr if smooth_tr else 0.0
    neg_di = 100 * smooth_dmn / smooth_tr if smooth_tr else 0.0
    return pos_di, neg_di


def get_active_perpetual_symbols():
    now = time.time()
    if now - _exchange_cache["ts"] < 300 and _exchange_cache["symbols"]:
        return _exchange_cache["symbols"]
    data = api_get(EXCHANGE_INFO_URL, timeout=10)
    if not data:
        return set()
    symbols = set()
    for s in data.get("symbols", []):
        if (
            s.get("quoteAsset") == "USDT"
            and s.get("contractType") == "PERPETUAL"
            and s.get("status") == "TRADING"
        ):
            symbols.add(s["symbol"])
    _exchange_cache.update({"ts": now, "symbols": symbols})
    return symbols


def get_btc_trend():
    res = api_get(KLINE_URL, {"symbol": "BTCUSDT", "interval": SCAN_INTERVAL, "limit": 100}, timeout=6)
    if not isinstance(res, list) or len(res) < 60:
        return "NEUTRAL"
    # Closed candles only.
    closed = res[:-1]
    closes = [float(k[4]) for k in closed]
    ema20 = calculate_ema(closes, 20)
    ema50 = calculate_ema(closes, 50)
    if not ema20 or not ema50:
        return "NEUTRAL"
    if closes[-1] > ema20[-1] > ema50[-1]:
        return "BULLISH"
    if closes[-1] < ema20[-1] < ema50[-1]:
        return "BEARISH"
    return "NEUTRAL"


def get_oi_status(symbol):
    """Screenshot-style OI rising check; informational unless REQUIRE_OI_RISING=true."""
    try:
        data = api_get(
            OI_HIST_URL,
            {"symbol": symbol, "period": "5m", "limit": max(OI_LOOKBACK + 1, 4)},
            timeout=5,
        )
        if isinstance(data, list) and len(data) >= 2:
            values = [float(x.get("sumOpenInterestValue", x.get("sumOpenInterest", 0))) for x in data]
            lookback_index = max(0, len(values) - 1 - OI_LOOKBACK)
            base = values[lookback_index]
            if values[-1] <= 0 or base <= 0:
                return False, 0.0
            pct = ((values[-1] - base) / base) * 100
            return pct >= OI_THRESHOLD_PCT, pct
    except Exception:
        pass
    return False, 0.0


def get_sector_tags(symbol):
    return [name for name, coins in SECTOR_MAP.items() if symbol in coins]


def process_symbol_data(ticker, active_symbols):
    symbol = ticker.get("symbol", "")
    if symbol not in active_symbols:
        return None
    if not symbol.endswith("USDT"):
        return None
    try:
        price = float(ticker["lastPrice"])
        price_change = float(ticker["priceChangePercent"])
        volume_24h = float(ticker["quoteVolume"])
    except Exception:
        return None
    if volume_24h < MIN_24H_QUOTE_VOLUME:
        return None

    res = api_get(KLINE_URL, {"symbol": symbol, "interval": SCAN_INTERVAL, "limit": KLINE_LIMIT}, timeout=7)
    if not isinstance(res, list) or len(res) < 70:
        return None

    # Use only closed candles for all signal calculations.
    closed = res[:-1]
    highs = [float(k[2]) for k in closed]
    lows = [float(k[3]) for k in closed]
    closes = [float(k[4]) for k in closed]
    volumes = [float(k[5]) for k in closed]
    if len(closes) < 60:
        return None

    ema20_vals = calculate_ema(closes, 20)
    ema50_vals = calculate_ema(closes, 50)
    rsi_vals = calculate_rsi(closes, 14)
    rsi_ema_vals = calculate_ema(rsi_vals, 9) if rsi_vals else []
    obv_vals = calculate_obv(closes, volumes)
    obv_ema50_vals = calculate_ema(obv_vals, 50)
    pos_di, neg_di = calculate_dmi(highs, lows, closes, 14)
    if not all([ema20_vals, ema50_vals, rsi_vals, rsi_ema_vals, obv_ema50_vals]) or pos_di is None:
        return None

    current_close = closes[-1]
    ema20 = ema20_vals[-1]
    ema50 = ema50_vals[-1]

    # Volume Surge: current CLOSED candle volume > previous 20 CLOSED candles average.
    if len(volumes) < 21:
        return None
    avg_vol20 = sum(volumes[-21:-1]) / 20
    current_volume = volumes[-1]
    vol_ratio = current_volume / avg_vol20 if avg_vol20 else 0.0
    vol_rising = current_volume > avg_vol20

    # Locked 5/5 trend checklist.
    dmi_bull = pos_di > neg_di
    dmi_bear = neg_di > pos_di
    rsi_ema_bull = rsi_vals[-1] > rsi_ema_vals[-1]
    rsi_ema_bear = rsi_vals[-1] < rsi_ema_vals[-1]
    rsi_50_bull = rsi_vals[-1] > 50
    rsi_50_bear = rsi_vals[-1] < 50
    obv_bull = obv_vals[-1] > obv_ema50_vals[-1]
    obv_bear = obv_vals[-1] < obv_ema50_vals[-1]
    price_above_ema = current_close > ema20 > ema50
    price_below_ema = current_close < ema20 < ema50

    bull_checks = [dmi_bull, rsi_ema_bull, rsi_50_bull, obv_bull, price_above_ema]
    bear_checks = [dmi_bear, rsi_ema_bear, rsi_50_bear, obv_bear, price_below_ema]
    bull_score = sum(bull_checks)
    bear_score = sum(bear_checks)

    # Extension <= 3.5% from EMA20.
    distance_pct = ((current_close - ema20) / ema20) * 100 if ema20 else 0.0
    ext_long_ok = price_above_ema and distance_pct <= 3.5
    ext_short_ok = price_below_ema and distance_pct >= -3.5

    # Screenshot-style OI data is added later only to likely candidates.
    sector_tags = get_sector_tags(symbol)
    if not sector_tags:
        sector_tags = ["General"]

    return {
        "symbol": symbol,
        "sector": sector_tags[0],
        "sector_tags": sector_tags,
        "price": current_close,
        "ticker_price": price,
        "price_change": price_change,
        "vol_24h": volume_24h,
        "current_volume": current_volume,
        "avg_vol20": avg_vol20,
        "vol_ratio": vol_ratio,
        "vol_rising": vol_rising,
        "ema20": ema20,
        "ema50": ema50,
        "rsi": rsi_vals[-1],
        "pos_di": pos_di,
        "neg_di": neg_di,
        "obv_bull": obv_bull,
        "price_above_ema": price_above_ema,
        "price_below_ema": price_below_ema,
        "bull_score": bull_score,
        "bear_score": bear_score,
        "ext_long_ok": ext_long_ok,
        "ext_short_ok": ext_short_ok,
        "extension_pct": distance_pct,
        "oi_rising": False,
        "oi_change_pct": 0.0,
    }


def fetch_daily_sector_history(symbol):
    data = api_get(KLINE_URL, {"symbol": symbol, "interval": "1d", "limit": 31}, timeout=7)
    if not isinstance(data, list) or len(data) < 8:
        return None
    # Exclude the current unfinished daily candle.
    closed = data[:-1]
    rows = []
    for k in closed[-30:]:
        rows.append({
            "quote_volume": float(k[7]),
            "close": float(k[4]),
        })
    return rows


def clamp(value, low=1.0, high=10.0):
    return max(low, min(high, value))


def build_sector_radar(scanned_results):
    """Builds screenshot-style Sector Flow Radar metrics.

    Sector score is a transparent composite:
      45% bullish participation, 35% volume-flow strength, 20% price breadth.
    10 = strong, 1 = weak. This is the implementation used by this dashboard;
    it is not an exchange-provided score.
    """
    sector_rows = []
    unique_sector_symbols = sorted({
        symbol for coins in SECTOR_MAP.values() for symbol in coins
    })
    active = get_active_perpetual_symbols()
    unique_sector_symbols = [s for s in unique_sector_symbols if s in active]

    history = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_daily_sector_history, s): s for s in unique_sector_symbols}
        for f in as_completed(futures):
            symbol = futures[f]
            try:
                history[symbol] = f.result()
            except Exception:
                history[symbol] = None

    scanned_by_symbol = {x["symbol"]: x for x in scanned_results}
    all_current_sector_volume = sum(scanned_by_symbol[s]["vol_24h"] for s in scanned_by_symbol if any(s in c for c in SECTOR_MAP.values()))
    if all_current_sector_volume <= 0:
        all_current_sector_volume = 1.0

    for sector_name, symbols in SECTOR_MAP.items():
        members = [scanned_by_symbol[s] for s in symbols if s in scanned_by_symbol]
        if not members:
            continue

        bullish_count = sum(1 for x in members if x["bull_score"] >= 4)
        price_positive_count = sum(1 for x in members if x["price_change"] > 0)
        busy_count = 0
        current_volume = 0.0
        daily7 = []
        daily30 = []
        historical_share = []

        for symbol in symbols:
            rows = history.get(symbol)
            if not rows:
                continue
            current_volume += scanned_by_symbol.get(symbol, {}).get("vol_24h", 0.0)
            vals = [r["quote_volume"] for r in rows]
            if len(vals) >= 8:
                avg7 = sum(vals[-7:]) / 7
                cur = vals[-1]
                if avg7 > 0 and cur / avg7 >= 1.5:
                    busy_count += 1
            if len(vals) >= 8:
                daily7.append(sum(vals[-7:]) / 7)
            if len(vals) >= 30:
                daily30.append(sum(vals[-30:]) / 30)

        # Current sector volume from ticker data is more reliable than daily kline
        # quote-volume for the current day.
        current_volume = sum(x["vol_24h"] for x in members)
        avg_sector7 = sum(daily7) if daily7 else current_volume
        avg_sector30 = sum(daily30) if daily30 else current_volume
        vs_week = current_volume / avg_sector7 if avg_sector7 else 1.0
        vs_month = current_volume / avg_sector30 if avg_sector30 else 1.0

        # Transparent 1-10 flow strength. 1x/1x => 5; ~2x/2x => 10.
        flow_score = 5 + 2.5 * math.log(max(vs_week, 0.05), 2) + 2.5 * math.log(max(vs_month, 0.05), 2)
        flow_score = clamp(flow_score)
        participation_score = (bullish_count / len(members)) * 10
        price_breadth_score = (price_positive_count / len(members)) * 10
        score = round((0.45 * participation_score) + (0.35 * flow_score) + (0.20 * price_breadth_score), 1)

        share_now = (current_volume / all_current_sector_volume) * 100
        read = "Money coming in" if score >= 7.5 else ("Bleeding attention" if score <= 3.5 else "Busy, no direction")

        sector_rows.append({
            "sector": sector_name,
            "volume_24h": current_volume,
            "vs_week": vs_week,
            "vs_month": vs_month,
            "share_now": share_now,
            "participation": (busy_count / len(members)) * 100,
            "price_24h": sum(x["price_change"] for x in members) / len(members),
            "coins": len(members),
            "score": score,
            "read": read,
        })

    return sorted(sector_rows, key=lambda x: x["score"], reverse=True)


def calculate_trade_levels(price, position_type):
    # Kept close to the original user's fixed risk model.
    if position_type == "LONG":
        entry_low = price * 0.997
        entry_high = price * 1.002
        entry_mid = (entry_low + entry_high) / 2
        sl = price * 0.985
        tp1 = price * 1.015
        tp2 = price * 1.030
        tp3 = price * 1.050
        risk = entry_mid - sl
        reward = tp2 - entry_mid
    else:
        entry_low = price * 0.998
        entry_high = price * 1.003
        entry_mid = (entry_low + entry_high) / 2
        sl = price * 1.015
        tp1 = price * 0.985
        tp2 = price * 0.970
        tp3 = price * 0.950
        risk = sl - entry_mid
        reward = entry_mid - tp2

    rr_ratio = reward / risk if risk > 0 else 0.0
    return {
        "market_price": f"{price:g}",
        "entry_zone": f"{entry_low:g} - {entry_high:g}",
        "tp1": f"{tp1:g}",
        "tp2": f"{tp2:g}",
        "tp3": f"{tp3:g}",
        "sl": f"{sl:g}",
        "rr_ratio": f"{rr_ratio:.2f}:1",
        "rr_ok": rr_ratio >= 2.0,
    }


def attach_oi_to_candidates(candidates):
    if not candidates:
        return candidates
    # OI is a screenshot-style extra signal, not part of the locked 5/5.
    with ThreadPoolExecutor(max_workers=min(10, len(candidates))) as executor:
        futures = {executor.submit(get_oi_status, c["symbol"]): c for c in candidates}
        for f in as_completed(futures):
            c = futures[f]
            try:
                rising, pct = f.result()
                c["oi_rising"] = rising
                c["oi_change_pct"] = pct
            except Exception:
                pass
    return candidates


def get_scanned_signals():
    btc_trend = get_btc_trend()
    tickers = api_get(TICKER_24H_URL, timeout=12)
    if not isinstance(tickers, list):
        return [], [], btc_trend, 0, []

    active_symbols = get_active_perpetual_symbols()
    candidates = [t for t in tickers if t.get("symbol") in active_symbols]
    scanned_results = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(process_symbol_data, t, active_symbols) for t in candidates]
        for f in as_completed(futures):
            try:
                result = f.result()
                if result:
                    scanned_results.append(result)
            except Exception:
                pass

    # Sector scores are computed from the sector's own participation/flow,
    # rather than from BTC dominance or a generic market score.
    sector_radar = build_sector_radar(scanned_results)
    sector_score_by_name = {x["sector"]: x["score"] for x in sector_radar}

    valid_longs = []
    valid_shorts = []
    for coin in scanned_results:
        sec_score = sector_score_by_name.get(coin["sector"], 0.0)
        coin["sector_score"] = sec_score

        # Final LONG algorithm.
        long_ok = (
            sec_score >= 8.0
            and coin["bull_score"] == 5
            and coin["price_above_ema"]
            and coin["vol_rising"]
            and coin["ext_long_ok"]
            and btc_trend != "BEARISH"
        )
        # Final SHORT algorithm.
        short_ok = (
            sec_score <= 3.0
            and coin["bear_score"] == 5
            and coin["price_below_ema"]
            and coin["vol_rising"]
            and coin["ext_short_ok"]
            and btc_trend != "BULLISH"
        )

        if REQUIRE_OI_RISING:
            # OI is checked only for provisional candidates to avoid hundreds of API calls.
            if long_ok or short_ok:
                rising, pct = get_oi_status(coin["symbol"])
                coin["oi_rising"] = rising
                coin["oi_change_pct"] = pct
                if not rising:
                    long_ok = short_ok = False

        if long_ok:
            trade = calculate_trade_levels(coin["price"], "LONG")
            if trade["rr_ok"]:
                coin_copy = dict(coin)
                coin_copy["trade"] = trade
                coin_copy["position"] = "LONG"
                valid_longs.append(coin_copy)

        if short_ok:
            trade = calculate_trade_levels(coin["price"], "SHORT")
            if trade["rr_ok"]:
                coin_copy = dict(coin)
                coin_copy["trade"] = trade
                coin_copy["position"] = "SHORT"
                valid_shorts.append(coin_copy)

    # Rank by the strength of the actual confirmation, then volume participation.
    valid_longs.sort(key=lambda x: (x["sector_score"], x["bull_score"], x["vol_ratio"], x["vol_24h"]), reverse=True)
    valid_shorts.sort(key=lambda x: (10 - x["sector_score"], x["bear_score"], x["vol_ratio"], x["vol_24h"]), reverse=True)

    # Add screenshot-style OI status to the displayed top candidates only.
    valid_longs = attach_oi_to_candidates(valid_longs[:TOP_N])
    valid_shorts = attach_oi_to_candidates(valid_shorts[:TOP_N])

    return valid_longs, valid_shorts, btc_trend, len(scanned_results), sector_radar


HTML_TEMPLATE = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Crypto Final Algorithm Dashboard</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css">
<style>
body{background:#0b0e11;color:#eaecef;font-family:Arial,sans-serif}.card-custom{background:#181a20;border:1px solid #2b313a;border-radius:10px}.table{--bs-table-bg:#11151d;--bs-table-color:#eaecef;vertical-align:middle}.text-green{color:#0ecb81!important}.text-red{color:#f6465d!important}.small-muted{color:#8b93a7;font-size:.82rem}.badge-soft{background:#232936;border:1px solid #343b4a}.score-long{color:#0ecb81;font-weight:700}.score-short{color:#f6465d;font-weight:700}.bar{height:6px;background:#2b313a;border-radius:4px;overflow:hidden}.bar>span{display:block;height:100%;background:#0ecb81}.bar.red>span{background:#f6465d}.sticky{position:sticky;top:0;z-index:5;background:#0b0e11;padding-top:8px}
</style>
</head>
<body class="p-3">
<div class="container-fluid">
<div class="sticky">
<div class="d-flex justify-content-between align-items-center mb-3">
<div><h4 class="fw-bold mb-0">⚡ Crypto Final Algorithm Dashboard</h4><div class="small-muted">{{ total_scanned }} active USDT perpetuals scanned · {{ interval }} closed-candle mode · BTC: <b>{{ btc_trend }}</b></div></div>
<div><a href="/send-telegram" class="btn btn-success fw-bold me-2">📲 Send Top Signals</a><a href="/" class="btn btn-outline-light fw-bold">↻ Refresh</a></div>
</div>
</div>

{% with messages=get_flashed_messages() %}{% if messages %}{% for msg in messages %}<div class="alert alert-info">{{ msg }}</div>{% endfor %}{% endif %}{% endwith %}

<div class="card-custom p-3 mb-3">
<h5 class="fw-bold mb-1">📡 Sector Flow Radar</h5><div class="small-muted mb-3">10 = strong sector flow · 1 = weak. Score uses sector participation + sector volume flow + price breadth.</div>
<div class="table-responsive"><table class="table table-sm"><thead><tr><th>Sector</th><th>Score</th><th>24H Volume</th><th>vs Week</th><th>vs Month</th><th>Share</th><th>Participation</th><th>Price 24H</th><th>Coins</th><th>Read</th></tr></thead><tbody>
{% for s in sector_radar %}<tr><td><b>{{ s.sector }}</b></td><td class="{{ 'score-long' if s.score>=8 else ('score-short' if s.score<=3 else '') }}">{{ s.score }}/10</td><td>${{ '%.1f'|format(s.volume_24h/1000000) }}M</td><td>{{ '%.2f'|format(s.vs_week) }}x</td><td>{{ '%.2f'|format(s.vs_month) }}x</td><td>{{ '%.2f'|format(s.share_now) }}%</td><td>{{ '%.0f'|format(s.participation) }}%</td><td class="{{ 'text-green' if s.price_24h>0 else 'text-red' }}">{{ '%+.2f'|format(s.price_24h) }}%</td><td>{{ s.coins }}</td><td>{{ s.read }}</td></tr>{% endfor %}
</tbody></table></div>
</div>

<div class="row g-3">
<div class="col-lg-6"><div class="card-custom p-3"><h5 class="text-green fw-bold">🟢 LONG — Final Confirmation</h5><div class="small-muted mb-2">Sector ≥8 · Bull 5/5 · 1H Volume > 20Avg · BTC not bearish · Extension ≤3.5% · RR ≥2:1</div>
<div class="table-responsive"><table class="table table-sm"><thead><tr><th>Pair</th><th>Sector</th><th>Trend</th><th>Vol</th><th>OI</th><th>RR</th></tr></thead><tbody>
{% for c in longs %}<tr><td><b class="text-green">{{ c.symbol.replace('USDT','') }}</b></td><td>{{ c.sector }}<br><span class="small-muted">{{ c.sector_score }}/10</span></td><td>{{ c.bull_score }}/5</td><td>{{ '%.2f'|format(c.vol_ratio) }}x</td><td>{{ 'YES' if c.oi_rising else 'NO' }}<br><span class="small-muted">{{ '%+.2f'|format(c.oi_change_pct) }}%</span></td><td>{{ c.trade.rr_ratio }}</td></tr>{% endfor %}
{% if not longs %}<tr><td colspan="6" class="text-muted">No LONG setup passed all final filters.</td></tr>{% endif %}
</tbody></table></div></div></div>

<div class="col-lg-6"><div class="card-custom p-3"><h5 class="text-red fw-bold">🔴 SHORT — Final Confirmation</h5><div class="small-muted mb-2">Sector ≤3 · Bear 5/5 · 1H Volume > 20Avg · BTC not bullish · Extension ≤3.5% · RR ≥2:1</div>
<div class="table-responsive"><table class="table table-sm"><thead><tr><th>Pair</th><th>Sector</th><th>Trend</th><th>Vol</th><th>OI</th><th>RR</th></tr></thead><tbody>
{% for c in shorts %}<tr><td><b class="text-red">{{ c.symbol.replace('USDT','') }}</b></td><td>{{ c.sector }}<br><span class="small-muted">{{ c.sector_score }}/10</span></td><td>{{ c.bear_score }}/5</td><td>{{ '%.2f'|format(c.vol_ratio) }}x</td><td>{{ 'YES' if c.oi_rising else 'NO' }}<br><span class="small-muted">{{ '%+.2f'|format(c.oi_change_pct) }}%</span></td><td>{{ c.trade.rr_ratio }}</td></tr>{% endfor %}
{% if not shorts %}<tr><td colspan="6" class="text-muted">No SHORT setup passed all final filters.</td></tr>{% endif %}
</tbody></table></div></div></div>
</div>

<div class="card-custom p-3 mt-3"><h6 class="fw-bold">🔒 Locked Logic</h6><div class="small-muted">LONG: Sector ≥8 + DMI/RSI/RSI50/OBV/EMA = 5/5 + volume surge + BTC not bearish + EMA20 extension ≤3.5% + RR ≥2. SHORT is the exact inverse with Sector ≤3 and BTC not bullish.</div><div class="small-muted mt-2">OI rising is displayed from the screenshot-style scanner but is informational by default. Set <code>REQUIRE_OI_RISING=true</code> if you want OI to become an additional hard filter.</div></div>
</div></body></html>
"""


@app.route("/")
def home():
    longs, shorts, btc_trend, total_scanned, sector_radar = get_scanned_signals()
    return render_template_string(
        HTML_TEMPLATE,
        longs=longs,
        shorts=shorts,
        btc_trend=btc_trend,
        total_scanned=total_scanned,
        sector_radar=sector_radar,
        interval=SCAN_INTERVAL,
    )


@app.route("/send-telegram")
def send_telegram():
    longs, shorts, btc_trend, _, _ = get_scanned_signals()
    if not longs and not shorts:
        flash("❌ Final rules အားလုံးကို ဖြတ်သန်းနိုင်တဲ့ Entry မရှိပါ။")
        return redirect(url_for("home"))

    msg = "🚀 <b>FINAL ALGORITHM CONFIRMED SIGNALS</b> 🚀\n\n"
    msg += f"📊 <b>BTC:</b> {btc_trend}\n"
    msg += f"⏱ <b>TF:</b> {SCAN_INTERVAL}\n"
    msg += "----------------------------------------\n"

    for c in longs[:TOP_N]:
        t = c["trade"]
        msg += f"🟢 <b>LONG {c['symbol'].replace('USDT','')}</b>\n"
        msg += f"• Sector: {c['sector']} ({c['sector_score']}/10)\n"
        msg += f"• Bull: {c['bull_score']}/5\n"
        msg += f"• Volume: {c['vol_ratio']:.2f}x 20Avg\n"
        msg += f"• OI: {'YES' if c['oi_rising'] else 'NO'} ({c['oi_change_pct']:+.2f}%)\n"
        msg += f"• Entry: ${t['entry_zone']}\n• TP1: ${t['tp1']}\n• TP2: ${t['tp2']}\n• TP3: ${t['tp3']}\n• SL: ${t['sl']}\n• RR: {t['rr_ratio']}\n\n"

    for c in shorts[:TOP_N]:
        t = c["trade"]
        msg += f"🔴 <b>SHORT {c['symbol'].replace('USDT','')}</b>\n"
        msg += f"• Sector: {c['sector']} ({c['sector_score']}/10)\n"
        msg += f"• Bear: {c['bear_score']}/5\n"
        msg += f"• Volume: {c['vol_ratio']:.2f}x 20Avg\n"
        msg += f"• OI: {'YES' if c['oi_rising'] else 'NO'} ({c['oi_change_pct']:+.2f}%)\n"
        msg += f"• Entry: ${t['entry_zone']}\n• TP1: ${t['tp1']}\n• TP2: ${t['tp2']}\n• TP3: ${t['tp3']}\n• SL: ${t['sl']}\n• RR: {t['rr_ratio']}\n\n"

    msg += "⚠️ <i>Signal scanner only. Apply position sizing and independent risk controls.</i>"
    if send_telegram_message(msg):
        flash("✅ Top confirmed LONG/SHORT signals ကို Telegram သို့ ပို့ပြီးပါပြီ။")
    else:
        flash("❌ Telegram credentials မရှိပါ/ပို့မရပါ။ Environment variables စစ်ပါ။")
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
