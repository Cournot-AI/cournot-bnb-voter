"""
VoterDaemon: long-running event loop that listens for ERC-8183 Disputed
events on-chain, evaluates deliverables via the Cournot pipeline, and casts
``vote_reject()`` when the deliverable fails verification.

Combines patterns from:
- ``examples/voter/watch.py``  (event polling, Disputed/VoteCast handling)
- ``acp_evaluator/daemon.py``  (daemon lifecycle, signal handling, heartbeat)
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from erc8183_voter.adapter import (
    build_verification_query,
    fetch_manifest,
    manifest_hash,
)
from erc8183_voter.config import VoterConfig
from erc8183_voter.storage import PoRStorage
from erc8183_voter.voter import CournotVoter

logger = logging.getLogger(__name__)


class VoterDaemon:
    """
    Autonomous voter daemon for ERC-8183 dispute settlement.

    Lifecycle:
        1. Load config, create wallet + chain client, verify voter whitelist.
        2. Poll for ``Disputed`` events every ``poll_interval`` seconds.
        3. For each disputed job, run the Cournot pipeline and optionally
           cast ``vote_reject()``.
        4. Optionally auto-settle when quorum is reached.
        5. Clean shutdown on SIGTERM / SIGINT.
    """

    def __init__(self, config: VoterConfig) -> None:
        self._config = config
        self._running = False

        # Lazy-initialised
        self._wallet: Any = None
        self._erc8183: Any = None
        self._policy: Any = None
        self._voter: Optional[CournotVoter] = None
        self._storage: Optional[PoRStorage] = None
        self._voter_address: str = ""

        # State
        self._last_block: int = 0
        self._seen_disputed: set[int] = set()

        # Stats
        self._stats = {
            "started_at": "",
            "evaluated": 0,
            "rejected": 0,
            "abstained": 0,
            "errors": 0,
        }

    # -- initialisation ------------------------------------------------------

    def _init_chain(self) -> None:
        """Create wallet provider and ERC-8183 client from config."""
        from bnbagent.erc8183 import ERC8183Client
        from bnbagent.wallets import EVMWalletProvider

        self._wallet = EVMWalletProvider(
            password=self._config.wallet_password,
            private_key=self._config.voter_private_key,
            persist=False,  # in-memory only, don't write keystore to disk
        )
        self._voter_address = self._wallet.address

        erc8183_kwargs: dict[str, Any] = {
            "wallet_provider": self._wallet,
            "network": self._config.network,
        }
        self._erc8183 = ERC8183Client(**erc8183_kwargs)
        self._policy = self._erc8183.policy
        logger.info("Chain client initialised on %s", self._config.network)

    def _preflight(self) -> None:
        """Verify that the voter address is whitelisted."""
        if not self._policy.is_voter(self._voter_address):
            raise RuntimeError(
                f"Address {self._voter_address} is not a whitelisted voter "
                "on the current ERC-8183 policy contract."
            )
        logger.info("Pre-flight OK: %s is a whitelisted voter", self._voter_address)

    # -- event querying (raw web3 log reads) ---------------------------------

    def _get_disputed_events(self, from_block: int, to_block: int) -> list[dict]:
        """Query ``Disputed(jobId, client)`` events from the policy contract."""
        try:
            logs = self._policy.contract.events.Disputed().get_logs(
                from_block=from_block,
                to_block=to_block,
            )
            return [
                {
                    "jobId": log["args"]["jobId"],
                    "client": log["args"]["client"],
                    "blockNumber": log["blockNumber"],
                }
                for log in logs
            ]
        except Exception as exc:
            logger.warning("Disputed event query failed: %s", exc)
            return []

    def _get_vote_cast_events(self, from_block: int, to_block: int) -> list[dict]:
        """Query ``VoteCast(jobId, voter, rejectVotes)`` events."""
        try:
            logs = self._policy.contract.events.VoteCast().get_logs(
                from_block=from_block,
                to_block=to_block,
            )
            return [
                {
                    "jobId": log["args"]["jobId"],
                    "voter": log["args"]["voter"],
                    "rejectVotes": log["args"]["rejectVotes"],
                    "blockNumber": log["blockNumber"],
                }
                for log in logs
            ]
        except Exception as exc:
            logger.debug("VoteCast event query failed: %s", exc)
            return []

    # -- event handlers ------------------------------------------------------

    def _handle_disputed_job(self, job_id: int, hint_block: int) -> None:
        """Evaluate a single disputed job and decide whether to reject."""
        log_prefix = f"[job {job_id}]"

        # Guard: already voted?
        try:
            if self._policy.has_voted(job_id, self._voter_address):
                logger.debug("%s already voted, skipping", log_prefix)
                return
        except Exception as exc:
            logger.warning("%s has_voted check failed: %s", log_prefix, exc)

        # Guard: job still in SUBMITTED status?
        try:
            job = self._erc8183.get_job(job_id)
        except Exception as exc:
            logger.error("%s get_job failed: %s", log_prefix, exc)
            self._stats["errors"] += 1
            return

        # Job.status is a JobStatus IntEnum; SUBMITTED = 2
        from bnbagent.erc8183.types import JobStatus
        if job.status != JobStatus.SUBMITTED:
            logger.debug("%s status is %s, skipping", log_prefix, job.status.name)
            return

        # Parse job description
        job_desc = None
        try:
            from bnbagent.erc8183.schema import JobDescription
            job_desc = JobDescription.from_str(job.description)
        except Exception:
            pass  # fall back to plain-text in adapter

        # Fetch deliverable manifest
        deliverable_url: Optional[str] = None
        try:
            deliverable_url = self._erc8183.get_deliverable_url(
                job_id, hint_block=hint_block,
            )
        except Exception as exc:
            logger.warning("%s get_deliverable_url failed: %s", log_prefix, exc)

        manifest_obj = None
        hash_ok = False
        if deliverable_url:
            manifest_obj = fetch_manifest(deliverable_url, self._config.storage_gateway_url)
            if manifest_obj is not None:
                # job.deliverable is bytes (32-byte hash).  manifest_hash()
                # returns a 0x-prefixed hex string, so compare as hex.
                on_chain_hex = "0x" + job.deliverable.hex() if job.deliverable else ""
                hash_ok = manifest_hash(manifest_obj) == on_chain_hex

        # Build query
        query = build_verification_query(job, job_desc, manifest_obj, hash_ok)

        # Run Cournot pipeline
        if self._voter is None:
            self._voter = CournotVoter(self._config)

        decision = self._voter.evaluate(query)
        self._stats["evaluated"] += 1

        # Act on decision
        action: str
        tx_hash: Optional[str] = None

        if decision.should_reject:
            action = "vote_reject"
            try:
                result = self._erc8183.vote_reject(job_id)
                tx_hash = result.get("transactionHash")
                logger.info(
                    "%s REJECTED (confidence=%.2f, tx=%s)",
                    log_prefix, decision.confidence, tx_hash,
                )
                self._stats["rejected"] += 1
            except Exception as exc:
                action = "vote_reject_failed"
                logger.error("%s vote_reject tx failed: %s", log_prefix, exc)
                self._stats["errors"] += 1
        else:
            action = "abstain"
            reason = decision.outcome or "pipeline_error"
            logger.info(
                "%s ABSTAIN (outcome=%s, confidence=%.2f, pipeline_ok=%s)",
                log_prefix, reason, decision.confidence, decision.pipeline_ok,
            )
            self._stats["abstained"] += 1

        # Persist artifacts
        if self._storage is None:
            self._storage = PoRStorage(self._config.por_storage_dir)

        self._storage.save(
            job_id,
            action=action,
            outcome=decision.outcome,
            confidence=decision.confidence,
            query=query,
            por_bundle=decision.por_bundle,
            tx_hash=tx_hash,
            errors=decision.errors,
        )

    # -- main loop -----------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Core event loop: poll for Disputed events."""
        logger.info("Poll loop started (interval=%ds)", self._config.poll_interval)

        while self._running:
            try:
                head = self._erc8183.w3.eth.block_number
                if head <= self._last_block:
                    await asyncio.sleep(self._config.poll_interval)
                    continue

                from_block = self._last_block + 1

                # Query Disputed events in the new block range
                disputed_events = self._get_disputed_events(from_block, head)

                for event in disputed_events:
                    job_id = int(event["jobId"])
                    if job_id in self._seen_disputed:
                        continue
                    self._seen_disputed.add(job_id)

                    hint_block = event.get("blockNumber", head)
                    self._handle_disputed_job(job_id, hint_block)

                # Check for auto-settle
                if self._config.auto_settle:
                    self._try_auto_settle(disputed_events)

                # Query VoteCast events for logging
                vote_events = self._get_vote_cast_events(from_block, head)
                for ve in vote_events:
                    logger.debug(
                        "VoteCast: job=%s voter=%s votes=%s",
                        ve["jobId"], ve["voter"], ve["rejectVotes"],
                    )

                self._last_block = head

            except Exception as exc:
                logger.error("Poll loop error: %s", exc)
                self._stats["errors"] += 1

            await asyncio.sleep(self._config.poll_interval)

    def _try_auto_settle(self, disputed_events: list[dict]) -> None:
        """If quorum is met for any disputed job, call settle()."""
        for event in disputed_events:
            job_id = int(event["jobId"])
            try:
                reject_count = self._policy.reject_votes(job_id)
                quorum = self._policy.vote_quorum()
                if reject_count >= quorum:
                    logger.info("Quorum met for job %s, calling settle()", job_id)
                    self._erc8183.settle(job_id)
            except Exception as exc:
                logger.warning("Auto-settle check/call failed for job %s: %s", job_id, exc)

    async def _heartbeat(self) -> None:
        """Log stats every 5 minutes."""
        while self._running:
            await asyncio.sleep(300)
            logger.info(
                "Heartbeat | evaluated=%d rejected=%d abstained=%d errors=%d | seen=%d jobs",
                self._stats["evaluated"],
                self._stats["rejected"],
                self._stats["abstained"],
                self._stats["errors"],
                len(self._seen_disputed),
            )

    # -- lifecycle -----------------------------------------------------------

    async def run(self) -> None:
        """Start the daemon (blocking coroutine)."""
        self._stats["started_at"] = datetime.now(timezone.utc).isoformat()
        self._running = True

        # Initialise chain objects
        self._init_chain()
        self._preflight()

        # Record start block
        self._last_block = self._erc8183.w3.eth.block_number
        logger.info("Start block: %d", self._last_block)

        # Wire up signal handlers
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._shutdown)

        logger.info("Daemon ready. Listening for Disputed events...")

        await asyncio.gather(
            self._poll_loop(),
            self._heartbeat(),
        )

    def _shutdown(self) -> None:
        logger.info("Shutdown signal received")
        self._running = False
        logger.info("Final stats: %s", json.dumps(self._stats))
