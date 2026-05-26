"""Unit tests for erc8183_voter.storage (PoR artifact persistence)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from erc8183_voter.storage import PoRStorage


@pytest.fixture
def storage_dir(tmp_path: Path) -> Path:
    return tmp_path / "por_artifacts"


@pytest.fixture
def storage(storage_dir: Path) -> PoRStorage:
    return PoRStorage(str(storage_dir))


# ---------------------------------------------------------------------------
# Tests: directory creation
# ---------------------------------------------------------------------------

class TestDirectoryCreation:
    def test_base_dir_created(self, storage: PoRStorage, storage_dir: Path):
        assert storage_dir.exists()

    def test_job_dir_created_on_save(self, storage: PoRStorage, storage_dir: Path):
        storage.save(
            job_id=42,
            action="abstain",
            outcome="YES",
            confidence=0.80,
            query="test query",
        )
        assert (storage_dir / "42").is_dir()


# ---------------------------------------------------------------------------
# Tests: file creation
# ---------------------------------------------------------------------------

class TestFileCreation:
    def test_decision_json_written(self, storage: PoRStorage, storage_dir: Path):
        storage.save(
            job_id=1,
            action="vote_reject",
            outcome="NO",
            confidence=0.90,
            query="test query",
            tx_hash="0xabc123",
        )
        decision_path = storage_dir / "1" / "decision.json"
        assert decision_path.exists()

        data = json.loads(decision_path.read_text())
        assert data["job_id"] == "1"
        assert data["action"] == "vote_reject"
        assert data["outcome"] == "NO"
        assert data["confidence"] == 0.90
        assert data["tx_hash"] == "0xabc123"
        assert "timestamp" in data

    def test_query_txt_written(self, storage: PoRStorage, storage_dir: Path):
        storage.save(
            job_id=2,
            action="abstain",
            outcome="INVALID",
            confidence=0.30,
            query="the verification query text",
        )
        query_path = storage_dir / "2" / "query.txt"
        assert query_path.exists()
        assert query_path.read_text() == "the verification query text"

    def test_por_bundle_json_written(self, storage: PoRStorage, storage_dir: Path):
        bundle = {"por_root": "0x" + "aa" * 32, "metadata": {"key": "value"}}
        storage.save(
            job_id=3,
            action="vote_reject",
            outcome="NO",
            confidence=0.85,
            query="q",
            por_bundle=bundle,
        )
        bundle_path = storage_dir / "3" / "por_bundle.json"
        assert bundle_path.exists()

        data = json.loads(bundle_path.read_text())
        assert data["por_root"] == "0x" + "aa" * 32

    def test_no_por_bundle_when_none(self, storage: PoRStorage, storage_dir: Path):
        storage.save(
            job_id=4,
            action="abstain",
            outcome="YES",
            confidence=0.80,
            query="q",
            por_bundle=None,
        )
        assert not (storage_dir / "4" / "por_bundle.json").exists()


# ---------------------------------------------------------------------------
# Tests: errors field
# ---------------------------------------------------------------------------

class TestErrorsPersistence:
    def test_errors_saved(self, storage: PoRStorage, storage_dir: Path):
        storage.save(
            job_id=5,
            action="abstain",
            outcome=None,
            confidence=0.0,
            query="q",
            errors=["pipeline failed", "timeout"],
        )
        data = json.loads((storage_dir / "5" / "decision.json").read_text())
        assert data["errors"] == ["pipeline failed", "timeout"]

    def test_empty_errors_default(self, storage: PoRStorage, storage_dir: Path):
        storage.save(
            job_id=6,
            action="abstain",
            outcome="YES",
            confidence=0.80,
            query="q",
        )
        data = json.loads((storage_dir / "6" / "decision.json").read_text())
        assert data["errors"] == []


# ---------------------------------------------------------------------------
# Tests: Pydantic model serialization
# ---------------------------------------------------------------------------

class TestPydanticBundleSerialization:
    def test_model_dump_called(self, storage: PoRStorage, storage_dir: Path):
        class FakeBundle:
            def model_dump(self, **kwargs):
                return {"por_root": "0xfake", "schema": "v1"}

        storage.save(
            job_id=7,
            action="vote_reject",
            outcome="NO",
            confidence=0.90,
            query="q",
            por_bundle=FakeBundle(),
        )
        data = json.loads((storage_dir / "7" / "por_bundle.json").read_text())
        assert data["por_root"] == "0xfake"


# ---------------------------------------------------------------------------
# Tests: return value
# ---------------------------------------------------------------------------

class TestReturnValue:
    def test_returns_job_directory(self, storage: PoRStorage, storage_dir: Path):
        result = storage.save(
            job_id=8,
            action="abstain",
            outcome="YES",
            confidence=0.80,
            query="q",
        )
        assert result == storage_dir / "8"
