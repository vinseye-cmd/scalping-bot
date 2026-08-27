"""
Bot autonome local — Stratégie 0.5 (Fibonacci 50%) — 24h/24.
Lancement : python auto_bot.py
"""

import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytz
from dotenv import load_dotenv

from executor import MoonXExecutor
from indicators import build_fib05_signal, compute_supertrend, get_htf_trend
from market_data import fetch_klines
from notifier import send_status_message

STATE_FILE = Path(__file__).parent / "state_auto.json"
PARIS_TZ = pytz.timezone("Europe/Paris")
POLL_SECONDS = 30
HEARTBEAT_INTERVAL_MIN = 60


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
        "lock_hours": int(os.getenv("LOCK_HOURS", 2)),
        "fib_lookback": int(os.getenv("FIB_LOOKBACK", 80)),
        "fib_n_side": int(os.getenv("FIB_N_SIDE", 5)),
        "fib_tolerance": float(os.getenv("FIB_TOLERANCE", 0.005)),
    }


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {
        "position": None,
        "consecutive_losses": 0,
        "locked_until": None,
        "last_signal": None,
        "last_heartbeat_ts": None,
        "last_htf_trend": 0,
    }


def save_state(s: dict):
    STATE_FILE.write_text(json.dumps(s, indent=2))


def tg(token: str, chat_id: str, text: str):
    try:
        send_status_message(token, chat_id, text)
    except Exception as e:
        print(f"[TG ERROR] {e}")


def is_locked(state: dict) -> bool:
    if not state.get("locked_until"):
        return False
    return datetime.now(timezone.utc) < datetime.fromisoformat(state["locked_until"])


def should_heartbeat(state: dict) -> bool:
    last = state.get("last_heartbeat_ts")
    if not last:
        return True
    return datetime.now(timezone.utc) - datetime.fromisoformat(last) >= timedelta(minutes=HEARTBEAT_INTERVAL_MIN)


