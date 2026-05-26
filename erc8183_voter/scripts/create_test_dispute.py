#!/usr/bin/env python3
"""
End-to-end test helper: create a job, submit a bad deliverable, and dispute it.

This script acts as a **job client** so the voter daemon has a disputed job
to pick up and evaluate.

Usage:
  # Use the SAME private key that is the policy admin (or a funded wallet)
  CLIENT_PRIVATE_KEY=0x... python -m erc8183_voter.scripts.create_test_dispute

Environment variables:
  CLIENT_PRIVATE_KEY  — private key of the job creator (required)
  WALLET_PASSWORD     — keystore password (default: "cournot-voter")
  NETWORK             — "bsc-testnet" (default)
  PROVIDER_ADDRESS    — address of the job provider (default: same as client)
  BUDGET_TOKENS       — budget in human-readable token units (default: "1")
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
_cournot_root = os.environ.get(
    "COURNOT_PROTOCOL_PATH",
    os.path.join(os.path.dirname(__file__), "../../../cournot-protocol"),
)
if os.path.isdir(_cournot_root) and os.path.abspath(_cournot_root) not in sys.path:
    sys.path.append(os.path.abspath(_cournot_root))


def _section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _info(msg: str) -> None:
    print(f"  [INFO] {msg}")


def main() -> None:
    from bnbagent.wallets import EVMWalletProvider
    from bnbagent.erc8183 import ERC8183Client
    from bnbagent.erc8183.types import JobStatus

    print("\n  ERC-8183 Test Dispute Creator")
    print("  " + "=" * 50)

    private_key = os.environ.get("CLIENT_PRIVATE_KEY")
    if not private_key:
        _fail("CLIENT_PRIVATE_KEY is required")
        sys.exit(1)

    password = os.environ.get("WALLET_PASSWORD", "cournot-voter")
    network = os.environ.get("NETWORK", "bsc-testnet")

    # --------------------------------------------------------- Init
    _section("1. Initialise Client")
    wallet = EVMWalletProvider(
        password=password,
        private_key=private_key,
        persist=False,
    )
    client_address = wallet.address
    _ok(f"Client address: {client_address}")

    erc8183 = ERC8183Client(wallet_provider=wallet, network=network)
    block = erc8183.w3.eth.block_number
    _ok(f"Connected to {network} at block {block}")

    provider_address = os.environ.get("PROVIDER_ADDRESS", client_address)

    # Check balances
    balance_wei = erc8183.w3.eth.get_balance(client_address)
    _info(f"tBNB balance: {balance_wei / 10**18:.6f}")

    token_symbol = erc8183.token_symbol()
    token_decimals = erc8183.token_decimals()
    token_bal = erc8183.token_balance(client_address)
    _info(f"{token_symbol} balance: {token_bal / 10**token_decimals:.4f}")

    # --------------------------------------------------------- Create Job
    _section("2. Create Job")
    description = json.dumps({
        "version": 1,
        "negotiated_at": int(time.time()),
        "task": "Write a haiku about blockchain technology",
        "terms": {
            "deliverables": "A single haiku poem following 5-7-5 syllable structure",
            "quality_standards": "Must be valid haiku format with correct syllable count",
            "success_criteria": "Poem must mention blockchain or distributed ledger",
        },
        "price": "0",
        "currency": erc8183.payment_token,
    })

    expired_at = int(time.time()) + 7200  # 2 hours from now
    _info(f"Provider: {provider_address}")
    _info(f"Expires:  {time.strftime('%H:%M:%S', time.localtime(expired_at))}")

    try:
        result = erc8183.create_job(
            provider=provider_address,
            expired_at=expired_at,
            description=description,
        )
        job_id = result["jobId"]
        _ok(f"Job created: id={job_id}, tx={result['transactionHash']}")
    except Exception as exc:
        _fail(f"create_job failed: {exc}")
        sys.exit(1)

    # -------------------------------------------------- Register + Fund
    _section("3. Register Policy & Fund")
    try:
        result = erc8183.register_job(job_id)
        _ok(f"Job registered with policy, tx={result['transactionHash']}")
    except Exception as exc:
        _fail(f"register_job failed: {exc}")
        _info("Continuing — job may already be registered or budget=0 path")

    budget_tokens = float(os.environ.get("BUDGET_TOKENS", "0"))
    if budget_tokens > 0:
        budget_raw = int(budget_tokens * (10 ** token_decimals))
        try:
            erc8183.set_budget(job_id, budget_raw)
            _ok(f"Budget set: {budget_tokens} {token_symbol}")
            erc8183.fund(job_id, budget_raw)
            _ok(f"Job funded")
        except Exception as exc:
            _warn(f"Fund failed (may be zero-budget flow): {exc}")
    else:
        _info("Zero-budget test job (no funding required)")

    # -------------------------------------------- Submit bad deliverable
    _section("4. Submit Bad Deliverable")
    # Intentionally bad content that should NOT satisfy the haiku spec
    bad_content = "This is clearly not a haiku. It's just a random sentence about nothing."
    manifest_data = {
        "version": 1,
        "job_id": job_id,
        "chain_id": erc8183.w3.eth.chain_id,
        "contracts": {
            "commerce": erc8183.commerce.address,
            "router": erc8183.router.address,
            "policy": erc8183.policy.address,
        },
        "response": {
            "content_type": "text/plain",
            "content": bad_content,
        },
        "metadata": {"test": True},
    }
    manifest_json = json.dumps(manifest_data, separators=(",", ":"), sort_keys=True)
    manifest_hash_bytes = hashlib.sha256(manifest_json.encode()).digest()

    # For testnet we'll use a fake deliverable_url since we can't easily
    # pin to IPFS in a script.  The voter daemon will fail to fetch it
    # and treat the manifest as unavailable (which triggers abstain, not reject).
    # To get a full e2e with rejection, host the manifest JSON on a public URL.
    deliverable_url = f"data:application/json;base64,{__import__('base64').b64encode(manifest_json.encode()).decode()}"

    _info(f"Manifest hash: 0x{manifest_hash_bytes.hex()}")
    _info(f"Bad content:   {bad_content[:60]}...")

    try:
        from web3 import Web3
        # Use keccak256 for on-chain hash (matching DeliverableManifest.manifest_hash())
        on_chain_hash = Web3.keccak(text=manifest_json)

        result = erc8183.submit(
            job_id,
            deliverable=on_chain_hash,
            opt_params={"deliverable_url": deliverable_url},
        )
        _ok(f"Deliverable submitted, tx={result['transactionHash']}")
    except Exception as exc:
        _fail(f"submit failed: {exc}")
        sys.exit(1)

    # Verify job status
    job = erc8183.get_job(job_id)
    _info(f"Job status: {job.status.name}")

    # ----------------------------------------------------- Dispute
    _section("5. Dispute")
    try:
        result = erc8183.dispute(job_id)
        _ok(f"Job disputed, tx={result['transactionHash']}")
    except Exception as exc:
        _fail(f"dispute failed: {exc}")
        _info("The dispute window may not have started yet, or you're not the client")
        sys.exit(1)

    # Final status
    _section("Summary")
    _ok(f"Test disputed job created: job_id={job_id}")
    _info(f"The voter daemon should now detect the Disputed event and evaluate it.")
    _info(f"Watch the daemon logs for: [job {job_id}]")
    print()


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


if __name__ == "__main__":
    main()
