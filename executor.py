"""
Couche d'execution MoonX via JSON-RPC over HTTP (protocole MCP stateless).
Utilise requests avec truststore pour la compatibilite SSL Windows.
"""

import json
import warnings
from typing import Optional

import requests

warnings.filterwarnings("ignore", message="Unverified HTTPS request")

MOONX_MCP_URL = "https://api.moon-x.io/mcp"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Mcp-Protocol-Version": "2024-11-05",
}
_req_id = 0


def _call_tool(api_token: str, tool_name: str, arguments: dict) -> dict:
    """Appelle un outil MoonX via JSON-RPC HTTP et retourne le resultat."""
    global _req_id
    _req_id += 1

    url = f"{MOONX_MCP_URL}?token={api_token}"
    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
        "id": _req_id,
    }

    resp = requests.post(url, json=payload, headers=HEADERS, timeout=15, verify=False)
    resp.raise_for_status()
    data = resp.json()

    if "error" in data:
        raise RuntimeError(f"MoonX error on {tool_name}: {data['error']}")

    content = data.get("result", {}).get("content", [])
    if not content:
        return {}
    try:
        return json.loads(content[0]["text"])
    except Exception:
        return {"raw": content[0].get("text", "")}


class MoonXExecutor:
    def __init__(self, api_token: str):
        self.token = api_token

    def _call(self, tool: str, params: dict = None) -> dict:
        return _call_tool(self.token, tool, params or {})

    def get_futures_balance(self) -> float:
        """Retourne le solde disponible dans le wallet futures (USDT)."""
        data = self._call("get_account_overview")
        futures = data.get("futuresBalances", {})
        return float(futures.get("USDT", 0))

    def open_position(
        self,
        side: str,
        symbol: str,
        margin_usdt: float,
        leverage: int,
        sl_price: float,
        tp_price: float,
    ) -> Optional[str]:
        """Ouvre une position futures et retourne son positionId."""
        result = self._call("open_futures_position", {
            "side": side,
            "symbol": symbol,
            "marginUsdt": margin_usdt,
            "leverage": leverage,
            "stopLossPrice": sl_price,
            "takeProfitPrice": tp_price,
            "marginMode": "isolated",
            "mode": "futures",
        })
        for key in ("positionId", "id", "position_id", "orderId"):
            if key in result:
                return str(result[key])
        return None

    def set_tp_sl(
        self,
        position_id: str,
        tp_price: Optional[float] = None,
        sl_price: Optional[float] = None,
        tp_fraction: int = 100,
        sl_fraction: int = 100,
    ):
        """Met a jour TP et/ou SL sur une position ouverte."""
        params: dict = {"positionId": position_id}
        if tp_price is not None:
            params["takeProfitPrice"] = tp_price
            params["takeProfitCloseFraction"] = tp_fraction
        if sl_price is not None:
            params["stopLossPrice"] = sl_price
            params["stopLossCloseFraction"] = sl_fraction
        self._call("set_futures_tp_sl", params)

    def close_position(self, position_id: str, percentage: float = 100):
        """Cloture une position (totalement ou partiellement)."""
        self._call("close_futures_position", {
            "positionId": position_id,
            "percentage": percentage,
        })

    def get_open_positions(self) -> list:
        """Retourne la liste des positions futures ouvertes."""
        data = self._call("list_futures_positions", {"mode": "futures"})
        if isinstance(data, list):
            return data
        return data.get("positions", data.get("data", []))
