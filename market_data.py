"""
Récupération des bougies (klines) via l'API publique de Binance.

On utilise Binance comme source de PRIX uniquement (API publique, sans clé,
très fiable et à jour à la seconde). L'exécution des ordres reste sur Moonx,
manuellement par toi pour l'instant.

Le prix du Bitcoin est quasi identique sur toutes les grandes plateformes
(écart de quelques dollars maximum), donc c'est une source fiable pour le
calcul des indicateurs.
"""

import pandas as pd
import requests

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


def fetch_klines(symbol: str = "BTCUSDT", interval: str = "1m", limit: int = 150) -> pd.DataFrame:
    """Récupère les dernières bougies et retourne un DataFrame propre."""
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    response = requests.get(BINANCE_KLINES_URL, params=params, timeout=10)
    response.raise_for_status()
    raw = response.json()

    df = pd.DataFrame(raw, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore"
    ])

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms")

    return df[["open_time", "close_time", "open", "high", "low", "close", "volume"]]
