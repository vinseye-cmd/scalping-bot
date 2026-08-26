"""
Indicateurs techniques : SuperTrend + EMA + RSI + Fibonacci 0.5 (Stratégie 0.5).
"""

import pandas as pd
import numpy as np


# ─────────────────────────────────────────────
# Indicateurs de base
# ─────────────────────────────────────────────

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
        pu = final_upper.iloc[i - 1]
        pl = final_lower.iloc[i - 1]

        if pd.isna(pu):
            final_upper.iloc[i] = upper_band.iloc[i]
        elif upper_band.iloc[i] < pu or df["close"].iloc[i - 1] > pu:
            final_upper.iloc[i] = upper_band.iloc[i]
        else:
            final_upper.iloc[i] = pu

        if pd.isna(pl):
            final_lower.iloc[i] = lower_band.iloc[i]
        elif lower_band.iloc[i] > pl or df["close"].iloc[i - 1] < pl:
            final_lower.iloc[i] = lower_band.iloc[i]
        else:
            final_lower.iloc[i] = pl

        if pd.isna(final_lower.iloc[i]) or pd.isna(final_upper.iloc[i]):
            trend.iloc[i] = trend.iloc[i - 1]
        elif trend.iloc[i - 1] == 1 and df["close"].iloc[i] < final_lower.iloc[i]:
            trend.iloc[i] = -1
        elif trend.iloc[i - 1] == -1 and df["close"].iloc[i] > final_upper.iloc[i]:
            trend.iloc[i] = 1
        else:
            trend.iloc[i] = trend.iloc[i - 1]

    df["supertrend"] = pd.Series(
        np.where(trend == 1, final_lower, final_upper), index=df.index
    )
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


# ─────────────────────────────────────────────
# Filtre tendance 1h (confirmation HTF)
# ─────────────────────────────────────────────

def get_htf_trend(df: pd.DataFrame, atr_period: int = 10, atr_mult: float = 3.0,
                  ema_period: int = 21) -> int:
    """
    Tendance 1h : SuperTrend ET prix vs EMA doivent être d'accord.
    Retourne 1 (haussier confirmé), -1 (baissier confirmé), 0 (neutre).
    """
    df = compute_supertrend(df, atr_period, atr_mult)
    df["ema"] = compute_ema(df, ema_period)
    last = df.iloc[-2]
    st_trend = int(last["trend"])
    price_vs_ema = 1 if float(last["close"]) > float(last["ema"]) else -1
    if st_trend == price_vs_ema:
        return st_trend
    return 0


# ─────────────────────────────────────────────
# Stratégie 0.5 — Fibonacci 50% retracement
# ─────────────────────────────────────────────

def find_swing_high(df: pd.DataFrame, n_lookback: int = 80, n_side: int = 5):
    """Swing high : maximum local avec n_side bougies de chaque côté (le plus récent)."""
    window = df.tail(n_lookback).reset_index(drop=True)
    highs = window["high"]
    for i in range(len(window) - n_side - 1, n_side - 1, -1):
        right_bars = min(n_side, len(window) - 1 - i)
        if right_bars < 1:
            continue
        if (all(highs.iloc[i] > highs.iloc[i - j] for j in range(1, n_side + 1)) and
                all(highs.iloc[i] > highs.iloc[i + j] for j in range(1, right_bars + 1))):
            return float(highs.iloc[i]), i
    return None, -1


def find_swing_low(df: pd.DataFrame, n_lookback: int = 80, n_side: int = 5):
    """Swing low : minimum local avec n_side bougies de chaque côté (le plus récent)."""
    window = df.tail(n_lookback).reset_index(drop=True)
    lows = window["low"]
    for i in range(len(window) - n_side - 1, n_side - 1, -1):
        right_bars = min(n_side, len(window) - 1 - i)
        if right_bars < 1:
            continue
        if (all(lows.iloc[i] < lows.iloc[i - j] for j in range(1, n_side + 1)) and
                all(lows.iloc[i] < lows.iloc[i + j] for j in range(1, right_bars + 1))):
            return float(lows.iloc[i]), i
    return None, -1


def is_bullish_engulfing(df: pd.DataFrame) -> bool:
    """Bougie englobante haussière : bougie verte englobant la bougie rouge précédente."""
    if len(df) < 3:
        return False
    prev = df.iloc[-3]
    curr = df.iloc[-2]
    return (prev["close"] < prev["open"] and    # précédente rouge
            curr["close"] > curr["open"] and    # actuelle verte
            curr["open"] <= prev["close"] and   # ouvre sous la clôture précédente
            curr["close"] >= prev["open"])      # ferme au-dessus de l'ouverture précédente


def is_bearish_engulfing(df: pd.DataFrame) -> bool:
    """Bougie englobante baissière : bougie rouge englobant la bougie verte précédente."""
    if len(df) < 3:
        return False
    prev = df.iloc[-3]
    curr = df.iloc[-2]
    return (prev["close"] > prev["open"] and    # précédente verte
            curr["close"] < curr["open"] and    # actuelle rouge
            curr["open"] >= prev["close"] and   # ouvre au-dessus de la clôture précédente
            curr["close"] <= prev["open"])      # ferme sous l'ouverture précédente


def build_fib05_signal(df: pd.DataFrame, n_lookback: int = 80, n_side: int = 5,
                       tolerance: float = 0.0025):
    """
    Stratégie 0.5 — Signal au retracement Fibonacci 50% confirmé par bougie englobante.

    Logique :
      - Identifier le Swing High (SH) et Swing Low (SL) récents
      - Calculer le niveau 0.5 = (SH + SL) / 2
      - Si le prix est proche du niveau 0.5 (tolérance 0.25%) :
          LONG  : mouvement haussier initial (SL avant SH) + englobante haussière
          SHORT : mouvement baissier initial (SH avant SL) + englobante baissière
      - SL = l'extrême opposé (niveau 1), TP = l'extrême cible (niveau 0)

    Retourne (signal, sl_price, tp_price) ou (None, None, None).
    """
    if len(df) < n_lookback + n_side:
        return None, None, None

    sh, sh_idx = find_swing_high(df, n_lookback, n_side)
    sl, sl_idx = find_swing_low(df, n_lookback, n_side)

    if sh is None or sl is None or sh_idx < 0 or sl_idx < 0:
        return None, None, None

    swing_range = sh - sl
    if swing_range <= 0 or abs(sh_idx - sl_idx) < n_side:
        return None, None, None

    fib_50 = (sh + sl) / 2
    price = float(df.iloc[-2]["close"])

    # Prix doit être proche du niveau 0.5
    if abs(price - fib_50) / price > tolerance:
        return None, None, None

    if sl_idx < sh_idx:
        # Swing Low avant Swing High -> mouvement impulsif haussier -> pullback vers 0.5 -> LONG
        if is_bullish_engulfing(df):
            return "LONG", round(sl, 2), round(sh, 2)
    else:
        # Swing High avant Swing Low -> mouvement impulsif baissier -> rebond vers 0.5 -> SHORT
        if is_bearish_engulfing(df):
            return "SHORT", round(sh, 2), round(sl, 2)

    return None, None, None


def get_session_opening_bias(df: pd.DataFrame, ema_period: int = 21) -> int:
    """
    Biais d'ouverture de session basé sur les 3 premières bougies (15 min).
    Retourne 1 (haussier), -1 (baissier).
    """
    if len(df) < ema_period + 3:
        return 0
    df = df.copy()
    df["ema"] = compute_ema(df, ema_period)
    last = df.iloc[-2]
    return 1 if float(last["close"]) > float(last["ema"]) else -1
