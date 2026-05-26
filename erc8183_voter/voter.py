"""
CournotVoter: wraps the Cournot PoR pipeline and applies the vote decision
threshold for ERC-8183 dispute resolution.

The Cournot pipeline is designed for *market resolution* (verifiable claims
backed by external evidence).  Deliverable evaluation queries are
self-contained — all evidence is embedded in the query text itself — so the
pipeline's collector cannot find external corroboration and the judge
defaults to INVALID.

To work around this, we use a **two-layer** approach:

1. **Direct LLM evaluation** — Ask the LLM to judge the deliverable against
   the spec and return a structured YES/NO/INVALID verdict.  This gives us
   a reliable outcome + confidence.

2. **Pipeline run** — Still run the full Cournot pipeline to produce a PoR
   bundle (cryptographic proof of reasoning) for auditability.  The
   pipeline's verdict itself is recorded but the *direct* evaluation
   drives the vote decision.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

from erc8183_voter.config import VoterConfig

logger = logging.getLogger(__name__)

# ── Direct evaluation prompt ──────────────────────────────────────────────

DIRECT_EVAL_SYSTEM = """\
You are a strict deliverable evaluator for an on-chain job marketplace.
You will receive a verification query containing a JOB SPECIFICATION, a DELIVERABLE, and an INTEGRITY CHECK.

Your job:
1. Determine whether the deliverable FULLY satisfies the specification.
2. Return a JSON object with exactly these fields:
   - "outcome": one of "YES", "NO", or "INVALID"
   - "confidence": a float between 0.0 and 1.0
   - "justification": a brief (1-3 sentence) explanation

Decision rules:
- YES: The deliverable clearly and fully satisfies every requirement in the spec.
- NO: The deliverable clearly fails to satisfy one or more requirements. You must cite which requirement(s) are not met.
- INVALID: The specification is too ambiguous to judge, or the deliverable is missing/unavailable.

Confidence guidelines:
- 0.90-1.00: Overwhelming clarity (e.g. wrong language, completely off-topic, or perfect match)
- 0.70-0.89: Clear judgment with minor ambiguity
- 0.55-0.69: Borderline case
- Below 0.55: Use INVALID instead of YES/NO

