import logging
import os
import time
import urllib.parse
import urllib.request
from binance.client import Client
from binance.exceptions import BinanceAPIException
from colorama import Fore, Style, init

# ==========================================
# 🔑 CREDENTIALS & CONFIGURATION
# ==========================================
SPOT_BASE = "https://testnet.binance.vision"

SPOT_API_KEY = os.environ.get(
    "SPOT_API_KEY",
    "EGMDZzNYcF8aHKsKGxWurbK63sLFdKA42cDEZC3zd8IPkyD3JDEH7btCt4D34aWV",
)
SPOT_SECRET_KEY = os.environ.get(
    "SPOT_SECRET_KEY",
    "YfGOumNKz4MMbZ9MBy7aMB3R6CWxSjVljJvreup8k3BGL5pi1pqc73ieCpOghM8R",
)

# 📱 Telegram Credentials
TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN", "8652275832:AAGxdVX66q7tQP_v3kNVAyslSYD3FsAWz60"
).strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6127362073").strip()

# Trading Parameters
base_currency = "USDT"  # Base Coin
second_currency = "BTC"  # Second Coin
third_currency_list = ["ETH", "BNB", "XRP"]  # Third Coins List

threshold_profit = 0.2  # USDT အနည်းဆုံး ရရှိလိုသည့် အမြတ်
percentage_of_full_amount = 0.95  # Balance ၏ 95% အသုံးပြုမည် (Buffer ပေးရန်)
TAKER_FEE = 0.001  # Binance Default Taker Fee (0.1%)
MAX_WAIT_ATTEMPTS = 10  # Timeout Limit
WAIT_INTERVAL = 1.5  # Seconds

# Initialize Client with Testnet
client = Client(SPOT_API_KEY, SPOT_SECRET_KEY, testnet=True)

# Colorama Setup
init()


