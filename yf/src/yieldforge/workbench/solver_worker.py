"""Disposable command-line worker for one synchronous Spyrrow solve."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from yieldforge.spyrrow_adapter import SpyrrowAdapter
from yieldforge.workbench.contracts import SolveRequest, WorkerMessage

MAX_REQUEST_BYTES = 4 * 1024 * 1024


def _serialized(message: WorkerMessage) -> str:
    return message.model_dump_json(exclude_none=True)


def execute_request(request: SolveRequest, emit: Callable[[str], None]) -> int:
    """Execute one validated request and emit only strict protocol messages."""

    try:
        emit(_serialized(WorkerMessage(kind="phase", phase="solving")))
        adapter = SpyrrowAdapter()
        result = adapter.run(
            request.problem,
            request.config,
            on_candidate=lambda candidate: emit(
                _serialized(WorkerMessage(kind="candidate", candidate=candidate))
            ),
        )
        emit(_serialized(WorkerMessage(kind="complete", result=result)))
        return 0
    except Exception:
        emit(
            _serialized(
                WorkerMessage(
                    kind="failure",
                    error_code="solver_failure",
                    error_message="solver worker failed",
                )
            )
        )
        return 1


def _read_request(path: Path) -> SolveRequest:
    if path.is_symlink() or not path.is_file():
        raise ValueError("request must be a regular file")
    payload = path.read_bytes()
    if len(payload) > MAX_REQUEST_BYTES:
        raise ValueError("request exceeds size limit")
    return SolveRequest.model_validate_json(payload)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        request = _read_request(args.request)
    except Exception:
        print(
            _serialized(
                WorkerMessage(
                    kind="failure",
                    error_code="solver_failure",
                    error_message="solver worker failed",
                )
            )
        )
        return 1

    return execute_request(request, lambda line: print(line, flush=True))


if __name__ == "__main__":  # pragma: no cover - exercised through subprocess supervision
    sys.exit(main())