def run():
    config = load_config()
    state = load_state()
    ex = MoonXExecutor(config["moonx_token"])

    try:
        balance = ex.get_futures_balance()
        balance_str = f"{balance:.2f} USDT"
    except Exception as e:
        balance_str = f"indisponible ({e})"

    print(f"Bot Strategie 0.5 (24h/24) demarre | Solde futures : {balance_str}")
    tg(config["tg_token"], config["chat_id"],
       f"*Bot Strategie 0.5 ACTIF — 24h/24*\n"
       f"Signal : Fibonacci 50% + Bougie directionnelle\n"
       f"Actif : `{config['symbol_moonx']}` | Levier : `{config['leverage']}x`\n"
       f"Tolerance Fib : `±{config['fib_tolerance']*100:.1f}%`\n"
       f"Solde futures : `{balance_str}`")

    while True:
        now_str = datetime.now(PARIS_TZ).strftime("%H:%M:%S")
        now_utc = datetime.now(timezone.utc)

        try:
            # ── HEARTBEAT HORAIRE ────────────────────────────────────
            if should_heartbeat(state):
                try:
                    bal = ex.get_futures_balance()
                    bal_str = f"{bal:.2f} USDT"
                except Exception:
                    bal_str = "indisponible"

                df_hb = fetch_klines(config["symbol_binance"], config["interval"], limit=150)
                price_hb = float(df_hb.iloc[-2]["close"])
                df_1h_hb = fetch_klines(config["symbol_binance"], "1h", limit=60)
                htf_hb = get_htf_trend(df_1h_hb, config["atr_period"], config["atr_mult"], config["ema_period"])
                htf_hb_label = "HAUSSIER" if htf_hb == 1 else "BAISSIER"

                sig_hb, sl_hb, tp_hb = build_fib05_signal(
                    df_hb, config["fib_lookback"], config["fib_n_side"], config["fib_tolerance"]
                )
                fib_str = (f"Fib 0.5 zone : `{round((sl_hb + tp_hb) / 2, 2):,.2f}`\n"
                           f"SL : `{sl_hb:,.2f}` | TP : `{tp_hb:,.2f}`") if sig_hb else "Aucun niveau Fib 0.5 actif"

                pos_str = (
                    f"Position : `{state['position']['side'].upper()}` @ `{state['position']['entry']:,.2f}`"
                    if state.get("position") else "Pas de position ouverte"
                )
                lock_str = ""
                if is_locked(state):
                    lu = datetime.fromisoformat(state["locked_until"]).astimezone(PARIS_TZ)
                    lock_str = f"\nPause jusqu'a : `{lu.strftime('%H:%M')}`"

                tg(config["tg_token"], config["chat_id"],
                   f"*STATUT HORAIRE*\n"
                   f"BTC : `{price_hb:,.2f}` USDT\n"
                   f"Tendance 1h : *{htf_hb_label}*\n"
                   f"{fib_str}\n"
                   f"{pos_str}\n"
                   f"Solde futures : `{bal_str}`\n"
                   f"Pertes consecutives : `{state['consecutive_losses']}/{config['max_losses']}`"
                   + lock_str)

                state["last_heartbeat_ts"] = now_utc.isoformat()
                save_state(state)

            # ── 1. SURVEILLANCE POSITION OUVERTE ────────────────────
            if state["position"]:
                pos = state["position"]

                try:
                    live = ex.get_open_positions()
                    live_ids = [str(p.get("positionId", p.get("id", p.get("_id", "")))) for p in live]
                    position_exists = pos["id"] in live_ids
                except Exception:
                    position_exists = True

                if not position_exists:
                    try:
                        history = ex._call("get_futures_trade_history")
                        last_trade = history[0] if isinstance(history, list) and history else {}
                        pnl_r = float(last_trade.get("pnl", 0))
                        fee = float(last_trade.get("feeAmount", 0))
                        net = pnl_r - fee
                        pnl_str = f"`{net:+.4f} USDT` ({'gain' if net >= 0 else 'perte'})"
                    except Exception:
                        net = 0
                        pnl_str = "indisponible"

                    if net < 0:
                        state["consecutive_losses"] += 1
                        if state["consecutive_losses"] >= config["max_losses"]:
                            locked_until = (now_utc + timedelta(hours=config["lock_hours"])).isoformat()
                            state["locked_until"] = locked_until
                            lu_p = datetime.fromisoformat(locked_until).astimezone(PARIS_TZ)
                            tg(config["tg_token"], config["chat_id"],
                               f"*PAUSE ACTIVEE — {state['consecutive_losses']} pertes consecutives*\n"
                               f"Reprise a `{lu_p.strftime('%H:%M')}`")
                    else:
                        state["consecutive_losses"] = 0
                        state["locked_until"] = None

                    state["position"] = None
                    save_state(state)
                    tg(config["tg_token"], config["chat_id"],
                       f"{'OK' if net >= 0 else 'NON'} *POSITION CLOTUREE*\n"
                       f"{pos['side'].upper()} BTC\n"
                       f"Entree : `{pos['entry']:,.2f}` | SL : `{pos['sl']:,.2f}` | TP : `{pos['tp2']:,.2f}`\n"
                       f"Resultat net : {pnl_str}")
                else:
                    df_q = fetch_klines(config["symbol_binance"], config["interval"], limit=5)
                    price_now = float(df_q.iloc[-1]["close"])

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
                            tg(config["tg_token"], config["chat_id"],
                               f"*TP1 ATTEINT — BREAKEVEN ACTIVE*\n"
                               f"{pos['side'].upper()} BTC | Prix : `{price_now:,.2f}`\n"
                               f"SL deplace a l'entree : `{pos['entry']:,.2f}`\n"
                               f"Objectif Fib 0 : `{pos['tp2']:,.2f}`")

                    if not pos["tp1_hit"]:
                        df_f = fetch_klines(config["symbol_binance"], config["interval"], limit=150)
                        df_f = compute_supertrend(df_f, config["atr_period"], config["atr_mult"])
                        reversed_ = (
                            (pos["side"] == "long" and int(df_f.iloc[-2]["trend"]) == -1) or
                            (pos["side"] == "short" and int(df_f.iloc[-2]["trend"]) == 1)
                        )
                        if reversed_:
                            try:
                                ex.close_position(pos["id"], percentage=100)
                            except Exception:
                                pass
                            pnl_pct = (
                                (price_now - pos["entry"]) / pos["entry"] * 100 * config["leverage"]
                                if pos["side"] == "long"
                                else (pos["entry"] - price_now) / pos["entry"] * 100 * config["leverage"]
                            )
                            if pnl_pct < 0:
                                state["consecutive_losses"] += 1
                                if state["consecutive_losses"] >= config["max_losses"]:
                                    locked_until = (now_utc + timedelta(hours=config["lock_hours"])).isoformat()
                                    state["locked_until"] = locked_until
                            else:
                                state["consecutive_losses"] = 0
                                state["locked_until"] = None
                            state["position"] = None
                            save_state(state)
                            tg(config["tg_token"], config["chat_id"],
                               f"*SORTIE — RETOURNEMENT SUPERTREND*\n"
                               f"{pos['side'].upper()} BTC\n"
                               f"Entree : `{pos['entry']:,.2f}` → Sortie : `{price_now:,.2f}`\n"
                               f"Resultat : `{pnl_pct:+.2f}%` sur marge"
                               + ("\n*Pause 2h activee.*" if is_locked(state) else ""))
                        else:
                            pnl_pct = (
                                (price_now - pos["entry"]) / pos["entry"] * 100 * config["leverage"]
                                if pos["side"] == "long"
                                else (pos["entry"] - price_now) / pos["entry"] * 100 * config["leverage"]
                            )
                            print(f"[{now_str}] Position {pos['side']} | {price_now:.2f} | PnL: {pnl_pct:+.2f}%")
                    else:
                        try:
                            df_t = fetch_klines(config["symbol_binance"], config["interval"], limit=150)
                            df_t = compute_supertrend(df_t, config["atr_period"], config["atr_mult"])
                            trail_st = float(df_t["supertrend"].iloc[-2])
                            current_sl = pos["sl"]
                            new_sl = round(max(current_sl, trail_st), 2) if pos["side"] == "long" else round(min(current_sl, trail_st), 2)
                            if new_sl != current_sl:
                                ex.set_tp_sl(pos["id"], sl_price=new_sl, tp_price=pos["tp2"], tp_fraction=100)
                                state["position"]["sl"] = new_sl
                                save_state(state)
                                tg(config["tg_token"], config["chat_id"],
                                   f"*TRAILING STOP AJUSTE*\n"
                                   f"{pos['side'].upper()} | Prix : `{price_now:.2f}`\n"
                                   f"SL : `{current_sl:,.2f}` → `{new_sl:,.2f}`\n"
                                   f"TP2 cible : `{pos['tp2']:,.2f}`")
                        except Exception as e:
                            print(f"[{now_str}] Trailing SL erreur : {e}")
                        print(f"[{now_str}] En attente TP2={pos['tp2']:.2f} | SL={pos['sl']:.2f} | Prix={price_now:.2f}")

            # ── 2. RECHERCHE DE SIGNAL (24h/24) ─────────────────────
            else:
                if is_locked(state):
                    lu = datetime.fromisoformat(state["locked_until"])
                    rem = int((lu - now_utc).total_seconds() / 60)
                    print(f"[{now_str}] Pause — reprise dans {rem} min.")
                else:
                    df = fetch_klines(config["symbol_binance"], config["interval"], limit=150)
                    price = float(df.iloc[-2]["close"])

                    df_1h = fetch_klines(config["symbol_binance"], "1h", limit=60)
                    htf_trend = get_htf_trend(df_1h, config["atr_period"], config["atr_mult"], config["ema_period"])
                    htf_label = "HAUSSIER" if htf_trend == 1 else "BAISSIER"

                    if htf_trend != state.get("last_htf_trend", 0):
                        state["last_htf_trend"] = htf_trend
                        save_state(state)
                        tg(config["tg_token"], config["chat_id"],
                           f"*TENDANCE 1H CHANGEE*\n"
                           f"SuperTrend 1h : *{htf_label}*\n"
                           f"BTC : `{price:,.2f}` USDT")

                    signal, fib_sl, fib_tp = build_fib05_signal(
                        df,
                        n_lookback=config["fib_lookback"],
                        n_side=config["fib_n_side"],
                        tolerance=config["fib_tolerance"],
                    )

                    if signal in ("LONG", "SHORT"):
                        fib_50 = round((fib_sl + fib_tp) / 2, 2)

                        if signal != state.get("last_signal"):
                            if (signal == "LONG" and htf_trend != 1) or (signal == "SHORT" and htf_trend != -1):
                                print(f"[{now_str}] Fib0.5 {signal} @ {fib_50} | Bloque: HTF {htf_label}")
                                tg(config["tg_token"], config["chat_id"],
                                   f"*FIB 0.5 DETECTE — Signal bloque*\n"
                                   f"Signal : `{signal}` | Zone 0.5 : `{fib_50:,.2f}`\n"
                                   f"SL : `{fib_sl:,.2f}` | TP : `{fib_tp:,.2f}`\n"
                                   f"Raison : Tendance 1h {htf_label}")
                            else:
                                dist = (price - fib_sl) if signal == "LONG" else (fib_sl - price)
                                if dist > 0:
                                    tp1 = round((price + fib_tp) / 2, 2)
                                    balance = ex.get_futures_balance()
                                    risk_usdt = balance * config["risk_pct"] / 100
                                    sl_pct = dist / price
                                    margin = max(5.0, min(round(risk_usdt / sl_pct / config["leverage"], 2), round(balance * 0.15, 2)))

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
                                        }
                                        state["last_signal"] = signal
                                        state["consecutive_losses"] = 0
                                        state["locked_until"] = None
                                        save_state(state)
                                        tg(config["tg_token"], config["chat_id"],
                                           f"*STRATEGIE 0.5 — {signal}*\n"
                                           f"Actif : `{config['symbol_moonx']}`\n"
                                           f"Entree (Fib 0.5) : `{fill_price:,.2f}` USDT\n"
                                           f"Marge : `{margin:.2f}` USDT | Levier : `{config['leverage']}x`\n"
                                           f"Tendance 1h : {htf_label}\n"
                                           f"---- Fibonacci ----\n"
                                           f"Niveau 0 (objectif) : `{fib_tp:,.2f}`\n"
                                           f"Niveau 0.5 (entree) : `{fib_50:,.2f}`\n"
                                           f"Niveau 1 (SL) : `{fib_sl:,.2f}`\n"
                                           f"---- Ordres ----\n"
                                           f"SL : `{fib_sl:,.2f}`\n"
                                           f"TP1 (50%) : `{real_tp1:,.2f}`\n"
                                           f"TP2 (100%) : `{fib_tp:,.2f}`")
                        else:
                            print(f"[{now_str}] Signal {signal} deja connu | Fib50={fib_50} | HTF: {htf_label}")
                    else:
                        print(f"[{now_str}] Pas de Fib0.5 | Prix: {price:.2f} | HTF: {htf_label}")

        except Exception as exc:
            print(f"[{now_str}] [ERREUR] {exc}")
            tg(config["tg_token"], config["chat_id"], f"Erreur bot : {exc}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
