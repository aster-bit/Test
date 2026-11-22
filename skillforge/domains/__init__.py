"""Domain plugin registry."""

from __future__ import annotations

from .base import DomainPlugin


def registry() -> dict[str, DomainPlugin]:
    from .invoice.plugin import InvoicePlugin

    plugins: list[DomainPlugin] = [InvoicePlugin()]
    return {p.name: p for p in plugins}


def get_plugin(name: str) -> DomainPlugin:
    return registry()[name]


def match_plugin(request: str) -> tuple[DomainPlugin | None, float]:
    text = request.lower()
    best, best_score = None, 0.0
    for p in registry().values():
        score = sum(1.0 for k in p.keywords if k in text)
        if score > best_score:
            best, best_score = p, score
    return best, best_score
