"""Live provider transport adapters for TradELATIN Input.

This module mirrors the callable interface exposed by the fixture emulator:

    fetcher(**request) -> provider-shaped response

Input owns request planning and normalization.  This layer only performs the
provider transport (REST / WebSocket) and returns the provider response.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
from typing import Any, Mapping
from urllib.parse import urljoin

import requests

try:
    import websocket  # websocket-client
except ImportError:  # pragma: no cover - checked only in live WS mode
    websocket = None

COINGLASS_REST_BASE_URL = os.getenv("COINGLASS_BASE_URL", "https://open-api-v4.coinglass.com")
COINGLASS_WS_BASE_URL = os.getenv("COINGLASS_WS_BASE_URL", "wss://open-ws.coinglass.com/ws-api")
CRYPTOQUANT_BASE_URL = os.getenv("CRYPTOQUANT_BASE_URL", "https://api.cryptoquant.com/v1")
GLASSNODE_BASE_URL = os.getenv("GLASSNODE_BASE_URL", "https://api.glassnode.com")

SUPPORTED_FAMILIES = (
    "prices_ohlcv",
    "cvd_volume_orderflow",
    "open_interest_and_funding",
    "etf_exchange_flows",
    "on_chain_miners",
    "volatility_market_regimes",
    "long_short_liquidations",
    "liquidity_microstructure",
)


def _required_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    raise RuntimeError(f"Missing live API credential. Set one of: {', '.join(names)}")


def _clean_params(params: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in params.items() if value is not None}


def _get_json(*, base_url: str, path: str, params: Mapping[str, Any], headers: Mapping[str, str], timeout: float) -> Any:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    response = requests.get(url, params=_clean_params(params), headers=dict(headers), timeout=timeout)
    response.raise_for_status()
    return response.json()


def _coinglass_ws(request: Mapping[str, Any], *, timeout: float, capture_seconds: float) -> dict[str, Any]:
    if websocket is None:
        raise RuntimeError("Live CoinGlass WebSocket requires websocket-client. Install project dependencies with `pip install -e .`.")
    api_key = _required_env("COINGLASS_API_KEY")
    channel = str(request.get("channel") or "").strip()
    if not channel:
        raise ValueError("CoinGlass WebSocket request is missing channel")

    url = f"{COINGLASS_WS_BASE_URL}?cg-api-key={api_key}"
    ws = websocket.create_connection(url, timeout=max(1.0, timeout))
    records: list[Any] = []
    deadline = time.monotonic() + max(0.25, capture_seconds)
    try:
        ws.send(json.dumps({"method": "subscribe", "channels": [channel]}))
        ws.settimeout(min(1.0, max(0.1, capture_seconds)))
        while time.monotonic() < deadline:
            try:
                message = ws.recv()
            except Exception as exc:  # websocket timeout is expected while collecting
                timeout_cls = getattr(websocket, "WebSocketTimeoutException", ())
                if timeout_cls and isinstance(exc, timeout_cls):
                    continue
                raise
            if not message:
                continue
            payload = json.loads(message)
            if not isinstance(payload, Mapping):
                continue
            if payload.get("channel") != channel:
                continue
            data = payload.get("data")
            if isinstance(data, list):
                records.extend(data)
    finally:
        try:
            ws.send(json.dumps({"method": "unsubscribe", "channels": [channel]}))
        except Exception:
            pass
        ws.close()
    return {"channel": channel, "data": records}


@dataclass(frozen=True)
class FamilyRealFetcher:
    family: str
    timeout_seconds: float = 30.0
    websocket_capture_seconds: float = 3.0

    def __call__(self, **request: Any) -> Any:
        provider = str(request.get("provider", "")).strip().lower()
        path = str(request.get("path") or "")
        params = request.get("params") or {}
        if not isinstance(params, Mapping):
            raise TypeError("params must be a mapping")

        if provider == "coinglass":
            if str(request.get("transport", "rest")).lower() == "websocket":
                return _coinglass_ws(
                    request,
                    timeout=self.timeout_seconds,
                    capture_seconds=self.websocket_capture_seconds,
                )
            api_key = _required_env("COINGLASS_API_KEY")
            return _get_json(
                base_url=COINGLASS_REST_BASE_URL,
                path=path,
                params=params,
                headers={"accept": "application/json", "CG-API-KEY": api_key},
                timeout=self.timeout_seconds,
            )

        if provider == "cryptoquant":
            token = _required_env("CRYPTOQUANT_ACCESS_TOKEN", "CRYPTOQUANT_API_KEY")
            return _get_json(
                base_url=CRYPTOQUANT_BASE_URL,
                path=path,
                params=params,
                headers={"accept": "application/json", "Authorization": f"Bearer {token}"},
                timeout=self.timeout_seconds,
            )

        if provider == "glassnode":
            api_key = _required_env("GLASSNODE_API_KEY")
            return _get_json(
                base_url=GLASSNODE_BASE_URL,
                path=path,
                params=params,
                headers={"accept": "application/json", "X-Api-Key": api_key},
                timeout=self.timeout_seconds,
            )

        raise ValueError(f"unsupported provider: {provider}")


class RealProviderRouter:
    """Live API router with the same family fetcher interface as the emulator."""

    def __init__(self, *, timeout_seconds: float = 30.0, websocket_capture_seconds: float | None = None) -> None:
        self.timeout_seconds = float(timeout_seconds)
        configured = os.getenv("COINGLASS_WS_CAPTURE_SECONDS") if websocket_capture_seconds is None else websocket_capture_seconds
        self.websocket_capture_seconds = float(configured or 3.0)

    def for_family(self, family: str) -> FamilyRealFetcher:
        if family not in SUPPORTED_FAMILIES:
            raise ValueError(f"unsupported family: {family}")
        return FamilyRealFetcher(
            family=family,
            timeout_seconds=self.timeout_seconds,
            websocket_capture_seconds=self.websocket_capture_seconds,
        )
