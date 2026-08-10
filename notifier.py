"""
Envoi des alertes de trading vers Telegram.
Utilise l'API HTTP brute de Telegram (pas besoin de dépendance lourde).
"""

import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def send_signal_alert(token: str, chat_id: str, signal: str, symbol: str,
                       price: float, take_profit: float, stop_loss: float) -> None:
    """Envoie une alerte de signal d'entrée (LONG ou SHORT)."""
    emoji = "🟢" if signal == "LONG" else "🔴"
    direction = "ACHAT (LONG)" if signal == "LONG" else "VENTE (SHORT)"

    text = (
        f"{emoji} *SIGNAL {direction}*\n\n"
        f"Actif : `{symbol}`\n"
        f"Prix d'entrée : `{price:,.2f}` USDT\n"
        f"🎯 Take Profit (+1%) : `{take_profit:,.2f}` USDT\n"
        f"🛑 Stop Loss (-1%) : `{stop_loss:,.2f}` USDT\n\n"
        f"⚠️ Vérifie le marché avant d'agir. Ceci est une analyse technique, "
        f"pas un conseil financier personnalisé."
    )
    _send_message(token, chat_id, text)


def send_exit_alert(token: str, chat_id: str, reason: str, symbol: str, price: float) -> None:
    """Envoie une alerte de sortie de position (retournement du SuperTrend)."""
    text = (
        f"⚪ *SIGNAL DE SORTIE*\n\n"
        f"Actif : `{symbol}`\n"
        f"Prix actuel : `{price:,.2f}` USDT\n"
        f"Raison : {reason}\n\n"
        f"Pense à retirer ta position si elle est encore ouverte."
    )
    _send_message(token, chat_id, text)


def send_status_message(token: str, chat_id: str, text: str) -> None:
    """Envoie un message de statut simple (démarrage, erreur, etc.)."""
    _send_message(token, chat_id, text)


def _send_message(token: str, chat_id: str, text: str) -> None:
    url = TELEGRAM_API.format(token=token, method="sendMessage")
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"[ERREUR] Échec de l'envoi Telegram : {exc}")
