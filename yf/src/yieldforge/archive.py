"""Immutable, content-addressable archives for generated candidates."""

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from pydantic import BaseModel

from yieldforge.domain import CandidateBatch, SourceTaskBinding


def canonical_json(value: BaseModel | dict[str, Any]) -> str:
    """Serialize a contract deterministically for hashing and JSONL storage."""

    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(data, allow_nan=False, separators=(",", ":"), sort_keys=True)


def batch_content_hash(batch: CandidateBatch) -> str:
    """Return the stable SHA-256 identity of a complete candidate batch."""

    return hashlib.sha256(canonical_json(batch).encode()).hexdigest()


class CandidateArchive:
    """Write a candidate batch once and refuse destructive replacement."""

    @classmethod
    def create(
        cls,
        output: Path,
        batch: CandidateBatch,
        *,
        source_task_binding: SourceTaskBinding | None = None,
    ) -> Path:
        output = Path(output)
        if output.exists():
            raise FileExistsError(f"archive path already exists: {output}")

        output.parent.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": "yieldforge.candidate-archive.v1",
            "candidate_count": len(batch.candidates),
            "batch_sha256": batch_content_hash(batch),
            "problem": batch.problem.model_dump(mode="json"),
            "solver": batch.solver.model_dump(mode="json"),
            "config": batch.config.model_dump(mode="json"),
        }
        if source_task_binding is not None:
            manifest["source_task_binding"] = source_task_binding.model_dump(mode="json")

        with TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temp_dir:
            staging = Path(temp_dir) / "archive"
            staging.mkdir()
            (staging / "manifest.json").write_text(
                json.dumps(manifest, allow_nan=False, indent=2, sort_keys=True) + "\n"
            )
            candidate_records = "\n".join(
                canonical_json(candidate) for candidate in batch.candidates
            )
            if candidate_records:
                candidate_records += "\n"
            (staging / "candidates.jsonl").write_text(candidate_records)
            staging.rename(output)

        return output
