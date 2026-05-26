"""Unit tests for erc8183_voter.voter (vote decision logic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import patch, MagicMock

import pytest

from erc8183_voter.config import VoterConfig
from erc8183_voter.voter import CournotVoter, VoteDecision


# ---------------------------------------------------------------------------
# Lightweight RunResult stubs (avoid importing the real orchestrator)
# ---------------------------------------------------------------------------

@dataclass
class _Verdict:
    outcome: str = "INVALID"
    confidence: float = 0.30
    market_id: str = "mk_test"


@dataclass
class _PoRBundle:
    por_root: str = "0x" + "bb" * 32


@dataclass
class _RunResult:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    verdict: Optional[_Verdict] = field(default_factory=_Verdict)
    por_bundle: Optional[_PoRBundle] = field(default_factory=_PoRBundle)
    audit_trace: str = "Reasoning trace text"

    @property
    def outcome(self) -> Optional[str]:
        return self.verdict.outcome if self.verdict else None


class _FakePipeline:
    """Pipeline that returns a pre-configured RunResult."""
    def __init__(self, result: _RunResult | None = None):
        self._result = result or _RunResult()

    def run(self, query: str) -> _RunResult:
        return self._result


class _BrokenPipeline:
    """Pipeline that always raises."""
    def run(self, query: str):
        raise RuntimeError("LLM timeout")


# ---------------------------------------------------------------------------
# Fixture: voter with injected pipeline + direct eval mock
# ---------------------------------------------------------------------------

def _make_voter(
    direct_result: dict | None = None,
    pipeline: object | None = None,
    threshold: float = 0.70,
) -> CournotVoter:
    cfg = VoterConfig(
        voter_private_key="0x" + "ab" * 32,
        confidence_threshold=threshold,
    )
    voter = CournotVoter(cfg)
    voter._pipeline = pipeline or _FakePipeline()

    # Patch _direct_evaluate to return our mock result
    if direct_result is not None:
        voter._direct_evaluate = lambda query: direct_result
    else:
        voter._direct_evaluate = lambda query: {"outcome": "INVALID", "confidence": 0.0, "justification": "mock"}

    return voter


# ---------------------------------------------------------------------------
# Tests: NO + high confidence -> reject
# ---------------------------------------------------------------------------

class TestRejectDecision:
    def test_no_high_confidence_rejects(self):
        voter = _make_voter(
            direct_result={"outcome": "NO", "confidence": 0.90, "justification": "Off-topic"},
            threshold=0.70,
        )
        decision = voter.evaluate("test query")
        assert decision.should_reject is True
        assert decision.outcome == "NO"
        assert decision.confidence == 0.90

    def test_no_at_threshold_rejects(self):
        voter = _make_voter(
            direct_result={"outcome": "NO", "confidence": 0.70, "justification": "Bad"},
            threshold=0.70,
        )
        decision = voter.evaluate("test query")
        assert decision.should_reject is True


# ---------------------------------------------------------------------------
# Tests: NO + low confidence -> abstain
# ---------------------------------------------------------------------------

class TestAbstainLowConfidence:
    def test_no_below_threshold_abstains(self):
        voter = _make_voter(
            direct_result={"outcome": "NO", "confidence": 0.50, "justification": "Unsure"},
            threshold=0.70,
        )
        decision = voter.evaluate("test query")
        assert decision.should_reject is False
        assert decision.outcome == "NO"


# ---------------------------------------------------------------------------
# Tests: YES -> abstain
# ---------------------------------------------------------------------------

class TestAbstainYes:
    def test_yes_always_abstains(self):
        voter = _make_voter(
            direct_result={"outcome": "YES", "confidence": 0.95, "justification": "Good"},
        )
        decision = voter.evaluate("test query")
        assert decision.should_reject is False
        assert decision.outcome == "YES"


# ---------------------------------------------------------------------------
# Tests: INVALID -> abstain
# ---------------------------------------------------------------------------

class TestAbstainInvalid:
    def test_invalid_always_abstains(self):
        voter = _make_voter(
            direct_result={"outcome": "INVALID", "confidence": 0.60, "justification": "Ambiguous"},
        )
        decision = voter.evaluate("test query")
        assert decision.should_reject is False
        assert decision.outcome == "INVALID"


# ---------------------------------------------------------------------------
# Tests: direct eval failure + pipeline failure -> abstain (fail-safe)
# ---------------------------------------------------------------------------

class TestFailSafe:
    def test_both_fail_abstains(self):
        cfg = VoterConfig(voter_private_key="0x" + "ab" * 32, confidence_threshold=0.70)
        voter = CournotVoter(cfg)
        voter._pipeline = _BrokenPipeline()
        # Make direct eval also fail
        voter._direct_evaluate = MagicMock(side_effect=RuntimeError("LLM down"))

        decision = voter.evaluate("test query")
        assert decision.should_reject is False
        assert decision.pipeline_ok is False
        assert len(decision.errors) > 0

    def test_direct_eval_fails_pipeline_ok_still_abstains(self):
        """If direct eval fails, we abstain even if pipeline returned NO."""
        cfg = VoterConfig(voter_private_key="0x" + "ab" * 32, confidence_threshold=0.70)
        voter = CournotVoter(cfg)
        voter._pipeline = _FakePipeline(_RunResult(
            verdict=_Verdict(outcome="NO", confidence=0.99),
        ))
        voter._direct_evaluate = MagicMock(side_effect=RuntimeError("LLM down"))

        decision = voter.evaluate("test query")
        assert decision.should_reject is False  # fail-safe


# ---------------------------------------------------------------------------
# Tests: por_root extraction
# ---------------------------------------------------------------------------

class TestPoRRoot:
    def test_por_root_extracted_from_pipeline(self):
        expected_root = "0x" + "cc" * 32
        voter = _make_voter(
            direct_result={"outcome": "YES", "confidence": 0.80, "justification": "Good"},
            pipeline=_FakePipeline(_RunResult(
                por_bundle=_PoRBundle(por_root=expected_root),
            )),
        )
        decision = voter.evaluate("test query")
        assert decision.por_root == expected_root

    def test_no_por_bundle_gives_none(self):
        voter = _make_voter(
            direct_result={"outcome": "YES", "confidence": 0.80, "justification": "Good"},
            pipeline=_FakePipeline(_RunResult(por_bundle=None)),
        )
        decision = voter.evaluate("test query")
        assert decision.por_root is None

    def test_pipeline_fails_but_direct_eval_works(self):
        """Decision still made even if pipeline fails (no PoR though)."""
        voter = _make_voter(
            direct_result={"outcome": "NO", "confidence": 0.95, "justification": "Bad"},
            pipeline=_BrokenPipeline(),
            threshold=0.70,
        )
        decision = voter.evaluate("test query")
        assert decision.should_reject is True
        assert decision.por_root is None
        assert len(decision.errors) > 0