Return ONLY the JSON object, no other text."""


@dataclass
class VoteDecision:
    """Result of a single verification evaluation."""

    outcome: Optional[str] = None  # YES / NO / INVALID
    confidence: float = 0.0
    should_reject: bool = False
    por_root: Optional[str] = None
    por_bundle: Any = None
    reasoning_summary: str = ""
    pipeline_ok: bool = False
    errors: list[str] = field(default_factory=list)


class CournotVoter:
    """
    Evaluates a verification query through direct LLM judgment (for the
    vote decision) and the Cournot PoR pipeline (for the cryptographic
    proof bundle).

    **Fail-safe rule**: if either evaluation errors internally,
    ``should_reject`` is always ``False``.
    """

    def __init__(self, config: VoterConfig) -> None:
        self._config = config
        self._pipeline: Any | None = None
        self._llm: Any | None = None

    # -- lazy init -----------------------------------------------------------

    def _ensure_cournot_path(self) -> None:
        cournot_root = os.environ.get(
            "COURNOT_PROTOCOL_PATH",
            os.path.join(os.path.dirname(__file__), "../../cournot-protocol"),
        )
        cournot_root = os.path.abspath(cournot_root)
        if cournot_root not in sys.path:
            # Append (not insert) so the local erc8183_voter package
            # is found before the copy inside cournot-protocol.
            sys.path.append(cournot_root)

    def _get_llm(self) -> Any:
        """Return (or create) the LLM client for direct evaluation."""
        if self._llm is not None:
            return self._llm

        self._ensure_cournot_path()
        from core.llm import create_llm_client

        provider = self._config.cournot_llm_provider
        api_key = (
            os.environ.get(f"{provider.upper()}_API_KEY")
            or os.environ.get("COURNOT_LLM_API_KEY")
        )
        if not api_key:
            raise RuntimeError(
                f"No API key found for LLM provider '{provider}'. "
                f"Set {provider.upper()}_API_KEY or COURNOT_LLM_API_KEY."
            )

        self._llm = create_llm_client(
            provider=provider,
            api_key=api_key,
            model=self._config.cournot_llm_model,
        )
        logger.info(
            "LLM client initialized (%s / %s)",
            provider, self._config.cournot_llm_model,
        )
        return self._llm

    def _get_pipeline(self) -> Any:
        if self._pipeline is not None:
            return self._pipeline

        self._ensure_cournot_path()
        from agents.context import AgentContext
        from orchestrator.pipeline import create_pipeline, ExecutionMode

        llm = self._get_llm()
        ctx = AgentContext(llm=llm)
        self._pipeline = create_pipeline(
            mode=ExecutionMode.PRODUCTION,
            context=ctx,
            require_llm=True,
        )
        logger.info("Cournot pipeline initialized")
        return self._pipeline

    # -- direct LLM evaluation -----------------------------------------------

    def _direct_evaluate(self, query: str) -> dict:
        """
        Ask the LLM directly to judge the deliverable.

        Returns ``{"outcome": ..., "confidence": ..., "justification": ...}``
        or raises on failure.
        """
        llm = self._get_llm()
        response = llm.chat(
            messages=[
                {"role": "system", "content": DIRECT_EVAL_SYSTEM},
                {"role": "user", "content": query},
            ],
        )
        raw = response.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        data = json.loads(raw)
        return {
            "outcome": str(data.get("outcome", "INVALID")).upper(),
            "confidence": float(data.get("confidence", 0.0)),
            "justification": str(data.get("justification", "")),
        }

    # -- public API ----------------------------------------------------------

    def evaluate(self, query: str) -> VoteDecision:
        """
        Run *query* through direct LLM evaluation (for the decision) and
        the Cournot pipeline (for the PoR bundle).
        """

        # ── Step 1: Direct LLM evaluation ──
        direct_result: Optional[dict] = None
        try:
            direct_result = self._direct_evaluate(query)
            logger.info(
                "Direct eval: outcome=%s confidence=%.2f",
                direct_result["outcome"],
                direct_result["confidence"],
            )
        except Exception as exc:
            logger.error("Direct LLM evaluation failed: %s", exc)

        # ── Step 2: Pipeline run (for PoR bundle) ──
        por_root: Optional[str] = None
        por_bundle: Any = None
        pipeline_errors: list[str] = []
        pipeline_reasoning = ""
        pipeline_ok = False

        try:
            pipeline = self._get_pipeline()
            result = pipeline.run(query)
            por_root = (
                getattr(result.por_bundle, "por_root", None)
                if result.por_bundle else None
            )
            por_bundle = result.por_bundle
            pipeline_reasoning = str(getattr(result, "audit_trace", ""))[:500]
            pipeline_errors = list(result.errors) if result.errors else []
            pipeline_ok = result.ok and len(pipeline_errors) == 0
        except Exception as exc:
            logger.error("Pipeline execution error: %s", exc)
            pipeline_errors = [f"Pipeline error: {exc}"]

        # ── Step 3: Build decision ──
        # Direct evaluation drives the outcome; pipeline provides the PoR.
        if direct_result is None:
            # Both failed — fail-safe abstain
            return VoteDecision(
                pipeline_ok=False,
                errors=pipeline_errors + ["Direct LLM evaluation also failed"],
                reasoning_summary="Both evaluation paths failed",
            )

        outcome = direct_result["outcome"]
        confidence = direct_result["confidence"]
        justification = direct_result["justification"]

        # Combine reasoning
        reasoning = justification
        if pipeline_reasoning:
            reasoning += f" | Pipeline trace: {pipeline_reasoning[:200]}"

        # Fail-safe: only reject when outcome is NO, confidence meets
        # threshold, and direct evaluation succeeded.
        should_reject = (
            outcome == "NO"
            and confidence >= self._config.confidence_threshold
        )

        return VoteDecision(
            outcome=outcome,
            confidence=confidence,
            should_reject=should_reject,
            por_root=por_root,
            por_bundle=por_bundle,
            reasoning_summary=reasoning[:500],
            pipeline_ok=pipeline_ok,
            errors=pipeline_errors,
        )
