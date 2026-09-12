"""
Cycle unique GitHub Actions — Stratégie 0.5 (Fibonacci 50%) — 24h/24.
S'exécute toutes les 5 minutes via cron.

Corrections v3 :
  - Pas de sortie SuperTrend anticipée : MoonX gère le SL Fibonacci
  - Filtre 4h : SuperTrend 1h ET 4h doivent être alignés
  - TP direct Fib 0 (R:R 1:1) + Breakeven automatique à mi-chemin
"""

import json
import os
import sys
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
HEARTBEAT_INTERVAL_MIN = 60


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
        try:
            s = json.loads(STATE_FILE.read_text())
            # Migration : ancienne structure → nouvelle
            if "last_htf_trend" in s:
                s["last_htf_state"] = [s.pop("last_htf_trend"), 0]
            if "last_htf_state" not in s:
                s["last_htf_state"] = [0, 0]
            return s
        except Exception:
            pass
    return {
        "position": None,
        "consecutive_losses": 0,
        "locked_until": None,
        "last_signal": None,
        "last_heartbeat_ts": None,
        "last_htf_state": [0, 0],
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


def get_htf_labels(htf_1h: int, htf_4h: int) -> tuple:
    label_1h = "HAUSSIER" if htf_1h == 1 else "BAISSIER"
    label_4h = "HAUSSIER" if htf_4h == 1 else "BAISSIER"
    return label_1h, label_4h


def htf_allows(signal: str, htf_1h: int, htf_4h: int) -> bool:
    """Les deux timeframes doivent être alignés avec le signal."""
    if signal == "LONG":
        return htf_1h == 1 and htf_4h == 1
    if signal == "SHORT":
        return htf_1h == -1 and htf_4h == -1
    return False


def pnl_pct(pos: dict, price: float, leverage: int) -> float:
    if pos["side"] == "long":
        return (price - pos["entry"]) / pos["entry"] * 100 * leverage
    return (pos["entry"] - price) / pos["entry"] * 100 * leverage


def run():
    config = load_config()
    state = load_state()
    ex = MoonXExecutor(config["moonx_token"])
    now_str = datetime.now(PARIS_TZ).strftime("%H:%M:%S")
    now_utc = datetime.now(timezone.utc)

    print(f"[{now_str}] Cycle 24/7 | Position: {'oui' if state['position'] else 'non'} | Pertes: {state['consecutive_losses']}")

    try:
        # ── HEARTBEAT HORAIRE ────────────────────────────────────────
        if should_heartbeat(state):
            try:
                balance = ex.get_futures_balance()
                balance_str = f"{balance:.2f} USDT"
            except Exception:
                balance_str = "indisponible"

            df_hb = fetch_klines(config["symbol_binance"], config["interval"], limit=150)
            price_hb = float(df_hb.iloc[-2]["close"])
            df_1h_hb = fetch_klines(config["symbol_binance"], "1h", limit=60)
            df_4h_hb = fetch_klines(config["symbol_binance"], "4h", limit=30)
            h1 = get_htf_trend(df_1h_hb, config["atr_period"], config["atr_mult"], config["ema_period"])
            h4 = get_htf_trend(df_4h_hb, config["atr_period"], config["atr_mult"], config["ema_period"])
            l1, l4 = get_htf_labels(h1, h4)
            aligned = h1 == h4

            sig_hb, sl_hb, tp_hb = build_fib05_signal(
                df_hb, config["fib_lookback"], config["fib_n_side"], config["fib_tolerance"]
            )
            fib_str = (f"Fib 0.5 zone : `{round((sl_hb + tp_hb) / 2, 2):,.2f}`\n"
                       f"SL (Fib 1) : `{sl_hb:,.2f}` | TP (Fib 0) : `{tp_hb:,.2f}`") if sig_hb else "Aucun niveau Fib 0.5 actif"

            pos_str = (
                f"Position : `{state['position']['side'].upper()}` @ `{state['position']['entry']:,.2f}` | BE: {'actif' if state['position'].get('be_active') else 'inactif'}"
                if state.get("position") else "Pas de position ouverte"
            )
            lock_str = ""
            if is_locked(state):
                lu = datetime.fromisoformat(state["locked_until"]).astimezone(PARIS_TZ)
                lock_str = f"\nPause jusqu'a : `{lu.strftime('%H:%M')}`"

            tg(config["tg_token"], config["chat_id"],
               f"*STATUT HORAIRE*\n"
               f"BTC : `{price_hb:,.2f}` USDT\n"
               f"Tendance 1h : *{l1}* | 4h : *{l4}*\n"
               f"{'Alignees — bot actif' if aligned else 'Non alignees — bot en attente'}\n"
               f"{fib_str}\n"
               f"{pos_str}\n"
               f"Solde futures : `{balance_str}`\n"
               f"Pertes consecutives : `{state['consecutive_losses']}/{config['max_losses']}`"
               + lock_str)

            state["last_heartbeat_ts"] = now_utc.isoformat()
            save_state(state)

        # ── 1. SURVEILLANCE POSITION OUVERTE ────────────────────────
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
                    net = float(last_trade.get("pnl", 0)) - float(last_trade.get("feeAmount", 0))
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
                           f"Reprise a `{lu_p.strftime('%H:%M')}` (Paris)")
                else:
                    state["consecutive_losses"] = 0
                    state["locked_until"] = None

                state["position"] = None
                save_state(state)
                tg(config["tg_token"], config["chat_id"],
                   f"{'OK' if net >= 0 else 'NON'} *POSITION CLOTUREE*\n"
                   f"{pos['side'].upper()} BTC\n"
                   f"Entree : `{pos['entry']:,.2f}` | SL : `{pos['sl']:,.2f}` | TP : `{pos['tp']:,.2f}`\n"
                   f"Resultat net : {pnl_str}")
                return

            df_q = fetch_klines(config["symbol_binance"], config["interval"], limit=5)
            price_now = float(df_q.iloc[-1]["close"])
            p_pct = pnl_pct(pos, price_now, config["leverage"])

            # Vérifier activation du Breakeven (mi-chemin vers TP)
            if not pos.get("be_active", False):
                halfway_hit = (
                    (pos["side"] == "long" and price_now >= pos["halfway"]) or
                    (pos["side"] == "short" and price_now <= pos["halfway"])
                )
                if halfway_hit:
                    ex.set_tp_sl(pos["id"], sl_price=pos["entry"], tp_price=pos["tp"])
                    state["position"]["be_active"] = True
                    state["position"]["sl"] = pos["entry"]
                    save_state(state)
                    print(f"[{now_str}] Mi-chemin @ {price_now:.2f} | SL -> BE | TP -> {pos['tp']:.2f}")
                    tg(config["tg_token"], config["chat_id"],
                       f"*MI-CHEMIN ATTEINT — BREAKEVEN ACTIVE*\n"
                       f"{pos['side'].upper()} BTC | Prix : `{price_now:,.2f}`\n"
                       f"SL deplace a l'entree : `{pos['entry']:,.2f}`\n"
                       f"TP Fib 0 (objectif) : `{pos['tp']:,.2f}`\n"
                       f"PnL actuel : `{p_pct:+.2f}%`")
                else:
                    print(f"[{now_str}] Position {pos['side']} | Prix: {price_now:.2f} | PnL: {p_pct:+.2f}% | BE @ {pos['halfway']:.2f}")
            else:
                # BE actif → trailing stop SuperTrend 5m
                try:
                    df_t = fetch_klines(config["symbol_binance"], config["interval"], limit=150)
                    df_t = compute_supertrend(df_t, config["atr_period"], config["atr_mult"])
                    trail_st = float(df_t["supertrend"].iloc[-2])
                    current_sl = pos["sl"]
                    new_sl = round(max(current_sl, trail_st), 2) if pos["side"] == "long" else round(min(current_sl, trail_st), 2)
                    if new_sl != current_sl:
                        ex.set_tp_sl(pos["id"], sl_price=new_sl, tp_price=pos["tp"])
                        state["position"]["sl"] = new_sl
                        save_state(state)
                        print(f"[{now_str}] Trailing SL: {current_sl:.2f} -> {new_sl:.2f}")
                        tg(config["tg_token"], config["chat_id"],
                           f"*TRAILING STOP AJUSTE*\n"
                           f"{pos['side'].upper()} BTC | Prix : `{price_now:.2f}`\n"
                           f"SL : `{current_sl:,.2f}` → `{new_sl:,.2f}`\n"
                           f"TP Fib 0 : `{pos['tp']:,.2f}` | PnL : `{p_pct:+.2f}%`")
                except Exception as e:
                    print(f"[{now_str}] Trailing SL erreur : {e}")
                print(f"[{now_str}] BE actif | TP={pos['tp']:.2f} | SL trailing={pos['sl']:.2f} | PnL: {p_pct:+.2f}%")

        # ── 2. RECHERCHE DE SIGNAL (24h/24) ─────────────────────────
        else:
            if is_locked(state):
                lu = datetime.fromisoformat(state["locked_until"])
                rem = int((lu - now_utc).total_seconds() / 60)
                print(f"[{now_str}] Pause active — reprise dans {rem} min.")
                return

            df = fetch_klines(config["symbol_binance"], config["interval"], limit=150)
            price = float(df.iloc[-2]["close"])

            # Tendances 1h et 4h
            df_1h = fetch_klines(config["symbol_binance"], "1h", limit=60)
            df_4h = fetch_klines(config["symbol_binance"], "4h", limit=30)
            htf_1h = get_htf_trend(df_1h, config["atr_period"], config["atr_mult"], config["ema_period"])
            htf_4h = get_htf_trend(df_4h, config["atr_period"], config["atr_mult"], config["ema_period"])
            lbl_1h, lbl_4h = get_htf_labels(htf_1h, htf_4h)
            aligned = htf_1h == htf_4h

            # Notification si les tendances changent
            htf_state = [htf_1h, htf_4h]
            if htf_state != state.get("last_htf_state", [0, 0]):
                state["last_htf_state"] = htf_state
                save_state(state)
                tg(config["tg_token"], config["chat_id"],
                   f"*TENDANCES CHANGEES*\n"
                   f"1h : *{lbl_1h}* | 4h : *{lbl_4h}*\n"
                   f"BTC : `{price:,.2f}` USDT\n"
                   f"{'Alignees — bot cherche un signal' if aligned else 'Non alignees — bot en attente dalignement'}")

            # Signal Stratégie 0.5
            signal, fib_sl, fib_tp = build_fib05_signal(
                df,
                n_lookback=config["fib_lookback"],
                n_side=config["fib_n_side"],
                tolerance=config["fib_tolerance"],
            )

            if signal in ("LONG", "SHORT"):
                fib_50 = round((fib_sl + fib_tp) / 2, 2)

                if signal != state.get("last_signal"):
                    # Filtre HTF : 1h ET 4h doivent confirmer
                    if not htf_allows(signal, htf_1h, htf_4h):
                        print(f"[{now_str}] Fib0.5 {signal} @ {fib_50} | Bloque: 1h={lbl_1h} 4h={lbl_4h}")
                        tg(config["tg_token"], config["chat_id"],
                           f"*FIB 0.5 DETECTE — Signal bloque*\n"
                           f"Signal : `{signal}` | Zone 0.5 : `{fib_50:,.2f}`\n"
                           f"SL (Fib 1) : `{fib_sl:,.2f}` | TP (Fib 0) : `{fib_tp:,.2f}`\n"
                           f"1h : {lbl_1h} | 4h : {lbl_4h} — non alignes")
                        return

                    dist = (price - fib_sl) if signal == "LONG" else (fib_sl - price)
                    if dist <= 0:
                        print(f"[{now_str}] Distance SL nulle, signal ignore.")
                        return

                    halfway = round((price + fib_tp) / 2, 2)

                    balance = ex.get_futures_balance()
                    risk_usdt = balance * config["risk_pct"] / 100
                    sl_pct = dist / price
                    margin_calc = round(risk_usdt / sl_pct / config["leverage"], 2)
                    max_margin = round(balance * 0.15, 2)
                    margin = max(5.0, min(margin_calc, max_margin))

                    # TP direct à Fib 0 (R:R 1:1), pas de partial close
                    pos_id = ex.open_position(
                        side=signal.lower(),
                        symbol=config["symbol_moonx"],
                        margin_usdt=margin,
                        leverage=config["leverage"],
                        sl_price=fib_sl,
                        tp_price=fib_tp,
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

                        real_halfway = round((fill_price + fib_tp) / 2, 2)

                        state["position"] = {
                            "id": pos_id,
                            "side": signal.lower(),
                            "entry": fill_price,
                            "sl": fib_sl,
                            "tp": fib_tp,
                            "halfway": real_halfway,
                            "be_active": False,
                        }
                        state["last_signal"] = signal
                        state["consecutive_losses"] = 0
                        state["locked_until"] = None
                        save_state(state)

                        print(f"[{now_str}] STRATEGIE 0.5 {signal} @ {fill_price:.2f} | SL={fib_sl:.2f} | TP={fib_tp:.2f} | BE@{real_halfway:.2f}")
                        tg(config["tg_token"], config["chat_id"],
                           f"*STRATEGIE 0.5 — {signal}*\n"
                           f"Actif : `{config['symbol_moonx']}`\n"
                           f"Entree (Fib 0.5) : `{fill_price:,.2f}` USDT\n"
                           f"Marge : `{margin:.2f}` USDT | Levier : `{config['leverage']}x`\n"
                           f"Tendances : 1h *{lbl_1h}* | 4h *{lbl_4h}*\n"
                           f"---- Fibonacci ----\n"
                           f"Niveau 0 — TP : `{fib_tp:,.2f}` (R:R 1:1)\n"
                           f"Niveau 0.5 — Entree : `{fib_50:,.2f}`\n"
                           f"Niveau 1 — SL : `{fib_sl:,.2f}`\n"
                           f"---- Gestion ----\n"
                           f"SL : `{fib_sl:,.2f}` (fixe jusqu'au mi-chemin)\n"
                           f"BE auto @ : `{real_halfway:,.2f}`\n"
                           f"TP : `{fib_tp:,.2f}` (100%)")
                    else:
                        print(f"[{now_str}] Signal {signal} detecte mais positionId non recu.")
                else:
                    print(f"[{now_str}] Signal {signal} deja connu | Fib50={fib_50} | 1h={lbl_1h} 4h={lbl_4h}")
            else:
                print(f"[{now_str}] Pas de Fib0.5 | Prix: {price:.2f} | 1h={lbl_1h} 4h={lbl_4h}")

    except Exception as exc:
        print(f"[{now_str}] [ERREUR] {exc}")
        tg(config["tg_token"], config["chat_id"], f"Erreur bot : {exc}")
        sys.exit(1)


if __name__ == "__main__":
    run()
