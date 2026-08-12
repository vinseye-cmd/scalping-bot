"""
Récupération des bougies (klines) via OKX (primaire) + Kraken (secours).
Binance : bloqué 451 depuis GitHub Actions (restriction légale).
Bybit   : bloqué 403 depuis GitHub Actions (geo-block).
OKX et Kraken sont accessibles librement depuis les serveurs GitHub.
"""

import pandas as pd
import requests

OKX_KLINES_URL = "https://www.okx.com/api/v5/market/candles"
KRAKEN_KLINES_URL = "https://api.kraken.com/0/public/OHLC"

OKX_INTERVAL_MAP = {
    "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1H", "2h": "2H", "4h": "4H", "6h": "6H", "12h": "12H",
    "1d": "1D", "1w": "1W",
}

KRAKEN_INTERVAL_MAP = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440, "1w": 10080,
}


def _okx_bar_minutes(bar: str) -> int:
    if bar.endswith("m"):
        return int(bar[:-1])
    if bar.endswith("H"):
        return int(bar[:-1]) * 60
    if bar.endswith("D"):
        return int(bar[:-1]) * 1440
    return 5


def _fetch_okx(symbol: str = "BTCUSDT", interval: str = "5m", limit: int = 150) -> pd.DataFrame:
    if symbol.endswith("USDT"):
        inst_id = symbol[:-4] + "-USDT"
    else:
        inst_id = symbol
    bar = OKX_INTERVAL_MAP.get(interval, "5m")
    params = {"instId": inst_id, "bar": bar, "limit": min(limit, 300)}
    response = requests.get(OKX_KLINES_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    if data.get("code") != "0":
        raise RuntimeError(f"OKX API error: {data.get('msg')}")
    raw = list(reversed(data["data"]))
    df = pd.DataFrame(raw, columns=["open_time", "open", "high", "low", "close",
                                     "volume", "vol_ccy", "vol_quote", "confirm"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    mins = _okx_bar_minutes(bar)
    df["close_time"] = df["open_time"] + pd.Timedelta(minutes=mins)
    return df[["open_time", "close_time", "open", "high", "low", "close", "volume"]]


def _fetch_kraken(symbol: str = "BTCUSDT", interval: str = "5m", limit: int = 150) -> pd.DataFrame:
    kraken_interval = KRAKEN_INTERVAL_MAP.get(interval, 5)
    params = {"pair": "XBTUSD", "interval": kraken_interval}
    response = requests.get(KRAKEN_KLINES_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()
    if data.get("error"):
        raise RuntimeError(f"Kraken API error: {data['error']}")
    result = data["result"]
    pair_key = next(k for k in result if k != "last")
    raw = result[pair_key][-limit:]
    df = pd.DataFrame(raw, columns=["open_time", "open", "high", "low", "close",
                                     "vwap", "volume", "count"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = pd.to_datetime(df["open_time"].astype(int), unit="s")
    df["close_time"] = df["open_time"] + pd.Timedelta(minutes=kraken_interval)
    return df[["open_time", "close_time", "open", "high", "low", "close", "volume"]]


def fetch_klines(symbol: str = "BTCUSDT", interval: str = "5m", limit: int = 150) -> pd.DataFrame:
    """Récupère les bougies OHLCV. OKX en primaire, Kraken en secours."""
    try:
        return _fetch_okx(symbol, interval, limit)
    except Exception as e:
        print(f"[WARN] OKX indisponible ({e}), bascule sur Kraken...")
        return _fetch_kraken(symbol, interval, limit)
