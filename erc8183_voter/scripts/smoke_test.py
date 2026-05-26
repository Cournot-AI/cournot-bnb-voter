#!/usr/bin/env python3
"""
Smoke test for ERC-8183 Voter Agent on BSC testnet.

Verifies:
  1. Chain connectivity (RPC responds, block number)
  2. Contract reads (policy quorum, dispute window, voter count)
  3. Voter whitelist status
  4. Cournot pipeline evaluation (optional, needs LLM key)

Usage:
  VOTER_PRIVATE_KEY=0x... python -m erc8183_voter.scripts.smoke_test

  Or with explicit wallet password:
  VOTER_PRIVATE_KEY=0x... WALLET_PASSWORD=pw python -m erc8183_voter.scripts.smoke_test
"""

from __future__ import annotations

import json
import os
import sys
import time

# Ensure package root is on sys.path
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, _repo_root)

# Load .env from repo root before anything else
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_repo_root, ".env"))
except ImportError:
    pass

# Ensure cournot-protocol is importable (for pipeline)
_cournot_root = os.environ.get(
    "COURNOT_PROTOCOL_PATH",
    os.path.join(_repo_root, "../cournot-protocol"),
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


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def main() -> None:
    from erc8183_voter.config import VoterConfig

    print("\n  Cournot ERC-8183 Voter Agent — Smoke Test")
    print("  " + "=" * 50)

    # ---------------------------------------------------------------- Config
    _section("1. Configuration")
    try:
        config = VoterConfig.from_env()
        _ok(f"Config loaded (network={config.network})")
    except EnvironmentError as exc:
        _fail(f"Config error: {exc}")
        print("\n  Set VOTER_PRIVATE_KEY to a BSC testnet private key.")
        sys.exit(1)

    # -------------------------------------------------------- Chain connect
    _section("2. Chain Connectivity")
    try:
        from bnbagent.wallets import EVMWalletProvider
        from bnbagent.erc8183 import ERC8183Client
        from bnbagent.config import resolve_network

        nc = resolve_network(config.network)
        _info(f"RPC URL:  {config.rpc_url or nc.rpc_url}")
        _info(f"Chain ID: {nc.chain_id}")
        _info(f"Commerce: {nc.commerce_contract}")
        _info(f"Router:   {nc.router_contract}")
        _info(f"Policy:   {nc.policy_contract}")

        wallet = EVMWalletProvider(
            password=config.wallet_password,
            private_key=config.voter_private_key,
            persist=False,
        )
        voter_address = wallet.address
        _ok(f"Wallet loaded: {voter_address}")

        erc8183 = ERC8183Client(wallet_provider=wallet, network=config.network)
        block = erc8183.w3.eth.block_number
        _ok(f"Current block: {block}")
    except Exception as exc:
        _fail(f"Chain connection failed: {exc}")
        sys.exit(1)

    # --------------------------------------------------------- Contract reads
    _section("3. Contract State")
    policy = erc8183.policy
    try:
        quorum = policy.vote_quorum()
        _ok(f"Vote quorum: {quorum}")
    except Exception as exc:
        _fail(f"vote_quorum() failed: {exc}")

    try:
        window = policy.dispute_window()
        _ok(f"Dispute window: {window}s")
    except Exception as exc:
        _fail(f"dispute_window() failed: {exc}")

    try:
        voter_count = policy.active_voter_count()
        _ok(f"Active voters: {voter_count}")
    except Exception as exc:
        _fail(f"active_voter_count() failed: {exc}")

    try:
        admin_addr = policy.admin()
        _info(f"Policy admin: {admin_addr}")
    except Exception as exc:
        _warn(f"admin() failed: {exc}")

    try:
        job_count = erc8183.commerce.job_counter()
        _ok(f"Total jobs created: {job_count}")
    except Exception as exc:
        _warn(f"job_counter() failed: {exc}")

    try:
        inflight = erc8183.inflight_job_count()
        _ok(f"In-flight jobs: {inflight}")
    except Exception as exc:
        _warn(f"inflight_job_count() failed: {exc}")

    # ----------------------------------------------------- Voter whitelist
    _section("4. Voter Whitelist Check")
    try:
        is_voter = policy.is_voter(voter_address)
        if is_voter:
            _ok(f"{voter_address} IS a whitelisted voter")
        else:
            _fail(f"{voter_address} is NOT a whitelisted voter")
            _info("The daemon will refuse to start without whitelist access.")
            _info(f"Ask the policy admin ({admin_addr}) to call:")
            _info(f"  policy.add_voter('{voter_address}')")
    except Exception as exc:
        _fail(f"is_voter() failed: {exc}")

    # -------------------------------------------------- tBNB balance check
    _section("5. Wallet Balance")
    try:
        balance_wei = erc8183.w3.eth.get_balance(voter_address)
        balance_bnb = balance_wei / 10**18
        if balance_bnb > 0.001:
            _ok(f"tBNB balance: {balance_bnb:.6f}")
        else:
            _warn(f"tBNB balance: {balance_bnb:.6f} — may need gas")
            _info("Get tBNB from: https://www.bnbchain.org/en/testnet-faucet")
    except Exception as exc:
        _warn(f"Balance check failed: {exc}")

    # ------------------------------------------------ Payment token balance
    _section("6. Payment Token")
    try:
        token_addr = erc8183.payment_token
        symbol = erc8183.token_symbol()
        decimals = erc8183.token_decimals()
        token_bal = erc8183.token_balance(voter_address)
        human_bal = token_bal / (10 ** decimals)
        _ok(f"Token: {symbol} at {token_addr}")
        _ok(f"Voter balance: {human_bal:.4f} {symbol}")
    except Exception as exc:
        _warn(f"Token check failed: {exc}")

    # -------------------------------------------- Cournot pipeline (optional)
    _section("7. Cournot Pipeline (optional)")
    llm_key = os.environ.get(f"{config.cournot_llm_provider.upper()}_API_KEY") or os.environ.get("COURNOT_LLM_API_KEY")
    if not llm_key:
        _warn(f"No API key for {config.cournot_llm_provider} — skipping pipeline test")
        _info(f"Set {config.cournot_llm_provider.upper()}_API_KEY to test the pipeline")
    else:
        _info(f"LLM: {config.cournot_llm_provider} / {config.cournot_llm_model}")
        try:
            from erc8183_voter.voter import CournotVoter
            voter = CournotVoter(config)
            test_query = (
                "Evaluate whether the following deliverable satisfies the job specification.\n\n"
                "=== JOB SPECIFICATION ===\n"
                "Task: Write a haiku about blockchain\n"
                "Deliverables required: A haiku poem\n"
                "Quality standards: Must follow 5-7-5 syllable structure\n"
                "Success criteria: Mentions blockchain\n\n"
                "=== DELIVERABLE ===\n"
                "Content type: text/plain\n"
                "Content:\nHello world this is not a haiku at all.\n\n"
                "=== INTEGRITY CHECK ===\n"
                "Manifest hash matches on-chain: True\n\n"
                "Answer YES if the deliverable fully satisfies the specification.\n"
                "Answer NO if it clearly does not.\n"
                "Answer INVALID if insufficient information to judge."
            )
            _info("Running test evaluation...")
            decision = voter.evaluate(test_query)
            _ok(f"Pipeline result: outcome={decision.outcome}, confidence={decision.confidence:.2f}")
            _ok(f"should_reject={decision.should_reject}, pipeline_ok={decision.pipeline_ok}")
            if decision.errors:
                _warn(f"Errors: {decision.errors}")
        except Exception as exc:
            _fail(f"Pipeline test failed: {exc}")

    # ------------------------------------------------------------ Summary
    _section("Summary")
    _info(f"Network:        {config.network}")
    _info(f"Voter address:  {voter_address}")
    _info(f"Current block:  {block}")
    try:
        _info(f"Is whitelisted: {is_voter}")
    except NameError:
        pass
    print()
    print("  To start the daemon:")
    print(f"    VOTER_PRIVATE_KEY=0x... python -m erc8183_voter")
    print()


if __name__ == "__main__":
    main()
