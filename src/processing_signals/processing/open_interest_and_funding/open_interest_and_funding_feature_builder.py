"""Structural assembly for Open Interest and Funding Processing v0.1."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

ROOT_SECTIONS = ("mode", "context", "series", "indicators", "events", "snapshots", "confirmations", "availability", "quality", "native_analysis")


def build_open_interest_and_funding_features(processed_sections: Mapping[str, Any]) -> dict[str, Any]:
    """Assemble already-calculated sections without performing mathematics."""
    if not isinstance(processed_sections, Mapping) or any(section not in processed_sections for section in ROOT_SECTIONS):
        raise ValueError("processed_sections is structurally incomplete")
    for section in ROOT_SECTIONS[1:]:
        if not isinstance(processed_sections[section], Mapping):
            raise ValueError(f"processed_sections.{section} must be a mapping")
    return {"family": "open_interest_and_funding", "stage": "processing", "version": "0.1",
        **{section: deepcopy(processed_sections[section]) for section in ROOT_SECTIONS}}


class OpenInterestAndFundingFeatureBuilder:
    """OO facade restricted to deterministic structural assembly."""

    def build(self, processed_sections: Mapping[str, Any]) -> dict[str, Any]:
        return build_open_interest_and_funding_features(processed_sections)
