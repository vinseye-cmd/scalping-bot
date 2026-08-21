"""
Calcul des indicateurs techniques : SuperTrend + EMA (moyenne mobile exponentielle).

Le SuperTrend est basé sur l'ATR (Average True Range) : il s'adapte à la
volatilité du marché, ce qui le rend particulièrement adapté au scalping
sur un actif volatil comme le Bitcoin.
"""

import pandas as pd
import numpy as np


def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """Calcule l'Average True Range sur les colonnes high/low/close."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def compute_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 2.0) -> pd.DataFrame:
    """
    Calcule le SuperTrend. Ajoute deux colonnes à df :
      - supertrend : la valeur de la ligne
      - trend : 1 (haussier / vert) ou -1 (baissier / rouge)
    """
    df = df.copy()
    atr = compute_atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2

    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr

    final_upper = upper_band.copy()
    final_lower = lower_band.copy()
    trend = pd.Series(1, index=df.index)

    for i in range(1, len(df)):
        prev_final_upper = final_upper.iloc[i - 1]
        prev_final_lower = final_lower.iloc[i - 1]

        # Tant que l'ATR n'est pas encore calculable (début de série), on
        # se contente de propager la bande brute pour éviter de figer un NaN.
        if pd.isna(prev_final_upper):
            final_upper.iloc[i] = upper_band.iloc[i]
        elif upper_band.iloc[i] < prev_final_upper or df["close"].iloc[i - 1] > prev_final_upper:
            final_upper.iloc[i] = upper_band.iloc[i]
        else:
            final_upper.iloc[i] = prev_final_upper

        if pd.isna(prev_final_lower):
            final_lower.iloc[i] = lower_band.iloc[i]
        elif lower_band.iloc[i] > prev_final_lower or df["close"].iloc[i - 1] < prev_final_lower:
            final_lower.iloc[i] = lower_band.iloc[i]
        else:
            final_lower.iloc[i] = prev_final_lower

        # Détermination de la tendance (reste à 1 tant que les bandes ne sont pas valides)
        if pd.isna(final_lower.iloc[i]) or pd.isna(final_upper.iloc[i]):
            trend.iloc[i] = trend.iloc[i - 1]
        elif trend.iloc[i - 1] == 1 and df["close"].iloc[i] < final_lower.iloc[i]:
            trend.iloc[i] = -1
        elif trend.iloc[i - 1] == -1 and df["close"].iloc[i] > final_upper.iloc[i]:
            trend.iloc[i] = 1
        else:
            trend.iloc[i] = trend.iloc[i - 1]

    supertrend = pd.Series(np.where(trend == 1, final_lower, final_upper), index=df.index)

    df["supertrend"] = supertrend
    df["trend"] = trend
    return df


def compute_ema(df: pd.DataFrame, period: int = 21) -> pd.Series:
    """Moyenne mobile exponentielle sur le prix de clôture."""
    return df["close"].ewm(span=period, adjust=False).mean()


def get_htf_trend(df: pd.DataFrame, atr_period: int = 10, atr_mult: float = 2.0) -> int:
    """SuperTrend sur timeframe supérieur (1h). Retourne 1 (haussier) ou -1 (baissier)."""
    df = compute_supertrend(df, atr_period, atr_mult)
    return int(df["trend"].iloc[-2])


def build_signal(df: pd.DataFrame, atr_period: int, atr_mult: float, ema_period: int) -> pd.DataFrame:
    """
    Ajoute les colonnes supertrend, trend, ema et signal à df.
    signal vaut :
      "LONG"  -> le SuperTrend vient de passer haussier ET le prix est au-dessus de l'EMA
      "SHORT" -> le SuperTrend vient de passer baissier ET le prix est en dessous de l'EMA
      None    -> pas de nouveau signal sur cette bougie
    """
    df = compute_supertrend(df, atr_period, atr_mult)
    df["ema"] = compute_ema(df, ema_period)

    df["trend_prev"] = df["trend"].shift(1)
    df["flip_up"] = (df["trend"] == 1) & (df["trend_prev"] == -1)
    df["flip_down"] = (df["trend"] == -1) & (df["trend_prev"] == 1)

    def signal_row(row):
        if row["flip_up"] and row["close"] > row["ema"]:
            return "LONG"
        if row["flip_down"] and row["close"] < row["ema"]:
            return "SHORT"
        return None

    df["signal"] = df.apply(signal_row, axis=1)
    return df
