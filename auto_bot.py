"""
Bot autonome local — Stratégie 0.5 (Fibonacci 50% retracement).
Lancement : python auto_bot.py
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytz
from dotenv import load_dotenv

from executor import MoonXExecutor
from indicators import (build_fib05_signal, compute_supertrend,
                        get_htf_trend, get_session_opening_bias)
from market_data import fetch_klines
from notifier import send_status_message

STATE_FILE = Path(__file__).parent / "state_auto.json"
PARIS_TZ = pytz.timezone("Europe/Paris")
POLL_SECONDS = 30


def load_config() -> dict:
    load_dotenv()
    required = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "MOONX_API_TOKEN"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Variables manquantes dans .env : {', '.join(missing)}")
    return {
        "tg_token": os.getenv("TELEGRAM_BOT_TOKEN"),
        "chat_id": os.getenv("TELEGRAM_CHAT_ID"),
        "moonx_token": os.getenv("MOONX_API_TOKEN"),
        "symbol_binance": os.getenv("SYMBOL", "BTCUSDT"),
        "symbol_moonx": os.getenv("MOONX_SYMBOL", "BTC"),
        "interval": os.getenv("INTERVAL", "5m"),
        "atr_period": int(os.getenv("ATR_PERIOD", 10)),
        "atr_mult": float(os.getenv("ATR_MULTIPLIER", 3.0)),
        "ema_period": int(os.getenv("EMA_PERIOD", 21)),
        "leverage": int(os.getenv("LEVERAGE", 10)),
        "risk_pct": float(os.getenv("RISK_PCT", 1.0)),
        "max_losses": int(os.getenv("MAX_CONSECUTIVE_LOSSES", 2)),
        "trading_windows": os.getenv("TRADING_WINDOWS", "09:00-13:00,14:00-17:00,20:00-23:00"),
        "fib_lookback": int(os.getenv("FIB_LOOKBACK", 80)),
        "fib_n_side": int(os.getenv("FIB_N_SIDE", 5)),
        "fib_tolerance": float(os.getenv("FIB_TOLERANCE", 0.0025)),
    }


def parse_windows(ws: str) -> list:
    result = []
    for chunk in ws.split(","):
        s, e = chunk.strip().split("-")
        result.append((
            datetime.strptime(s, "%H:%M").time(),
            datetime.strptime(e, "%H:%M").time(),
        ))
    return result


def in_window(windows: list) -> bool:
    now = datetime.now(PARIS_TZ).time()
    return any(s <= now <= e for s, e in windows)


def current_session_idx(windows: list) -> int:
    now = datetime.now(PARIS_TZ).time()
    for i, (s, e) in enumerate(windows):
        if s <= now <= e:
            return i
    return -1


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "position": None,
        "consecutive_losses": 0,
        "locked_session_idx": -1,
        "last_signal": None,
        "session_bias": {},
    }


def save_state(s: dict):
    STATE_FILE.write_text(json.dumps(s, indent=2))


def tg(token: str, chat_id: str, text: str):
    try:
        send_status_message(token, chat_id, text)
    except Exception as e:
        print(f"[TG ERROR] {e}")


def run():
    config = load_config()
    windows = parse_windows(config["trading_windows"])
    state = load_state()
    ex = MoonXExecutor(config["moonx_token"])

    try:
        balance = ex.get_futures_balance()
        balance_str = f"{balance:.2f} USDT"
    except Exception as e:
        balance_str = f"indisponible ({e})"

    print(f"Bot Strategie 0.5 demarre | Solde futures : {balance_str}")
    tg(config["tg_token"], config["chat_id"],
       f"*Bot Strategie 0.5 ACTIF*\n"
       f"Signal : Fibonacci 50% + Englobante\n"
       f"Actif : `{config['symbol_moonx']}` | Levier : `{config['leverage']}x`\n"
       f"Fenetres : `{config['trading_windows']}` (Paris)\n"
       f"Solde futures : `{balance_str}`")

    while True:
        now_str = datetime.now(PARIS_TZ).strftime("%H:%M:%S")
        sess_idx = current_session_idx(windows)
        active = sess_idx >= 0

        try:
            # ── 1. SURVEILLANCE DE LA POSITION OUVERTE ──────────────
            if state["position"]:
                pos = state["position"]

                try:
                    live = ex.get_open_positions()
                    live_ids = [str(p.get("positionId", p.get("id", p.get("_id", "")))) for p in live]
                    position_exists = pos["id"] in live_ids
                except Exception:
                    position_exists = True

                if not position_exists:
                    state["position"] = None
                    save_state(state)
                    print(f"[{now_str}] Position cloturee par MoonX (SL/TP).")
                    tg(config["tg_token"], config["chat_id"],
                       f"*POSITION CLOTUREE PAR MOONX*\n"
                       f"{pos['side'].upper()} BTC ferme automatiquement\n"
                       f"Entree : `{pos['entry']:,.2f}` | SL : `{pos['sl']:,.2f}` | TP : `{pos['tp2']:,.2f}`")
                else:
                    df_quick = fetch_klines(config["symbol_binance"], config["interval"], limit=5)
                    price_now = float(df_quick.iloc[-1]["close"])

                    if not pos["tp1_hit"]:
                        tp1_hit = (
                            (pos["side"] == "long" and price_now >= pos["tp1"]) or
                            (pos["side"] == "short" and price_now <= pos["tp1"])
                        )
                        if tp1_hit:
                            ex.set_tp_sl(pos["id"], sl_price=pos["entry"], tp_price=pos["tp2"], tp_fraction=100)
                            state["position"]["tp1_hit"] = True
                            state["position"]["sl"] = pos["entry"]
                            save_state(state)
                            print(f"[{now_str}] TP1 @ {price_now:.2f} | SL -> BE | TP2 -> {pos['tp2']:.2f}")
                            tg(config["tg_token"], config["chat_id"],
                               f"*TP1 ATTEINT - BREAKEVEN ACTIVE*\n"
                               f"{pos['side'].upper()} BTC | Prix : `{price_now:,.2f}`\n"
                               f"SL deplace a l'entree : `{pos['entry']:,.2f}`\n"
                               f"Objectif Fib 0 : `{pos['tp2']:,.2f}`")

                    if not pos["tp1_hit"]:
                        df_full = fetch_klines(config["symbol_binance"], config["interval"], limit=150)
                        df_full = compute_supertrend(df_full, config["atr_period"], config["atr_mult"])
                        last = df_full.iloc[-2]
                        reversed_ = (
                            (pos["side"] == "long" and int(last["trend"]) == -1) or
                            (pos["side"] == "short" and int(last["trend"]) == 1)
                        )
                        if reversed_:
                            try:
                                ex.close_position(pos["id"], percentage=100)
                            except Exception:
                                pass
                            state["consecutive_losses"] += 1
                            was_locked = state["consecutive_losses"] >= config["max_losses"]
                            if was_locked:
                                state["locked_session_idx"] = sess_idx
                            state["position"] = None
                            save_state(state)
                            print(f"[{now_str}] Sortie SuperTrend @ {price_now:.2f}")
                            tg(config["tg_token"], config["chat_id"],
                               f"*SORTIE - RETOURNEMENT SUPERTREND*\n"
                               f"{pos['side'].upper()} BTC | Prix : `{price_now:,.2f}`"
                               + ("\n*Session verrouillee.*" if was_locked else ""))
                        else:
                            print(f"[{now_str}] Position {pos['side']} | Prix : {price_now:.2f}")
                    else:
                        # Trailing stop SuperTrend après TP1
                        try:
                            df_trail = fetch_klines(config["symbol_binance"], config["interval"], limit=150)
                            df_trail = compute_supertrend(df_trail, config["atr_period"], config["atr_mult"])
                            trail_st = float(df_trail["supertrend"].iloc[-2])
                            current_sl = pos["sl"]
                            if pos["side"] == "long":
                                new_sl = round(max(current_sl, trail_st), 2)
                            else:
                                new_sl = round(min(current_sl, trail_st), 2)
                            if new_sl != current_sl:
                                ex.set_tp_sl(pos["id"], sl_price=new_sl, tp_price=pos["tp2"], tp_fraction=100)
                                state["position"]["sl"] = new_sl
                                save_state(state)
                                print(f"[{now_str}] Trailing SL: {current_sl:.2f} -> {new_sl:.2f}")
                                tg(config["tg_token"], config["chat_id"],
                                   f"*TRAILING STOP AJUSTE*\n"
                                   f"SL : `{current_sl:,.2f}` -> `{new_sl:,.2f}`\n"
                                   f"TP2 cible : `{pos['tp2']:,.2f}`")
                        except Exception as trail_err:
                            print(f"[{now_str}] Trailing SL non mis a jour : {trail_err}")
                        print(f"[{now_str}] En attente Fib-0 ({pos['tp2']:.2f}) | SL: {pos['sl']:.2f} | Prix: {price_now:.2f}")

            # ── 2. RECHERCHE D'UN SIGNAL ─────────────────────────────
            elif active:
                if state["locked_session_idx"] == sess_idx:
                    print(f"[{now_str}] Session verrouillee.")
                else:
                    df = fetch_klines(config["symbol_binance"], config["interval"], limit=150)

                    # Biais d'ouverture de session
                    session_biases = state.get("session_bias", {})
                    bias_key = str(sess_idx)
                    if bias_key not in session_biases:
                        opening_bias = get_session_opening_bias(df, config["ema_period"])
                        session_biases[bias_key] = opening_bias
                        state["session_bias"] = session_biases
                        save_state(state)
                        bias_label = "haussier" if opening_bias == 1 else "baissier"
                        print(f"[{now_str}] Biais session : {bias_label}")
                    opening_bias = session_biases.get(bias_key, 0)

                    # Filtre HTF 1h
                    df_1h = fetch_klines(config["symbol_binance"], "1h", limit=60)
                    htf_trend = get_htf_trend(df_1h, config["atr_period"], config["atr_mult"], config["ema_period"])
                    htf_labels = {1: "haussier", -1: "baissier", 0: "neutre"}
                    htf_label = htf_labels.get(htf_trend, "?")

                    # Signal Stratégie 0.5
                    signal, fib_sl, fib_tp = build_fib05_signal(
                        df,
                        n_lookback=config["fib_lookback"],
                        n_side=config["fib_n_side"],
                        tolerance=config["fib_tolerance"],
                    )
                    price = float(df.iloc[-2]["close"])

                    if signal in ("LONG", "SHORT") and signal != state.get("last_signal"):
                        if (signal == "LONG" and htf_trend != 1) or (signal == "SHORT" and htf_trend != -1):
                            print(f"[{now_str}] Signal {signal} bloque — tendance 1h {htf_label}")
                            continue

                        if opening_bias != 0 and (
                            (signal == "LONG" and opening_bias != 1) or
                            (signal == "SHORT" and opening_bias != -1)
                        ):
                            print(f"[{now_str}] Signal {signal} bloque — contra biais de session")
                            continue

                        dist = (price - fib_sl) if signal == "LONG" else (fib_sl - price)
                        if dist <= 0:
                            print(f"[{now_str}] Distance SL nulle, signal ignore.")
                            continue

                        tp1 = round((price + fib_tp) / 2, 2)
                        fib_50_display = round((fib_sl + fib_tp) / 2, 2)

                        balance = ex.get_futures_balance()
                        risk_usdt = balance * config["risk_pct"] / 100
                        sl_pct = dist / price
                        margin_calc = round(risk_usdt / sl_pct / config["leverage"], 2)
                        max_margin = round(balance * 0.15, 2)
                        margin = max(5.0, min(margin_calc, max_margin))

                        pos_id = ex.open_position(
                            side=signal.lower(),
                            symbol=config["symbol_moonx"],
                            margin_usdt=margin,
                            leverage=config["leverage"],
                            sl_price=fib_sl,
                            tp_price=tp1,
                        )

                        if pos_id:
                            try:
                                live = ex.get_open_positions()
                                fill_price = next(
                                    (float(p["entryPrice"]) for p in live
                                     if str(p.get("positionId", p.get("id", p.get("_id", "")))) == pos_id),
                                    price
                                )
                            except Exception:
                                fill_price = price

                            real_tp1 = round((fill_price + fib_tp) / 2, 2)
                            ex.set_tp_sl(pos_id, tp_price=real_tp1, sl_price=fib_sl, tp_fraction=50)

                            state["position"] = {
                                "id": pos_id,
                                "side": signal.lower(),
                                "entry": fill_price,
                                "sl": fib_sl,
                                "tp1": real_tp1,
                                "tp2": fib_tp,
                                "tp1_hit": False,
                                "session_idx": sess_idx,
                            }
                            state["last_signal"] = signal
                            state["consecutive_losses"] = 0
                            save_state(state)
                            print(f"[{now_str}] STRATEGIE 0.5 {signal} @ {fill_price:.2f} | SL={fib_sl:.2f} | TP={fib_tp:.2f}")
                            tg(config["tg_token"], config["chat_id"],
                               f"*STRATEGIE 0.5 - {signal}*\n"
                               f"Actif : `{config['symbol_moonx']}`\n"
                               f"Entree (Fib 0.5) : `{fill_price:,.2f}` USDT\n"
                               f"Marge : `{margin:.2f}` USDT | Levier : `{config['leverage']}x`\n"
                               f"---- Fibonacci ----\n"
                               f"Niveau 0 (objectif) : `{fib_tp:,.2f}`\n"
                               f"Niveau 0.5 (entree) : `{fib_50_display:,.2f}`\n"
                               f"Niveau 1 (SL) : `{fib_sl:,.2f}`\n"
                               f"---- Ordres ----\n"
                               f"SL : `{fib_sl:,.2f}`\n"
                               f"TP1 (50%) : `{real_tp1:,.2f}`\n"
                               f"TP2 (100%) : `{fib_tp:,.2f}`")
                        else:
                            print(f"[{now_str}] Signal {signal} detecte mais positionId non recu.")
                    else:
                        print(f"[{now_str}] Pas de signal Fib0.5 | Prix : {price:.2f} | HTF : {htf_label}")

            # ── 3. HORS FENETRE ──────────────────────────────────────
            else:
                if state["locked_session_idx"] >= 0 and state["locked_session_idx"] != sess_idx:
                    state["locked_session_idx"] = -1
                    state["consecutive_losses"] = 0
                    state["session_bias"] = {}
                    save_state(state)
                print(f"[{now_str}] Hors fenetre horaire.")

        except Exception as exc:
            print(f"[{now_str}] [ERREUR] {exc}")
            tg(config["tg_token"], config["chat_id"], f"Erreur bot : {exc}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
