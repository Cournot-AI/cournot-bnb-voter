"""
CLI entry point for the Cournot ERC-8183 Voter Agent.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys


BANNER = r"""
  ____                            _    __     __    _
 / ___|___  _   _ _ __ _ __   ___ | |_  \ \   / /__ | |_ ___ _ __
| |   / _ \| | | | '__| '_ \ / _ \| __|  \ \ / / _ \| __/ _ \ '__|
| |__| (_) | |_| | |  | | | | (_) | |_    \ V / (_) | ||  __/ |
 \____\___/ \__,_|_|  |_| |_|\___/ \__|    \_/ \___/ \__\___|_|

  ERC-8183 Dispute Voter Agent
  Powered by Cournot Proof-of-Reasoning
"""


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="erc8183_voter",
        description="Cournot ERC-8183 Voter Agent - AI-powered dispute verification",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress the startup banner",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level (default: from VOTER_CONFIG or INFO)",
    )
    args = parser.parse_args(argv)

    # Print banner
    if not args.quiet:
        print(BANNER)

    # Load config
    from erc8183_voter.config import VoterConfig

    try:
        config = VoterConfig.from_env()
    except EnvironmentError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Configure logging
    log_level = args.log_level or config.log_level
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logger = logging.getLogger("erc8183_voter")
    logger.info("Network:    %s", config.network)
    logger.info("RPC URL:    %s", config.rpc_url or "(network default)")
    logger.info("LLM:        %s / %s", config.cournot_llm_provider, config.cournot_llm_model)
    logger.info("Threshold:  %.2f", config.confidence_threshold)
    logger.info("Poll:       %ds", config.poll_interval)
    logger.info("Auto-settle: %s", config.auto_settle)
    logger.info("PoR dir:    %s", config.por_storage_dir)

    # Run daemon
    from erc8183_voter.daemon import VoterDaemon

    daemon = VoterDaemon(config)
    asyncio.run(daemon.run())
