"""Integration tests for erc8183_voter.daemon (mocked chain + pipeline)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest

from erc8183_voter.config import VoterConfig
from erc8183_voter.daemon import VoterDaemon
from erc8183_voter.voter import VoteDecision


# ---------------------------------------------------------------------------
# Stub for bnbagent.erc8183.types.JobStatus
# ---------------------------------------------------------------------------

class _JobStatus(IntEnum):
    OPEN = 0
    FUNDED = 1
    SUBMITTED = 2
    COMPLETED = 3
    REJECTED = 4
    EXPIRED = 5


# ---------------------------------------------------------------------------
# Mock chain objects (matching actual bnbagent SDK API)
# ---------------------------------------------------------------------------

@dataclass
class _MockJob:
    id: int = 1
    client: str = "0xClient"
    provider: str = "0xProvider"
    evaluator: str = "0xEvaluator"
    description: str = '{"task":"Generate a logo","terms":{"deliverables":"PNG image"}}'
    budget: int = 100
    expired_at: int = 9999999999
    status: _JobStatus = _JobStatus.SUBMITTED
    hook: str = "0x0000000000000000000000000000000000000000"
    deliverable: bytes = b"\xff" * 32


class _MockPolicyContract:
    """Mimics the web3 contract.events interface for Disputed/VoteCast."""
    class _DisputedEvent:
        def get_logs(self, from_block=0, to_block="latest"):
            return []

    class _VoteCastEvent:
        def get_logs(self, from_block=0, to_block="latest"):
            return []

    class events:
        @staticmethod
        def Disputed():
            return _MockPolicyContract._DisputedEvent()

        @staticmethod
        def VoteCast():
            return _MockPolicyContract._VoteCastEvent()


class _MockPolicy:
    def __init__(self):
        self._voted: set[tuple[int, str]] = set()
        self.contract = _MockPolicyContract()

    def is_voter(self, address: str) -> bool:
        return True

    def has_voted(self, job_id: int, address: str) -> bool:
        return (job_id, address) in self._voted

    def get_deliverable_url(self, job_id: int, *, hint_block: int | None = None) -> str:
        return "https://example.com/deliverable.json"

    def reject_votes(self, job_id: int) -> int:
        return 0

    def vote_quorum(self) -> int:
        return 3


class _MockW3Eth:
    block_number: int = 100


class _MockW3:
    eth = _MockW3Eth()


class _MockERC8183Client:
    def __init__(self):
        self.policy = _MockPolicy()
        self.w3 = _MockW3()
        self._vote_reject_calls: list[int] = []

    def get_job(self, job_id: int) -> _MockJob:
        return _MockJob(id=job_id)

    def get_deliverable_url(self, job_id: int, *, hint_block: int | None = None) -> str:
        return "https://example.com/deliverable.json"

    def vote_reject(self, job_id: int) -> dict[str, Any]:
        self._vote_reject_calls.append(job_id)
        return {"transactionHash": f"0xtx_{job_id}", "status": 1, "receipt": {}}

    def settle(self, job_id: int) -> dict[str, Any]:
        return {"transactionHash": "0xsettle", "status": 1, "receipt": {}}


# ---------------------------------------------------------------------------
# Fixture: daemon with all mocks injected
# ---------------------------------------------------------------------------

@pytest.fixture
def config(tmp_path) -> VoterConfig:
    return VoterConfig(
        voter_private_key="0x" + "ab" * 32,
        wallet_password="test",
        network="bsc-testnet",
        confidence_threshold=0.70,
        poll_interval=1,
        por_storage_dir=str(tmp_path / "por"),
    )


@pytest.fixture
def mock_erc8183() -> _MockERC8183Client:
    return _MockERC8183Client()


@pytest.fixture
def daemon(config: VoterConfig, mock_erc8183: _MockERC8183Client) -> VoterDaemon:
    d = VoterDaemon(config)
    # Inject mock chain objects (bypass _init_chain)
    d._wallet = MagicMock()
    d._wallet.address = "0xVoterAddress"
    d._erc8183 = mock_erc8183
    d._policy = mock_erc8183.policy
    d._voter_address = "0xVoterAddress"
    d._last_block = 99
    return d


# ---------------------------------------------------------------------------
# Tests: handle_disputed_job -> vote_reject
# ---------------------------------------------------------------------------

class TestHandleDisputedJobReject:
    @patch("erc8183_voter.daemon.VoterDaemon._handle_disputed_job")
    def test_reject_when_pipeline_says_no(
        self,
        _mock_handle,  # we test the real method below, not patched
        daemon: VoterDaemon,
        mock_erc8183: _MockERC8183Client,
    ):
        # Call the real implementation directly (undo the patch for this test)
        _mock_handle.stop()

        mock_voter = MagicMock()
        mock_voter.evaluate.return_value = VoteDecision(
            outcome="NO",
            confidence=0.90,
            should_reject=True,
            por_root="0x" + "dd" * 32,
            pipeline_ok=True,
            reasoning_summary="Deliverable does not match spec",
        )
        daemon._voter = mock_voter

        # Patch the JobStatus import inside _handle_disputed_job
        with patch("erc8183_voter.daemon.VoterDaemon._handle_disputed_job", wraps=daemon._handle_disputed_job):
            # We need to mock the bnbagent import inside the method
            pass

    def test_reject_calls_vote_reject(
        self,
        daemon: VoterDaemon,
        mock_erc8183: _MockERC8183Client,
    ):
        mock_voter = MagicMock()
        mock_voter.evaluate.return_value = VoteDecision(
            outcome="NO",
            confidence=0.90,
            should_reject=True,
            por_root="0x" + "dd" * 32,
            pipeline_ok=True,
            reasoning_summary="Deliverable does not match spec",
        )
        daemon._voter = mock_voter

        # Patch the JobStatus import
        with patch.dict("sys.modules", {"bnbagent.erc8183.types": MagicMock(JobStatus=_JobStatus)}):
            with patch.dict("sys.modules", {"bnbagent.erc8183.schema": MagicMock(JobDescription=MagicMock(from_str=MagicMock(return_value=None)))}):
                daemon._handle_disputed_job(job_id=42, hint_block=100)

        assert 42 in mock_erc8183._vote_reject_calls
        assert daemon._stats["rejected"] == 1


# ---------------------------------------------------------------------------
# Tests: handle_disputed_job -> abstain
# ---------------------------------------------------------------------------

class TestHandleDisputedJobAbstain:
    def test_abstain_when_pipeline_says_yes(
        self,
        daemon: VoterDaemon,
        mock_erc8183: _MockERC8183Client,
    ):
        mock_voter = MagicMock()
        mock_voter.evaluate.return_value = VoteDecision(
            outcome="YES",
            confidence=0.95,
            should_reject=False,
            pipeline_ok=True,
            reasoning_summary="Deliverable looks good",
        )
        daemon._voter = mock_voter

        with patch.dict("sys.modules", {"bnbagent.erc8183.types": MagicMock(JobStatus=_JobStatus)}):
            with patch.dict("sys.modules", {"bnbagent.erc8183.schema": MagicMock(JobDescription=MagicMock(from_str=MagicMock(return_value=None)))}):
                daemon._handle_disputed_job(job_id=43, hint_block=100)

        assert 43 not in mock_erc8183._vote_reject_calls
        assert daemon._stats["abstained"] == 1

    def test_abstain_when_pipeline_errors(
        self,
        daemon: VoterDaemon,
        mock_erc8183: _MockERC8183Client,
    ):
        mock_voter = MagicMock()
        mock_voter.evaluate.return_value = VoteDecision(
            outcome=None,
            confidence=0.0,
            should_reject=False,
            pipeline_ok=False,
            errors=["LLM timeout"],
            reasoning_summary="Pipeline failed",
        )
        daemon._voter = mock_voter

        with patch.dict("sys.modules", {"bnbagent.erc8183.types": MagicMock(JobStatus=_JobStatus)}):
            with patch.dict("sys.modules", {"bnbagent.erc8183.schema": MagicMock(JobDescription=MagicMock(from_str=MagicMock(return_value=None)))}):
                daemon._handle_disputed_job(job_id=44, hint_block=100)

        assert 44 not in mock_erc8183._vote_reject_calls
        assert daemon._stats["abstained"] == 1


# ---------------------------------------------------------------------------
# Tests: skip already-voted jobs
# ---------------------------------------------------------------------------

class TestSkipAlreadyVoted:
    def test_skip_when_already_voted(
        self,
        daemon: VoterDaemon,
        mock_erc8183: _MockERC8183Client,
    ):
        # Mark as already voted
        mock_erc8183.policy._voted.add((45, "0xVoterAddress"))

        mock_voter = MagicMock()
        daemon._voter = mock_voter

        daemon._handle_disputed_job(job_id=45, hint_block=100)

        # Voter should never be called
        mock_voter.evaluate.assert_not_called()
        assert 45 not in mock_erc8183._vote_reject_calls


# ---------------------------------------------------------------------------
# Tests: skip non-SUBMITTED jobs
# ---------------------------------------------------------------------------

class TestSkipNonSubmitted:
    def test_skip_when_job_not_submitted(
        self,
        daemon: VoterDaemon,
        mock_erc8183: _MockERC8183Client,
    ):
        # Return a job with COMPLETED status
        mock_erc8183.get_job = lambda jid: _MockJob(status=_JobStatus.COMPLETED)

        mock_voter = MagicMock()
        daemon._voter = mock_voter

        with patch.dict("sys.modules", {"bnbagent.erc8183.types": MagicMock(JobStatus=_JobStatus)}):
            daemon._handle_disputed_job(job_id=46, hint_block=100)

        mock_voter.evaluate.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: vote_reject tx failure is handled gracefully
# ---------------------------------------------------------------------------

class TestVoteRejectTxFailure:
    def test_tx_failure_logged_as_error(
        self,
        daemon: VoterDaemon,
        mock_erc8183: _MockERC8183Client,
    ):
        mock_voter = MagicMock()
        mock_voter.evaluate.return_value = VoteDecision(
            outcome="NO",
            confidence=0.90,
            should_reject=True,
            pipeline_ok=True,
        )
        daemon._voter = mock_voter

        # Make vote_reject raise
        mock_erc8183.vote_reject = MagicMock(side_effect=RuntimeError("tx reverted"))

        with patch.dict("sys.modules", {"bnbagent.erc8183.types": MagicMock(JobStatus=_JobStatus)}):
            with patch.dict("sys.modules", {"bnbagent.erc8183.schema": MagicMock(JobDescription=MagicMock(from_str=MagicMock(return_value=None)))}):
                daemon._handle_disputed_job(job_id=47, hint_block=100)

        assert daemon._stats["errors"] == 1
        # Should still record artifacts (rejected count stays 0 due to tx fail)
        assert daemon._stats["rejected"] == 0


# ---------------------------------------------------------------------------
# Tests: preflight
# ---------------------------------------------------------------------------

class TestPreflight:
    def test_preflight_passes_for_whitelisted_voter(self, daemon: VoterDaemon):
        daemon._preflight()  # should not raise

    def test_preflight_fails_for_non_voter(self, daemon: VoterDaemon):
        daemon._policy.is_voter = lambda addr: False
        with pytest.raises(RuntimeError, match="not a whitelisted voter"):
            daemon._preflight()


# ---------------------------------------------------------------------------
# Tests: event querying
# ---------------------------------------------------------------------------

class TestEventQuerying:
    def test_get_disputed_events_returns_empty_on_no_events(self, daemon: VoterDaemon):
        events = daemon._get_disputed_events(100, 200)
        assert events == []

    def test_get_vote_cast_events_returns_empty_on_no_events(self, daemon: VoterDaemon):
        events = daemon._get_vote_cast_events(100, 200)
        assert events == []
