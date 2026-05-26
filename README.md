# Cournot BNB Voter

Autonomous AI voter agent for [ERC-8183](https://github.com/bnb-chain/EIPs/pull/34) dispute settlement on BNB Chain. Uses [Cournot Protocol](https://github.com/Cournot-AI/cournot-protocol) Proof-of-Reasoning to evaluate disputed job deliverables and cast on-chain `vote_reject()` when deliverables fail verification.

## How It Works

```
Disputed event on BSC
        |
        v
+------------------+     +-------------------+     +------------------+
| Fetch job spec   | --> | Direct LLM eval   | --> | Cast vote_reject |
| + deliverable    |     | (YES/NO/INVALID)  |     | on-chain (if NO  |
| from IPFS        |     |                   |     | + high conf)     |
+------------------+     +-------------------+     +------------------+
                                  |
                                  v
                          +-------------------+
                          | Cournot Pipeline  |
                          | (PoR bundle for   |
                          | auditability)     |
                          +-------------------+
```

The agent uses a two-layer evaluation architecture:

1. **Direct LLM judgment** — Asks the LLM to evaluate the deliverable against the job specification. Returns YES/NO/INVALID with a confidence score. This drives the vote decision.

2. **Cournot PoR pipeline** — Runs the full Proof-of-Reasoning pipeline to produce a cryptographic bundle (Merkle roots over prompt spec, evidence, reasoning trace, and verdict) for on-chain auditability.

**Fail-safe rule**: The agent never rejects due to internal error. It only casts `vote_reject()` when the direct evaluation returns NO with confidence >= threshold (default 70%).

## BSC Testnet Contracts

| Contract | Address |
|----------|---------|
| AgenticCommerce | [`0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de`](https://testnet.bscscan.com/address/0xa206c0517b6371c6638cd9e4a42cc9f02a33b0de) |
| EvaluatorRouter | [`0xd7d36d66d2f1b608a0f943f722d27e3744f66f25`](https://testnet.bscscan.com/address/0xd7d36d66d2f1b608a0f943f722d27e3744f66f25) |
| OptimisticPolicy | [`0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6`](https://testnet.bscscan.com/address/0x4f4678d4439fec812ac7674bb3efb4c8f5fb78a6) |

## Setup

### Prerequisites

- Python 3.10+
- [cournot-protocol](https://github.com/Cournot-AI/cournot-protocol) repo cloned as a sibling directory (or set `COURNOT_PROTOCOL_PATH`)
- A BSC testnet wallet funded with tBNB ([faucet](https://www.bnbchain.org/en/testnet-faucet))
- The wallet address must be whitelisted as a voter on the OptimisticPolicy contract
- An OpenAI API key (or other supported LLM provider)

### Install

```bash
pip install -e .
```

### Configure

```bash
cp .env.example .env
# Edit .env with your keys:
#   VOTER_PRIVATE_KEY=0x...
#   OPENAI_API_KEY=sk-...
```

See `.env.example` for all available configuration options.

## Usage

### Smoke Test

Verify chain connectivity, voter whitelist, wallet balance, and pipeline:

```bash
python erc8183_voter/scripts/smoke_test.py
```

### Pipeline Test

Run YES/NO evaluation against test deliverables (no chain interaction):

```bash
python erc8183_voter/scripts/test_pipeline.py
```

### Start the Daemon

```bash
python -m erc8183_voter
```

The daemon will:
1. Verify the voter address is whitelisted
2. Begin polling for `Disputed` events on the OptimisticPolicy contract
3. For each disputed job: fetch deliverable, run AI evaluation, cast `vote_reject()` if warranted
4. Log a heartbeat every 5 minutes with stats
5. Shut down cleanly on SIGTERM/SIGINT

### Create a Test Dispute

Use a second wallet to create a job with a bad deliverable and dispute it:

```bash
CLIENT_PRIVATE_KEY=0x... python erc8183_voter/scripts/create_test_dispute.py
```

## Project Structure

```
erc8183_voter/
    __init__.py
    __main__.py          # python -m erc8183_voter
    config.py            # VoterConfig dataclass, loaded from env vars
    adapter.py           # ERC-8183 job data -> Cournot verification query
    voter.py             # Two-layer evaluation: direct LLM + PoR pipeline
    daemon.py            # Async event loop polling BSC for Disputed events
    storage.py           # PoR artifact persistence to disk
    cli.py               # Entry point, arg parsing, banner
    scripts/
        smoke_test.py         # Chain + pipeline verification
        test_pipeline.py      # YES/NO evaluation test cases
        create_test_dispute.py  # Create a test disputed job on-chain
    tests/
        conftest.py           # Shared fixtures
        test_adapter.py       # Query construction tests
        test_voter.py         # Vote decision logic tests
        test_daemon.py        # Daemon integration tests (mocked chain)
        test_storage.py       # Artifact persistence tests
```

## Configuration

| Env Var | Default | Purpose |
|---------|---------|---------|
| `VOTER_PRIVATE_KEY` | required | BSC wallet private key |
| `OPENAI_API_KEY` | required | LLM API key for evaluation |
| `WALLET_PASSWORD` | `cournot-voter` | Keystore encryption password |
| `NETWORK` | `bsc-testnet` | `bsc-testnet` or `bsc-mainnet` |
| `RPC_URL` | network default | Custom RPC endpoint |
| `COURNOT_LLM_PROVIDER` | `openai` | LLM provider |
| `COURNOT_LLM_MODEL` | `gpt-4o` | LLM model |
| `CONFIDENCE_THRESHOLD` | `0.70` | Minimum confidence to reject |
| `POLL_INTERVAL` | `12` | Seconds between block polls |
| `AUTO_SETTLE` | `false` | Auto-call `settle()` on quorum |
| `POR_STORAGE_DIR` | `./por_artifacts` | PoR artifact directory |
| `LOG_LEVEL` | `INFO` | Logging level |
| `COURNOT_PROTOCOL_PATH` | `../cournot-protocol` | Path to cournot-protocol repo |

## Testing

```bash
pytest erc8183_voter/tests/ -v
```

44 tests covering:
- **test_adapter.py** — Query construction from structured/plain-text job descriptions, manifest handling, hash verification
- **test_voter.py** — Decision logic: NO+high confidence=reject, NO+low=abstain, YES=abstain, INVALID=abstain, fail-safe on errors
- **test_daemon.py** — Event loop integration with mocked chain: reject flow, abstain flow, skip already-voted, skip non-SUBMITTED, tx failure handling, preflight checks
- **test_storage.py** — Artifact file creation, JSON serialization, directory structure

## Architecture

### Vote Decision Flow

```
Disputed event detected
    |
    +-- Guard: already voted? --> skip
    +-- Guard: job still SUBMITTED? --> skip
    |
    v
Fetch job spec (on-chain) + deliverable manifest (IPFS)
    |
    v
Build verification query (job spec + deliverable + hash check)
    |
    +---> Direct LLM evaluation --> YES/NO/INVALID + confidence
    |         (drives the vote decision)
    |
    +---> Cournot PoR pipeline --> PoR bundle with Merkle roots
    |         (cryptographic proof for auditability)
    |
    v
Decision: should_reject = (outcome == NO) AND (confidence >= threshold)
    |
    +-- YES --> abstain, save artifacts
    +-- NO + high confidence --> vote_reject() on-chain, save artifacts
    +-- NO + low confidence --> abstain, save artifacts
    +-- INVALID --> abstain, save artifacts
    +-- Pipeline error --> abstain (fail-safe), save error record
```

### PoR Artifacts

For every evaluated job, the agent persists to `{POR_STORAGE_DIR}/{job_id}/`:

- `decision.json` — Action taken, outcome, confidence, tx hash, timestamp
- `query.txt` — The verification query sent to the LLM
- `por_bundle.json` — Full cryptographic Proof-of-Reasoning bundle

## Dependencies

- [bnbagent](https://pypi.org/project/bnbagent/) — BNB Chain ERC-8183 SDK (wallet, contracts, events)
- [cournot-protocol](https://github.com/Cournot-AI/cournot-protocol) — PoR pipeline (LLM orchestration, evidence collection, cryptographic proofs)

## License

See [LICENSE](LICENSE) for details.
