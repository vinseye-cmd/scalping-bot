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
# Filtre tendance 1h (SuperTrend uniquement)
# ─────────────────────────────────────────────

def get_htf_trend(df: pd.DataFrame, atr_period: int = 10, atr_mult: float = 3.0,
                  ema_period: int = 21) -> int:
    """
    Tendance 1h basée sur SuperTrend uniquement.
    Retourne 1 (haussier) ou -1 (baissier).
    """
    df = compute_supertrend(df, atr_period, atr_mult)
    return int(df.iloc[-2]["trend"])


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


def is_bullish_confirmation(df: pd.DataFrame) -> bool:
    """Confirmation haussière : dernière bougie clôturée verte (close > open)."""
    if len(df) < 2:
        return False
    curr = df.iloc[-2]
    return float(curr["close"]) > float(curr["open"])


def is_bearish_confirmation(df: pd.DataFrame) -> bool:
    """Confirmation baissière : dernière bougie clôturée rouge (close < open)."""
    if len(df) < 2:
        return False
    curr = df.iloc[-2]
    return float(curr["close"]) < float(curr["open"])


def build_fib05_signal(df: pd.DataFrame, n_lookback: int = 80, n_side: int = 5,
                       tolerance: float = 0.005):
    """
    Stratégie 0.5 — Signal au retracement Fibonacci 50%.

    Logique :
      - Identifier Swing High (SH) et Swing Low (SL) récents
      - Calculer niveau 0.5 = (SH + SL) / 2
      - Si le prix est dans la zone 0.5 (±tolérance) :
          LONG  : impulsion haussière (SL avant SH) + bougie verte de confirmation
          SHORT : impulsion baissière (SH avant SL) + bougie rouge de confirmation
      - SL = extrême opposé (niveau 1), TP = extrême cible (niveau 0)

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

    if abs(price - fib_50) / price > tolerance:
        return None, None, None

    if sl_idx < sh_idx:
        # Swing Low avant Swing High → impulsion haussière → pullback → LONG
        if is_bullish_confirmation(df):
            return "LONG", round(sl, 2), round(sh, 2)
    else:
        # Swing High avant Swing Low → impulsion baissière → rebond → SHORT
        if is_bearish_confirmation(df):
            return "SHORT", round(sh, 2), round(sl, 2)

    return None, None, None
