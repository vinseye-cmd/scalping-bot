"""
Bot de scalping 100% autonome - SuperTrend + EMA -> execution directe sur MoonX.
Aucune validation humaine requise. Lancement : python auto_bot.py
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytz
from dotenv import load_dotenv

from executor import MoonXExecutor
from indicators import build_signal, get_htf_trend
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
        "interval": os.getenv("INTERVAL", "1m"),
        "atr_period": int(os.getenv("ATR_PERIOD", 10)),
        "atr_mult": float(os.getenv("ATR_MULTIPLIER", 2.0)),
        "ema_period": int(os.getenv("EMA_PERIOD", 21)),
        "leverage": int(os.getenv("LEVERAGE", 10)),
        "risk_pct": float(os.getenv("RISK_PCT", 1.0)),
        "rr_tp1": float(os.getenv("RR_TP1", 1.0)),
        "rr_tp2": float(os.getenv("RR_TP2", 1.5)),
        "max_losses": int(os.getenv("MAX_CONSECUTIVE_LOSSES", 2)),
        "trading_windows": os.getenv("TRADING_WINDOWS", "09:00-13:00,14:00-17:00"),
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

    print(f"Bot Auto-Scalp demarre | Solde futures : {balance_str}")
    tg(config["tg_token"], config["chat_id"],
       f"*Bot Auto-Scalp ACTIF*\n"
       f"Actif : `{config['symbol_moonx']}`\n"
       f"Fenetres : `{config['trading_windows']}` (Paris)\n"
       f"Levier : `{config['leverage']}x` | Risque/trade : `{config['risk_pct']}%`\n"
       f"Solde futures : `{balance_str}`")

    while True:
        now_str = datetime.now(PARIS_TZ).strftime("%H:%M:%S")
        sess_idx = current_session_idx(windows)
        active = sess_idx >= 0

        try:
            # ── 1. SURVEILLANCE DE LA POSITION OUVERTE ──────────────────
            if state["position"]:
                pos = state["position"]

                # Vérifier que la position existe encore sur MoonX
                try:
                    live = ex.get_open_positions()
                    live_ids = [str(p.get("positionId", p.get("id", p.get("_id", "")))) for p in live]
                    position_exists = pos["id"] in live_ids
                except Exception:
                    position_exists = True  # si la vérification échoue, on assume ouverte

                if not position_exists:
                    state["position"] = None
                    save_state(state)
                    print(f"[{now_str}] Position cloturee par MoonX (SL/TP auto).")
                    tg(config["tg_token"], config["chat_id"],
                       f"*POSITION CLOTUREE PAR MOONX*\n"
                       f"La position {pos['side'].upper()} a ete fermee automatiquement (SL ou TP atteint).\n"
                       f"Entree : `{pos['entry']:,.2f}` | SL : `{pos['sl']:,.2f}` | TP1 : `{pos['tp1']:,.2f}`")
                else:
                    df_quick = fetch_klines(config["symbol_binance"], config["interval"], limit=5)
                    price_now = float(df_quick.iloc[-1]["close"])

                    # TP1 atteint ?
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
                            print(f"[{now_str}] TP1 @ {price_now:.2f} | SL -> BE {pos['entry']:.2f} | TP2 -> {pos['tp2']:.2f}")
                            tg(config["tg_token"], config["chat_id"],
                               f"*TP1 ATTEINT - BREAKEVEN ACTIVE*\n"
                               f"Prix : `{price_now:,.2f}`\n"
                               f"SL deplace a l'entree : `{pos['entry']:,.2f}`\n"
                               f"Objectif TP2 : `{pos['tp2']:,.2f}`")

                    # Retournement SuperTrend avant TP1 -> sortie defensive
                    if not pos["tp1_hit"]:
                        df_full = fetch_klines(config["symbol_binance"], config["interval"], limit=150)
                        df_full = build_signal(df_full, config["atr_period"], config["atr_mult"], config["ema_period"])
                        last = df_full.iloc[-2]
                        reversed_ = (
                            (pos["side"] == "long" and int(last["trend"]) == -1) or
                            (pos["side"] == "short" and int(last["trend"]) == 1)
                        )
                        if reversed_:
                            try:
                                ex.close_position(pos["id"], percentage=100)
                            except Exception:
                                pass  # position déjà fermée par MoonX
                            state["consecutive_losses"] += 1
                            was_locked = state["consecutive_losses"] >= config["max_losses"]
                            if was_locked:
                                state["locked_session_idx"] = sess_idx
                            state["position"] = None
                            save_state(state)
                            print(f"[{now_str}] Sortie SuperTrend @ {price_now:.2f} | Pertes : {state['consecutive_losses']}")
                            tg(config["tg_token"], config["chat_id"],
                               f"*SORTIE AUTO - RETOURNEMENT SUPERTREND*\n"
                               f"Prix de sortie : `{price_now:,.2f}`\n"
                               + ("*Session verrouillee (2 pertes). Reprise a la prochaine fenetre.*" if was_locked else ""))
                        else:
                            print(f"[{now_str}] Position {pos['side']} ouverte | Prix : {price_now:.2f}")
                    else:
                        print(f"[{now_str}] En attente TP2 ({pos['tp2']:.2f}) | Prix : {price_now:.2f}")

            # ── 2. RECHERCHE D'UN SIGNAL ─────────────────────────────────
            elif active:
                if state["locked_session_idx"] == sess_idx:
                    print(f"[{now_str}] Session verrouillee. Pas d'ordre.")
                else:
                    df = fetch_klines(config["symbol_binance"], config["interval"], limit=150)
                    df = build_signal(df, config["atr_period"], config["atr_mult"], config["ema_period"])
                    last = df.iloc[-2]
                    signal = last["signal"]
                    price = float(last["close"])
                    st_level = float(last["supertrend"])

                    # Filtre de tendance 1h : on n'ouvre que dans le sens de la tendance dominante
                    df_1h = fetch_klines(config["symbol_binance"], "1h", limit=60)
                    htf_trend = get_htf_trend(df_1h, config["atr_period"], config["atr_mult"])
                    htf_label = "haussier ↑" if htf_trend == 1 else "baissier ↓"

                    if signal in ("LONG", "SHORT") and signal != state.get("last_signal"):
                        if (signal == "LONG" and htf_trend == -1) or (signal == "SHORT" and htf_trend == 1):
                            print(f"[{now_str}] Signal {signal} ignoré — tendance 1h {htf_label}")
                            continue

                    if signal in ("LONG", "SHORT") and signal != state.get("last_signal"):
                        if signal == "LONG":
                            sl = st_level
                            dist = price - sl
                            tp1 = price + dist * config["rr_tp1"]
                            tp2 = price + dist * config["rr_tp2"]
                        else:
                            sl = st_level
                            dist = sl - price
                            tp1 = price - dist * config["rr_tp1"]
                            tp2 = price - dist * config["rr_tp2"]

                        if dist <= 0:
                            print(f"[{now_str}] Distance SL nulle, signal ignore.")
                        else:
                            balance = ex.get_futures_balance()
                            risk_usdt = balance * config["risk_pct"] / 100
                            sl_pct = dist / price
                            margin_calc = round(risk_usdt / sl_pct / config["leverage"], 2)
                            max_margin = round(balance * 0.15, 2)  # jamais plus de 15% du wallet
                            margin = max(5.0, min(margin_calc, max_margin))

                            pos_id = ex.open_position(
                                side=signal.lower(),
                                symbol=config["symbol_moonx"],
                                margin_usdt=margin,
                                leverage=config["leverage"],
                                sl_price=round(sl, 2),
                                tp_price=round(tp1, 2),
                            )

                            if pos_id:
                                # Récupérer le prix de remplissage réel sur MoonX
                                try:
                                    live = ex.get_open_positions()
                                    fill_price = next(
                                        (float(p["entryPrice"]) for p in live
                                         if str(p.get("positionId", p.get("id", p.get("_id", "")))) == pos_id),
                                        price
                                    )
                                except Exception:
                                    fill_price = price

                                # Recalculer TP/SL depuis le prix réel de remplissage
                                if signal == "LONG":
                                    real_dist = fill_price - sl
                                    real_tp1 = fill_price + real_dist * config["rr_tp1"]
                                    real_tp2 = fill_price + real_dist * config["rr_tp2"]
                                else:
                                    real_dist = sl - fill_price
                                    real_tp1 = fill_price - real_dist * config["rr_tp1"]
                                    real_tp2 = fill_price - real_dist * config["rr_tp2"]

                                if real_dist > 0:
                                    ex.set_tp_sl(pos_id, tp_price=round(real_tp1, 2), sl_price=round(sl, 2), tp_fraction=50)
                                else:
                                    ex.set_tp_sl(pos_id, tp_price=round(tp1, 2), sl_price=round(sl, 2), tp_fraction=50)
                                    real_tp1, real_tp2 = tp1, tp2

                                state["position"] = {
                                    "id": pos_id,
                                    "side": signal.lower(),
                                    "entry": fill_price,
                                    "sl": round(sl, 2),
                                    "tp1": round(real_tp1, 2),
                                    "tp2": round(real_tp2, 2),
                                    "tp1_hit": False,
                                    "session_idx": sess_idx,
                                }
                                state["last_signal"] = signal
                                state["consecutive_losses"] = 0
                                save_state(state)
                                print(f"[{now_str}] ORDRE {signal} @ {fill_price:.2f} | SL={sl:.2f} | TP1={real_tp1:.2f} | Marge={margin:.2f} USDT")
                                tg(config["tg_token"], config["chat_id"],
                                   f"*ORDRE AUTO EXECUTE - {signal}*\n"
                                   f"Actif : `{config['symbol_moonx']}`\n"
                                   f"Prix entree : `{fill_price:,.2f}` USDT\n"
                                   f"Marge : `{margin:.2f}` USDT | Levier : `{config['leverage']}x`\n"
                                   f"SL : `{sl:,.2f}`\n"
                                   f"TP1 (50%, BE) : `{real_tp1:,.2f}`\n"
                                   f"TP2 (100%) : `{real_tp2:,.2f}`")
                            else:
                                print(f"[{now_str}] Signal {signal} detecte mais aucun positionId recu.")
                    else:
                        print(f"[{now_str}] Pas de signal | Prix : {price:.2f} | Tendance : {int(last['trend'])}")

            # ── 3. HORS FENETRE ──────────────────────────────────────────
            else:
                if state["locked_session_idx"] >= 0 and state["locked_session_idx"] != sess_idx:
                    state["locked_session_idx"] = -1
                    state["consecutive_losses"] = 0
                    save_state(state)
                print(f"[{now_str}] Hors fenetre horaire.")

        except Exception as exc:
            print(f"[{now_str}] [ERREUR] {exc}")
            tg(config["tg_token"], config["chat_id"], f"Erreur bot : {exc}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run()
