"""
Adapter: Converts ERC-8183 on-chain job data into a Cournot verification query.

Follows the pattern of acp_evaluator/adapter.py::build_evaluation_query() but
tailored to the ERC-8183 job schema (JobDescription, deliverable manifest, hash
integrity check).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deliverable manifest (fetched from IPFS / HTTP)
# ---------------------------------------------------------------------------

@dataclass
class DeliverableManifest:
    """Parsed deliverable manifest fetched from an off-chain URL."""
    response: dict[str, Any]
    raw: str  # raw text content


def fetch_manifest(
    deliverable_url: str,
    gateway_url: str,
) -> Optional[DeliverableManifest]:
    """
    Fetch a deliverable manifest from a URL (HTTP or IPFS).

    If *deliverable_url* starts with ``ipfs://`` it is rewritten to use
    *gateway_url* as the HTTP gateway.

    Returns ``None`` when the manifest cannot be retrieved.
    """
    url = deliverable_url
    if url.startswith("ipfs://"):
        cid = url.removeprefix("ipfs://")
        url = f"{gateway_url.rstrip('/')}/{cid}"

    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        raw = resp.text
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            data = {"content_type": "text/plain", "content": raw}

        return DeliverableManifest(response=data, raw=raw)
    except Exception as exc:
        logger.warning("Failed to fetch manifest from %s: %s", url, exc)
        return None


def manifest_hash(manifest: DeliverableManifest) -> str:
    """SHA-256 hash of the raw manifest content."""
    return "0x" + hashlib.sha256(manifest.raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------

def build_verification_query(
    job: Any,
    job_desc: Any,
    manifest: Optional[DeliverableManifest],
    hash_ok: bool,
) -> str:
    """
    Build a natural-language verification query for the Cournot pipeline.

    Parameters
    ----------
    job : object
        The raw on-chain job object (must have at least ``.description``).
    job_desc : object | None
        Parsed ``JobDescription`` from ``bnbagent.erc8183.schema``.  May be
        ``None`` for plain-text legacy job descriptions.
    manifest : DeliverableManifest | None
        The deliverable manifest fetched from IPFS / HTTP, or ``None`` if
        the manifest could not be retrieved.
    hash_ok : bool
        Whether the on-chain deliverable hash matches the manifest hash.
    """

    # --- Job specification block ---
    if job_desc is not None:
        task = getattr(job_desc, "task", None) or str(getattr(job, "description", ""))
        terms = getattr(job_desc, "terms", None) or {}
        if not isinstance(terms, dict):
            terms = {}
        deliverables_required = terms.get("deliverables", "N/A")
        quality_standards = terms.get("quality_standards", "N/A")
        success_criteria = terms.get("success_criteria", "N/A")

        spec_block = (
            f"Task: {task}\n"
            f"Deliverables required: {deliverables_required}\n"
            f"Quality standards: {quality_standards}\n"
            f"Success criteria: {success_criteria}"
        )
    else:
        # Plain-text fallback
        spec_block = str(getattr(job, "description", ""))

    # --- Deliverable block ---
    if manifest is not None:
        content_type = manifest.response.get("content_type", "text/plain")
        content = manifest.response.get("content", manifest.raw)
        deliv_block = (
            f"Content type: {content_type}\n"
            f"Content:\n{content}"
        )
    else:
        deliv_block = "(manifest unavailable)"

    # --- Integrity check ---
    integrity_block = f"Manifest hash matches on-chain: {hash_ok}"

    query = (
        "Evaluate whether the following deliverable satisfies the job specification.\n\n"
        f"=== JOB SPECIFICATION ===\n{spec_block}\n\n"
        f"=== DELIVERABLE ===\n{deliv_block}\n\n"
        f"=== INTEGRITY CHECK ===\n{integrity_block}\n\n"
        "Answer YES if the deliverable fully satisfies the specification.\n"
        "Answer NO if it clearly does not.\n"
        "Answer INVALID if insufficient information to judge."
    )
    return query
