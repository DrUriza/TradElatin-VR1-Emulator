"""
Utilidades compartidas de filtrado de historial (sección 12 de la spec).

Cada proveedor usa nombres de parámetros distintos (start_time/end_time,
from/to, s/u) pero todos terminan filtrando por rango de timestamp y
recortando por `limit`. Esta función centraliza esa lógica para que los
tres serializadores se comporten de forma consistente.
"""

from app.core.market_state import MarketState


class InvalidQueryParameter(Exception):
    """Se lanza cuando un parámetro de query no es válido (-> HTTP 400)."""


def filter_by_time_range(
    history: list[MarketState],
    start_timestamp: int | None,
    end_timestamp: int | None,
) -> list[MarketState]:
    if start_timestamp is not None and end_timestamp is not None:
        if start_timestamp > end_timestamp:
            raise InvalidQueryParameter(
                "start_timestamp must be <= end_timestamp"
            )

    filtered = history

    if start_timestamp is not None:
        filtered = [item for item in filtered if item.timestamp >= start_timestamp]

    if end_timestamp is not None:
        filtered = [item for item in filtered if item.timestamp <= end_timestamp]

    return filtered


def apply_limit(history: list[MarketState], limit: int) -> list[MarketState]:
    if limit <= 0:
        raise InvalidQueryParameter("limit must be a positive integer")

    return history[-limit:]


def validate_allowed_value(
    value: str,
    allowed: set[str],
    param_name: str,
) -> None:
    if value not in allowed:
        raise InvalidQueryParameter(
            f"Invalid value for '{param_name}': {value}. "
            f"Allowed values: {sorted(allowed)}"
        )