# ==========================================
# 📱 TELEGRAM NOTIFICATION HELPER
# ==========================================
def send_telegram_msg(message: str):
    """Telegram သို့ Instant Alert မက်ဆေ့ဂျ် ပို့ပေးသည့် Function"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = urllib.parse.urlencode(
            {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
        ).encode("utf-8")
        req = urllib.request.Request(url, data=payload)
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        logging.error(f"Failed to send Telegram notification: {e}")


class ColorfulFormatter(logging.Formatter):

    def format(self, record):
        if record.levelno == logging.INFO:
            if "Applying Arbitrage" in record.msg:
                return Fore.GREEN + super().format(record) + Style.RESET_ALL
            elif "Arbitrage Opportunity" in record.msg:
                return Fore.YELLOW + super().format(record) + Style.RESET_ALL
            elif "No arbitrage opportunity" in record.msg:
                return Fore.RED + super().format(record) + Style.RESET_ALL
            else:
                return (
                    Fore.LIGHTWHITE_EX
                    + super().format(record)
                    + Style.RESET_ALL
                )
        return super().format(record)


logging.basicConfig(
    level=logging.INFO,
    handlers=[logging.StreamHandler()],
    format="%(message)s",
)
logging.getLogger().handlers[0].setFormatter(ColorfulFormatter("%(message)s"))


# ==========================================
# 🛠️ HELPER FUNCTIONS
# ==========================================
def get_balance(asset_name):
    """Binance မှ Asset ၏ Free Balance ကို ရယူပေးသည့် Function"""
    try:
        balance_info = client.get_asset_balance(asset=asset_name)
        if balance_info:
            return float(balance_info["free"])
        return 0.0
    except BinanceAPIException as e:
        logging.error(f"Error fetching balance for {asset_name}: {e}")
        return 0.0


def wait_for_balance(asset_name, min_amount=0.0):
    """Order ပြည့်မီပြီး အကောင့်ထဲ ငွေရောက်လာသည်အထိ စောင့်ဆိုင်းပေးသည့် Function"""
    attempts = 0
    while attempts < MAX_WAIT_ATTEMPTS:
        bal = get_balance(asset_name)
        if bal > min_amount:
            return bal
        logging.info(
            f"Waiting for {asset_name} balance to update (Current: {bal})..."
        )
        time.sleep(WAIT_INTERVAL)
        attempts += 1
    return get_balance(asset_name)


def arbitrage_opportunity(
    prices, base_amount, first_pair, second_pair, third_pair, fee=TAKER_FEE
):
    """Fees များ ထည့်သွင်းတွက်ချက်ထားသော အမြတ်စစ်ဆေးသည့် Function"""
    fee_factor = 1.0 - fee

    first_price = prices[first_pair]
    second_price = prices[second_pair]
    third_price = prices[third_pair]

    # Leg 1: USDT -> BTC
    second_amount = (base_amount / first_price) * fee_factor
    # Leg 2: BTC -> ETH
    third_amount = (second_amount / second_price) * fee_factor
    # Leg 3: ETH -> USDT
    final_base_amount = (third_amount * third_price) * fee_factor

    potential_profit = final_base_amount - base_amount
    return potential_profit


# ==========================================
# 🚀 MAIN TRADING BOT LOOP
# ==========================================
if __name__ == "__main__":
    start_msg = "🚀 <b>Binance Spot Testnet Arbitrage Bot Started!</b>\nMonitoring triangular arbitrage opportunities..."
    logging.info("Starting Binance Spot Testnet Arbitrage Bot...")
    send_telegram_msg(start_msg)

    while True:
        try:
            # ၁။ Base Balance ရယူခြင်း
            balance_base = get_balance(base_currency)
            logging.info(f"Current {base_currency} Balance: {balance_base:.2f}")

            if balance_base <= 5.0:
                logging.warning(
                    f"Insufficient {base_currency} balance ({balance_base:.2f}). Waiting 10s..."
                )
                time.sleep(10)
                continue

            # ၂။ Ticker Prices ရယူခြင်း
            all_tickers = client.get_all_tickers()
            prices = {
                t["symbol"]: float(t["price"])
                for t in all_tickers
                if "price" in t
            }

            first_pair = f"{second_currency}{base_currency}"

            # ၃။ Pair များကို စစ်ဆေးခြင်း
            for third_currency in third_currency_list:
                second_pair = f"{third_currency}{second_currency}"
                third_pair = f"{third_currency}{base_currency}"

                if not all(
                    p in prices for p in [first_pair, second_pair, third_pair]
                ):
                    continue

                potential_profit = arbitrage_opportunity(
                    prices,
                    balance_base,
                    first_pair,
                    second_pair,
                    third_pair,
                    fee=TAKER_FEE,
                )

                logging.info(
                    f"Verifying: {base_currency} -> {second_currency} -> {third_currency}"
                )

                if potential_profit >= threshold_profit:
                    alert_text = (
                        f"⚡ <b>Arbitrage Opportunity Detected!</b>\n"
                        f"Route: {base_currency} ➡️ {second_currency} ➡️ {third_currency} ➡️ {base_currency}\n"
                        f"Expected Profit: <b>+{potential_profit:.4f} {base_currency}</b>\n"
                        f"Executing trades..."
                    )
                    logging.info(
                        f"Applying Arbitrage: Potential Profit = {potential_profit:.4f} {base_currency}"
                    )
                    send_telegram_msg(alert_text)

                    # --- LEG 1: BUY BTC with USDT ---
                    buy_amount_usdt = (
                        balance_base * percentage_of_full_amount
                    )
                    vol_leg1 = round(buy_amount_usdt / prices[first_pair], 5)

                    logging.info(
                        f"[Leg 1] BUY {first_pair} Volume: {vol_leg1}"
                    )
                    order1 = client.order_market_buy(
                        symbol=first_pair, quantity=vol_leg1
                    )

                    sec_bal = wait_for_balance(second_currency)
                    if sec_bal <= 0:
                        err_msg = "❌ <b>Leg 1 Execution Failed!</b> Stopping cycle."
                        logging.error(err_msg)
                        send_telegram_msg(err_msg)
                        break

                    # --- LEG 2: BUY ETH with BTC ---
                    buy_amount_btc = sec_bal * percentage_of_full_amount
                    vol_leg2 = round(buy_amount_btc / prices[second_pair], 4)

                    logging.info(
                        f"[Leg 2] BUY {second_pair} Volume: {vol_leg2}"
                    )
                    order2 = client.order_market_buy(
                        symbol=second_pair, quantity=vol_leg2
                    )

                    third_bal = wait_for_balance(third_currency)
                    if third_bal <= 0:
                        err_msg = "❌ <b>Leg 2 Execution Failed!</b> Stopping cycle."
                        logging.error(err_msg)
                        send_telegram_msg(err_msg)
                        break

                    # --- LEG 3: SELL ETH for USDT ---
                    vol_leg3 = round(third_bal, 4)
                    logging.info(
                        f"[Leg 3] SELL {third_pair} Volume: {vol_leg3}"
                    )
                    order3 = client.order_market_sell(
                        symbol=third_pair, quantity=vol_leg3
                    )

                    final_usdt = wait_for_balance(
                        base_currency, min_amount=balance_base
                    )
                    realized_profit = final_usdt - balance_base

                    success_msg = (
                        f"✅ <b>Arbitrage Trade Completed!</b>\n"
                        f"Initial Balance: {balance_base:.2f} {base_currency}\n"
                        f"Final Balance: {final_usdt:.2f} {base_currency}\n"
                        f"Net Realized Profit: <b>+{realized_profit:.4f} {base_currency}</b>"
                    )
                    logging.info(
                        f"Arbitrage Completed! Realized Profit: {realized_profit:.4f} {base_currency}\n"
                    )
                    send_telegram_msg(success_msg)
                    break

                elif threshold_profit > potential_profit > 0:
                    logging.info(
                        f"Arbitrage Opportunity: {potential_profit:.5f} {base_currency} (Below Threshold)"
                    )
                else:
                    logging.info(
                        f"No arbitrage opportunity: {potential_profit:.4f} {base_currency}"
                    )

                time.sleep(1)

        except BinanceAPIException as e:
            err_log = f"⚠️ <b>Binance API Error:</b> {e.message}"
            logging.error(f"Binance API Exception: {e}")
            send_telegram_msg(err_log)
            time.sleep(5)
        except Exception as e:
            err_log = f"🚨 <b>Unexpected Bot Error:</b> {str(e)}"
            logging.error(f"Unexpected error: {e}")
            send_telegram_msg(err_log)
            time.sleep(5)
