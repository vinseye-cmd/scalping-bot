"""
Récupération des bougies (klines) via l'API publique de Bybit.
Bybit remplace Binance qui retourne une erreur 451 (restriction légale)
depuis les serveurs GitHub Actions.
Le prix BTC est quasi identique sur toutes les grandes plateformes.
"""

import pandas as pd
import requests

BYBIT_KLINES_URL = "https://api.bybit.com/v5/market/kline"

INTERVAL_MAP = {
    "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
    "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
    "1d": "D", "1w": "W",
}


def fetch_klines(symbol: str = "BTCUSDT", interval: str = "5m", limit: int = 150) -> pd.DataFrame:
    """Récupère les dernières bougies et retourne un DataFrame propre."""
    bybit_interval = INTERVAL_MAP.get(interval, "5")
    params = {
        "category": "linear",
        "symbol": symbol,
        "interval": bybit_interval,
        "limit": min(limit, 200),
    }
    response = requests.get(BYBIT_KLINES_URL, params=params, timeout=10)
    response.raise_for_status()
    data = response.json()

    if data.get("retCode") != 0:
        raise RuntimeError(f"Bybit API error: {data.get('retMsg')}")

    raw = list(reversed(data["result"]["list"]))

    df = pd.DataFrame(raw, columns=["open_time", "open", "high", "low", "close", "volume", "turnover"])

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    df["open_time"] = pd.to_datetime(df["open_time"].astype(float), unit="ms")
    df["close_time"] = df["open_time"] + pd.Timedelta(minutes=int(bybit_interval) if bybit_interval.isdigit() else 1440)

    return df[["open_time", "close_time", "open", "high", "low", "close", "volume"]]
