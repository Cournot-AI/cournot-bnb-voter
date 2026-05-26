#!/usr/bin/env python3
"""
Test the Cournot pipeline with realistic YES / NO deliverable cases.

Usage:
    python erc8183_voter/scripts/test_pipeline.py
"""

from __future__ import annotations

import os
import sys

_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, _repo_root)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_repo_root, ".env"))
except ImportError:
    pass

_cournot_root = os.environ.get(
    "COURNOT_PROTOCOL_PATH",
    os.path.join(_repo_root, "../cournot-protocol"),
)
if os.path.isdir(_cournot_root) and os.path.abspath(_cournot_root) not in sys.path:
    sys.path.append(os.path.abspath(_cournot_root))

from erc8183_voter.adapter import build_verification_query, DeliverableManifest
from erc8183_voter.config import VoterConfig
from erc8183_voter.voter import CournotVoter


def _section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _info(msg: str) -> None:
    print(f"  [INFO] {msg}")


# ---------------------------------------------------------------------------
# Test cases: (name, job_desc, deliverable_content, expected_outcome)
# ---------------------------------------------------------------------------

class _Job:
    def __init__(self, desc: str):
        self.description = desc


class _JobDesc:
    def __init__(self, task: str, terms: dict):
        self.task = task
        self.terms = terms


CASES = [
    {
        "name": "GOOD deliverable (expect YES)",
        "job": _Job("Write a Python function to check if a number is prime"),
        "job_desc": _JobDesc(
            task="Write a Python function to check if a number is prime",
            terms={
                "deliverables": "A Python function named is_prime that takes an integer and returns True/False",
                "quality_standards": "Must handle edge cases (0, 1, negative numbers). Must be correct.",
                "success_criteria": "Function is syntactically valid Python, returns correct results for primes and non-primes",
            },
        ),
        "content": (
            "def is_prime(n: int) -> bool:\n"
            "    if n < 2:\n"
            "        return False\n"
            "    if n == 2:\n"
            "        return True\n"
            "    if n % 2 == 0:\n"
            "        return False\n"
            "    for i in range(3, int(n**0.5) + 1, 2):\n"
            "        if n % i == 0:\n"
            "            return False\n"
            "    return True\n"
        ),
        "content_type": "text/python",
    },
    {
        "name": "BAD deliverable (expect NO)",
        "job": _Job("Write a Python function to check if a number is prime"),
        "job_desc": _JobDesc(
            task="Write a Python function to check if a number is prime",
            terms={
                "deliverables": "A Python function named is_prime that takes an integer and returns True/False",
                "quality_standards": "Must handle edge cases (0, 1, negative numbers). Must be correct.",
                "success_criteria": "Function is syntactically valid Python, returns correct results for primes and non-primes",
            },
        ),
        "content": (
            "I don't know how to write code. Here is a recipe for chocolate cake:\n"
            "Ingredients: 2 cups flour, 1 cup sugar, 3 eggs, 1 cup milk.\n"
            "Instructions: Mix everything together and bake at 350F for 30 minutes.\n"
        ),
        "content_type": "text/plain",
    },
]


def main() -> None:
    print("\n  Cournot Pipeline — YES / NO Verification Test")
    print("  " + "=" * 50)

    config = VoterConfig.from_env()
    _info(f"LLM: {config.cournot_llm_provider} / {config.cournot_llm_model}")
    _info(f"Confidence threshold: {config.confidence_threshold}")

    voter = CournotVoter(config)

    for i, case in enumerate(CASES, 1):
        _section(f"Case {i}: {case['name']}")

        manifest = DeliverableManifest(
            response={
                "content_type": case["content_type"],
                "content": case["content"],
            },
            raw=case["content"],
        )

        query = build_verification_query(
            job=case["job"],
            job_desc=case["job_desc"],
            manifest=manifest,
            hash_ok=True,
        )

        _info(f"Query length: {len(query)} chars")
        _info("Running pipeline...")

        decision = voter.evaluate(query)

        print()
        _ok(f"Outcome:       {decision.outcome}")
        _ok(f"Confidence:    {decision.confidence:.2f}")
        _ok(f"Should reject: {decision.should_reject}")
        _ok(f"Pipeline OK:   {decision.pipeline_ok}")
        if decision.reasoning_summary:
            summary = decision.reasoning_summary[:200]
            _info(f"Reasoning:     {summary}")
        if decision.errors:
            _info(f"Errors:        {decision.errors}")
        if decision.por_root:
            _info(f"PoR root:      {decision.por_root}")

    _section("Summary")
    print("  Case 1 (good deliverable): should get YES")
    print("  Case 2 (bad deliverable):  should get NO + should_reject=True")
    print()


if __name__ == "__main__":
    main()
