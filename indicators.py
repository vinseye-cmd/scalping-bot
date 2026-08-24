"""
Calcul des indicateurs techniques : SuperTrend + EMA + RSI.
"""

import pandas as pd
import numpy as np


def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def compute_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.DataFrame:
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
    return df["close"].ewm(span=period, adjust=False).mean()


def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("inf"))
    return 100 - (100 / (1 + rs))


def get_htf_trend(df: pd.DataFrame, atr_period: int = 10, atr_mult: float = 3.0, ema_period: int = 21) -> int:
    """
    Tendance 1h : SuperTrend ET prix vs EMA doivent être d'accord.
    Retourne 1 (haussier confirmé), -1 (baissier confirmé), ou 0 (neutre / désaccord).
    """
    df = compute_supertrend(df, atr_period, atr_mult)
    df["ema"] = compute_ema(df, ema_period)
    last = df.iloc[-2]
    st_trend = int(last["trend"])
    price_vs_ema = 1 if float(last["close"]) > float(last["ema"]) else -1
    if st_trend == price_vs_ema:
        return st_trend
    return 0  # désaccord SuperTrend/EMA = tendance non confirmée


def build_signal(df: pd.DataFrame, atr_period: int, atr_mult: float, ema_period: int) -> pd.DataFrame:
    """
    Signal sur flip SuperTrend confirmé par EMA et RSI.
    LONG  : SuperTrend vient de passer haussier + prix > EMA + RSI < 70 (pas suracheté)
    SHORT : SuperTrend vient de passer baissier + prix < EMA + RSI > 30 (pas survendu)
    """
    df = compute_supertrend(df, atr_period, atr_mult)
    df["ema"] = compute_ema(df, ema_period)
    df["rsi"] = compute_rsi(df, period=14)

    df["trend_prev"] = df["trend"].shift(1)
    df["flip_up"] = (df["trend"] == 1) & (df["trend_prev"] == -1)
    df["flip_down"] = (df["trend"] == -1) & (df["trend_prev"] == 1)

    def signal_row(row):
        if pd.isna(row["rsi"]):
            return None
        if row["flip_up"] and row["close"] > row["ema"] and row["rsi"] < 70:
            return "LONG"
        if row["flip_down"] and row["close"] < row["ema"] and row["rsi"] > 30:
            return "SHORT"
        return None

    df["signal"] = df.apply(signal_row, axis=1)
    return df
