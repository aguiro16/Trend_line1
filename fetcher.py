"""
fetcher.py — جلب البيانات من Binance
"""
import os
import logging
import pandas as pd
from binance.client import Client
from binance.exceptions import BinanceAPIException

log = logging.getLogger("fetcher")

API_KEY    = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")

client = Client(API_KEY, API_SECRET)

TF_MAP = {
    "3d": Client.KLINE_INTERVAL_3DAY,
    "1d": Client.KLINE_INTERVAL_1DAY,
}

TOP_N            = int(os.getenv("TOP_PAIRS", "60"))
VOLUME_SPIKE_PCT = float(os.getenv("VOLUME_SPIKE_PCT", "200"))


def get_top_pairs(n: int = TOP_N) -> list[str]:
    try:
        tickers  = client.get_ticker()
        EXCLUDED = ["UP", "DOWN", "BULL", "BEAR", "TUSD", "BUSD", "USDC"]
        usdt = [
            t for t in tickers
            if t["symbol"].endswith("USDT")
            and not any(s in t["symbol"] for s in EXCLUDED)
        ]
        by_volume   = sorted(usdt, key=lambda x: float(x["quoteVolume"]), reverse=True)
        top_symbols = {t["symbol"] for t in by_volume[:n]}

        spike_symbols = set()
        for t in usdt:
            try:
                vol_24h   = float(t["quoteVolume"])
                price_chg = abs(float(t.get("priceChangePercent", 0)))
                if price_chg >= 15 and vol_24h > 500_000:
                    spike_symbols.add(t["symbol"])
            except (ValueError, ZeroDivisionError):
                continue

        all_symbols = list(top_symbols | spike_symbols)
        log.info(f"فحص {len(all_symbols)} زوج (Top {n} + Spike: {len(spike_symbols - top_symbols)})")
        return all_symbols
    except BinanceAPIException as e:
        log.error(f"get_top_pairs error: {e}")
        return []


def fetch_ohlcv(symbol: str, tf: str, limit: int = 150) -> pd.DataFrame | None:
    try:
        raw = client.get_klines(symbol=symbol, interval=TF_MAP[tf], limit=limit)
        if not raw or len(raw) < 30:
            return None
        df = pd.DataFrame(raw, columns=[
            "time","open","high","low","close","volume",
            "close_time","qav","trades","tbbav","tbqav","ignore"
        ])
        df = df.astype({"open": float, "high": float, "low": float,
                        "close": float, "volume": float})
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        return df.reset_index(drop=True)
    except BinanceAPIException as e:
        log.warning(f"{symbol} {tf} fetch error: {e}")
        return None


def get_current_price(symbol: str) -> float | None:
    try:
        ticker = client.get_symbol_ticker(symbol=symbol)
        return float(ticker["price"])
    except BinanceAPIException as e:
        log.warning(f"get_current_price {symbol}: {e}")
        return None
