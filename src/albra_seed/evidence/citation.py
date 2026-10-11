"""Citation gate — requires citations for factual claims.

Interface for agents that must refuse, mark-unverified, or request evidence
before emitting a factual claim. The gate never fabricates evidence.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from albra_seed.core.types import new_id, utc_now


class CitationDecision(BaseModel):
    decision_id: str = Field(default_factory=lambda: new_id("citdec"))
    action: str  # cite | refuse | unverified | request_evidence
    claim: str
    evidence_id: str | None = None
    reason: str | None = None
    query: str | None = None
    timestamp: Any = Field(default_factory=utc_now)


class CitationGate:
    """Gate for requiring citations on factual claims.

    Methods:
        require_evidence(claim)       -> mark awaiting evidence
        cite(evidence_id)             -> cite a specific evidence id
        refuse(reason)                -> refuse to claim (e.g. ambiguous)
        mark_unverified(reason)       -> emit claim without evidence
        request_evidence(query)       -> ask the system/user for evidence
    """

    def __init__(self) -> None:
        self._history: list[CitationDecision] = []

    @property
    def history(self) -> list[CitationDecision]:
        return list(self._history)

    def require_evidence(self, claim: str) -> CitationDecision:
        d = CitationDecision(
            action="request_evidence",
            claim=claim,
            reason="evidence required before factual claim",
            timestamp=utc_now(),
        )
        self._history.append(d)
        return d

    def cite(self, claim: str, evidence_id: str) -> CitationDecision:
        if not evidence_id:
            raise ValueError("cite requires a non-empty evidence_id")
        d = CitationDecision(
            action="cite",
            claim=claim,
            evidence_id=evidence_id,
            reason=None,
            timestamp=utc_now(),
        )
        self._history.append(d)
        return d

    def refuse(self, claim: str, reason: str) -> CitationDecision:
        d = CitationDecision(
            action="refuse",
            claim=claim,
            reason=reason or "refused without reason",
            timestamp=utc_now(),
        )
        self._history.append(d)
        return d

    def mark_unverified(self, claim: str, reason: str | None = None) -> CitationDecision:
        d = CitationDecision(
            action="unverified",
            claim=claim,
            reason=reason or "no supporting evidence available",
            timestamp=utc_now(),
        )
        self._history.append(d)
        return d

    def request_evidence(self, query: str) -> CitationDecision:
        d = CitationDecision(
            action="request_evidence",
            claim=query,
            query=query,
            reason="evidence requested by citation gate",
            timestamp=utc_now(),
        )
        self._history.append(d)
        return d

    def has_citation_for(self, claim: str) -> bool:
        return any(
            d.action == "cite" and d.claim == claim and d.evidence_id
            for d in self._history
        )

    def refused(self, claim: str) -> bool:
        return any(d.action == "refuse" and d.claim == claim for d in self._history)

    def unverified(self, claim: str) -> bool:
        return any(d.action == "unverified" and d.claim == claim for d in self._history)

    def last(self) -> CitationDecision | None:
        return self._history[-1] if self._history else None

    def reset(self) -> None:
        self._history.clear()
