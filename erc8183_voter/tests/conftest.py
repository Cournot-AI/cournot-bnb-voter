"""Shared fixtures for erc8183_voter tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

from erc8183_voter.config import VoterConfig


# ---------------------------------------------------------------------------
# VoterConfig fixture (no real key needed for unit tests)
# ---------------------------------------------------------------------------

@pytest.fixture
def voter_config() -> VoterConfig:
    return VoterConfig(
        voter_private_key="0x" + "ab" * 32,
        wallet_password="test",
        network="bsc-testnet",
        confidence_threshold=0.70,
        poll_interval=1,
        por_storage_dir="/tmp/por_test_artifacts",
    )


# ---------------------------------------------------------------------------
# Mock on-chain job
# ---------------------------------------------------------------------------

@dataclass
class MockJob:
    description: str = "Generate a logo for Acme Corp."
    status: str = "SUBMITTED"
    deliverable: str = "0x" + "ff" * 32


@pytest.fixture
def mock_job() -> MockJob:
    return MockJob()


# ---------------------------------------------------------------------------
# Mock JobDescription (mimics bnbagent.erc8183.schema.JobDescription)
# ---------------------------------------------------------------------------

@dataclass
class MockJobDescription:
    task: str = "Generate a logo"
    terms: dict = field(default_factory=lambda: {
        "deliverables": "A PNG logo image",
        "quality_standards": "High resolution, on-brand",
        "success_criteria": "Logo matches brief",
    })

    @classmethod
    def from_str(cls, raw: str) -> Optional["MockJobDescription"]:
        if raw.startswith("{"):
            return cls()
        return None


@pytest.fixture
def mock_job_desc() -> MockJobDescription:
    return MockJobDescription()


# ---------------------------------------------------------------------------
# Mock RunResult (mimics orchestrator.pipeline.RunResult)
# ---------------------------------------------------------------------------

@dataclass
class MockVerdict:
    outcome: str = "NO"
    confidence: float = 0.85
    market_id: str = "mk_test"


@dataclass
class MockPoRBundle:
    por_root: str = "0x" + "aa" * 32

    def model_dump(self, **kwargs) -> dict:
        return {"por_root": self.por_root}


@dataclass
class MockRunResult:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    verdict: Optional[MockVerdict] = field(default_factory=MockVerdict)
    por_bundle: Optional[MockPoRBundle] = field(default_factory=MockPoRBundle)
    audit_trace: str = "The deliverable does not match the spec."

    @property
    def outcome(self) -> Optional[str]:
        return self.verdict.outcome if self.verdict else None


@pytest.fixture
def mock_run_result() -> MockRunResult:
    return MockRunResult()


# ---------------------------------------------------------------------------
# Mock Cournot Pipeline
# ---------------------------------------------------------------------------

class MockPipeline:
    """Pipeline stub that returns a pre-set RunResult."""

    def __init__(self, result: MockRunResult | None = None) -> None:
        self._result = result or MockRunResult()

    def run(self, query: str) -> MockRunResult:
        return self._result


@pytest.fixture
def mock_pipeline(mock_run_result: MockRunResult) -> MockPipeline:
    return MockPipeline(mock_run_result)
