"""
Cycle unique pour GitHub Actions.
S'exécute une fois, vérifie le marché, agit si nécessaire, puis quitte.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import pytz
from dotenv import load_dotenv

from executor import MoonXExecutor
from indicators import build_signal
from market_data import fetch_klines
from notifier import send_status_message

STATE_FILE = Path(__file__).parent / "state_auto.json"
PARIS_TZ = pytz.timezone("Europe/Paris")


def load_config() -> dict:
    load_dotenv()
    required = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "MOONX_API_TOKEN"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise RuntimeError(f"Variables manquantes : {', '.join(missing)}")
    return {
        "tg_token": os.getenv("TELEGRAM_BOT_TOKEN"),
        "chat_id": os.getenv("TELEGRAM_CHAT_ID"),
        "moonx_token": os.getenv("MOONX_API_TOKEN"),
        "symbol_binance": os.getenv("SYMBOL", "BTCUSDT"),
        "symbol_moonx": os.getenv("MOONX_SYMBOL", "BTC"),
        "interval": os.getenv("INTERVAL", "5m"),
        "atr_period": int(os.getenv("ATR_PERIOD", 10)),
        "atr_mult": float(os.getenv("ATR_MULTIPLIER", 2.5)),
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


def current_session_idx(windows: list) -> int:
    now = datetime.now(PARIS_TZ).time()
    for i, (s, e) in enumerate(windows):
        if s <= now <= e:
            return i
    return -1


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"position": None, "consecutive_losses": 0, "locked_session_idx": -1, "last_signal": None, "last_heartbeat_date": None}


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
    now_str = datetime.now(PARIS_TZ).strftime("%H:%M:%S")
    sess_idx = current_session_idx(windows)
    active = sess_idx >= 0

    print(f"[{now_str}] Cycle | Fenetre: {'active' if active else 'inactive'} | Position: {'oui' if state['position'] else 'non'}")

    # Heartbeat quotidien à 09h00 Paris
    now_paris = datetime.now(PARIS_TZ)
    today_str = now_paris.strftime("%Y-%m-%d")
    if now_paris.hour == 9 and now_paris.minute < 5 and state.get("last_heartbeat_date") != today_str:
        try:
            balance = ex.get_futures_balance()
            balance_str = f"{balance:.2f} USDT"
        except Exception:
            balance_str = "indisponible"
        tg(config["tg_token"], config["chat_id"],
           f"*Bot de scalping ACTIF*\n"
           f"Surveillance BTC en cours...\n"
           f"Fenetres : `09:00-13:00` | `14:00-17:00` (Paris)\n"
           f"Solde futures : `{balance_str}`\n"
           f"Pertes consecutives : `{state['consecutive_losses']}/{config['max_losses']}`")
        state["last_heartbeat_date"] = today_str
        save_state(state)

    try:
        # ── 1. SURVEILLANCE DE LA POSITION OUVERTE ──────────────────
        if state["position"]:
            pos = state["position"]

            try:
                live = ex.get_open_positions()
                live_ids = [str(p.get("positionId", p.get("id", p.get("_id", "")))) for p in live]
                position_exists = pos["id"] in live_ids
            except Exception:
                position_exists = True

            if not position_exists:
                # Récupérer le PnL réalisé depuis l'historique MoonX
                try:
                    history = ex._call("get_futures_trade_history")
                    last_trade = history[0] if isinstance(history, list) and history else {}
                    pnl_realise = float(last_trade.get("pnl", 0))
                    fee = float(last_trade.get("feeAmount", 0))
                    net = pnl_realise - fee
                    pnl_str = f"`{net:+.4f} USDT` ({'gain' if net >= 0 else 'perte'})"
                except Exception:
                    pnl_str = "indisponible"
                state["position"] = None
                save_state(state)
                print(f"[{now_str}] Position cloturee par MoonX (SL/TP auto).")
                tg(config["tg_token"], config["chat_id"],
                   f"{'✅' if net >= 0 else '❌'} *POSITION CLOTUREE*\n"
                   f"{pos['side'].upper()} BTC ferme automatiquement\n"
                   f"Entree : `{pos['entry']:,.2f}` USDT\n"
                   f"SL : `{pos['sl']:,.2f}` | TP1 : `{pos['tp1']:,.2f}`\n"
                   f"Resultat net : {pnl_str}")
                return

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
                       f"Prix : `{price_now:,.2f}`\n"
                       f"SL deplace a l'entree : `{pos['entry']:,.2f}`\n"
                       f"Objectif TP2 : `{pos['tp2']:,.2f}`")

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
                        pass
                    if pos["side"] == "long":
                        pnl_pct = (price_now - pos["entry"]) / pos["entry"] * 100 * config["leverage"]
                    else:
                        pnl_pct = (pos["entry"] - price_now) / pos["entry"] * 100 * config["leverage"]
                    state["consecutive_losses"] += 1
                    was_locked = state["consecutive_losses"] >= config["max_losses"]
                    if was_locked:
                        state["locked_session_idx"] = sess_idx
                    state["position"] = None
                    save_state(state)
                    print(f"[{now_str}] Sortie SuperTrend @ {price_now:.2f} | Pertes : {state['consecutive_losses']}")
                    tg(config["tg_token"], config["chat_id"],
                       f"{'✅' if pnl_pct >= 0 else '❌'} *SORTIE - RETOURNEMENT SUPERTREND*\n"
                       f"{pos['side'].upper()} BTC ferme\n"
                       f"Entree : `{pos['entry']:,.2f}` → Sortie : `{price_now:,.2f}`\n"
                       f"Resultat : `{pnl_pct:+.2f}%` sur marge\n"
                       + ("*Session verrouillee. Reprise a la prochaine fenetre.*" if was_locked else ""))
                else:
                    pnl_pct = (price_now - pos["entry"]) / pos["entry"] * 100 * config["leverage"] if pos["side"] == "long" else (pos["entry"] - price_now) / pos["entry"] * 100 * config["leverage"]
                    print(f"[{now_str}] Position {pos['side']} en cours | Prix : {price_now:.2f} | PnL : {pnl_pct:+.2f}%")
                    tg(config["tg_token"], config["chat_id"],
                       f"📊 *Position en cours*\n"
                       f"{pos['side'].upper()} BTC | Prix : `{price_now:,.2f}`\n"
                       f"Entree : `{pos['entry']:,.2f}` | PnL : `{pnl_pct:+.2f}%`\n"
                       f"SL : `{pos['sl']:,.2f}` | TP1 : `{pos['tp1']:,.2f}`")
            else:
                pnl_pct = (price_now - pos["entry"]) / pos["entry"] * 100 * config["leverage"] if pos["side"] == "long" else (pos["entry"] - price_now) / pos["entry"] * 100 * config["leverage"]
                print(f"[{now_str}] En attente TP2 ({pos['tp2']:.2f}) | Prix : {price_now:.2f}")
                tg(config["tg_token"], config["chat_id"],
                   f"📊 *En attente TP2*\n"
                   f"{pos['side'].upper()} BTC | Prix : `{price_now:,.2f}`\n"
                   f"PnL : `{pnl_pct:+.2f}%` | BE actif\n"
                   f"TP2 cible : `{pos['tp2']:,.2f}`")

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

                trend_str = "haussier ↑" if int(last["trend"]) == 1 else "baissier ↓"
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
                            ex.set_tp_sl(pos_id, tp_price=round(tp1, 2), sl_price=round(sl, 2), tp_fraction=50)
                            state["position"] = {
                                "id": pos_id,
                                "side": signal.lower(),
                                "entry": price,
                                "sl": round(sl, 2),
                                "tp1": round(tp1, 2),
                                "tp2": round(tp2, 2),
                                "tp1_hit": False,
                                "session_idx": sess_idx,
                            }
                            state["last_signal"] = signal
                            state["consecutive_losses"] = 0
                            save_state(state)
                            print(f"[{now_str}] ORDRE {signal} @ {price:.2f} | SL={sl:.2f} | TP1={tp1:.2f} | Marge={margin:.2f} USDT")
                            tg(config["tg_token"], config["chat_id"],
                               f"*ORDRE AUTO EXECUTE - {signal}*\n"
                               f"Actif : `{config['symbol_moonx']}`\n"
                               f"Prix entree : `{price:,.2f}` USDT\n"
                               f"Marge : `{margin:.2f}` USDT | Levier : `{config['leverage']}x`\n"
                               f"SL : `{sl:,.2f}`\n"
                               f"TP1 (50%, BE) : `{tp1:,.2f}`\n"
                               f"TP2 (100%) : `{tp2:,.2f}`")
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
        sys.exit(1)


if __name__ == "__main__":
    run()
