"""
PoR artifact persistence for ERC-8183 voter decisions.

Saves decision metadata, the full PoR bundle, and the verification query
to ``{por_storage_dir}/{job_id}/``.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class PoRStorage:
    """Persists PoR artifacts and decision records to local disk."""

    def __init__(self, base_dir: str) -> None:
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    def _job_dir(self, job_id: int | str) -> Path:
        d = self._base / str(job_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(
        self,
        job_id: int | str,
        *,
        action: str,
        outcome: Optional[str],
        confidence: float,
        query: str,
        por_bundle: Any = None,
        tx_hash: Optional[str] = None,
        errors: Optional[list[str]] = None,
    ) -> Path:
        """
        Persist all artifacts for a voter decision.

        Returns the directory where artifacts were saved.
        """
        d = self._job_dir(job_id)

        # decision.json
        decision = {
            "job_id": str(job_id),
            "action": action,
            "outcome": outcome,
            "confidence": confidence,
            "tx_hash": tx_hash,
            "errors": errors or [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        (d / "decision.json").write_text(json.dumps(decision, indent=2))

        # query.txt
        (d / "query.txt").write_text(query)

        # por_bundle.json
        if por_bundle is not None:
            bundle_data = _serialize_por(por_bundle)
            (d / "por_bundle.json").write_text(json.dumps(bundle_data, indent=2))

        logger.info("Saved PoR artifacts for job %s -> %s", job_id, d)
        return d


def _serialize_por(bundle: Any) -> Any:
    """Best-effort serialization of a PoR bundle (Pydantic model or dict)."""
    if hasattr(bundle, "model_dump"):
        return bundle.model_dump(mode="json")
    if hasattr(bundle, "dict"):
        return bundle.dict()
    if isinstance(bundle, dict):
        return bundle
    return str(bundle)
