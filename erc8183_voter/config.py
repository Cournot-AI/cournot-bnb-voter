"""Configuration for the Cournot ERC-8183 Voter Agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def _env_required(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        raise EnvironmentError(f"Required environment variable {key} is not set")
    return val


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    return float(raw) if raw else default


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    return int(raw) if raw else default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key, "").lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes")


@dataclass(frozen=True)
class VoterConfig:
    """All settings for the Cournot Voter Agent, loaded from env vars."""

    # Wallet / chain
    voter_private_key: str = field(repr=False)
    wallet_password: str = field(repr=False, default="cournot-voter")
    network: str = "bsc-testnet"
    rpc_url: str | None = None

    # IPFS / storage gateway
    storage_gateway_url: str = "https://gateway.pinata.cloud/ipfs/"

    # Cournot pipeline
    cournot_llm_provider: str = "openai"
    cournot_llm_model: str = "gpt-4o"

    # Decision threshold
    confidence_threshold: float = 0.70

    # Polling
    poll_interval: int = 12  # seconds between block polls

    # Auto-settle
    auto_settle: bool = False

    # PoR artifact storage
    por_storage_dir: str = "./por_artifacts"

    # Logging
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> VoterConfig:
        """Load configuration from environment variables (and .env if present)."""
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:
            pass

        return cls(
            voter_private_key=_env_required("VOTER_PRIVATE_KEY"),
            wallet_password=_env("WALLET_PASSWORD", "cournot-voter") or "cournot-voter",
            network=_env("NETWORK", "bsc-testnet") or "bsc-testnet",
            rpc_url=_env("RPC_URL"),
            storage_gateway_url=_env("STORAGE_GATEWAY_URL", "https://gateway.pinata.cloud/ipfs/") or "https://gateway.pinata.cloud/ipfs/",
            cournot_llm_provider=_env("COURNOT_LLM_PROVIDER", "openai") or "openai",
            cournot_llm_model=_env("COURNOT_LLM_MODEL", "gpt-4o") or "gpt-4o",
            confidence_threshold=_env_float("CONFIDENCE_THRESHOLD", 0.70),
            poll_interval=_env_int("POLL_INTERVAL", 12),
            auto_settle=_env_bool("AUTO_SETTLE", False),
            por_storage_dir=_env("POR_STORAGE_DIR", "./por_artifacts") or "./por_artifacts",
            log_level=_env("LOG_LEVEL", "INFO") or "INFO",
        )
