"""M8 exact full-horizon rollout oracle."""

from yieldforge.oracle.contracts import M8ActionScore, M8OracleDecision
from yieldforge.oracle.proofs import (
    M8ActionProof,
    M8EventClassification,
    M8EventWitness,
    M8InfluenceClassification,
    M8InfluenceWitness,
    build_m8_action_proof,
)
from yieldforge.oracle.reference import M8OracleRequest, M8ReferenceResult, score_reference_event
from yieldforge.oracle.visibility import FullRealizedVisibility, KnownOnlyVisibility

__all__ = [
    "FullRealizedVisibility",
    "KnownOnlyVisibility",
    "M8ActionScore",
    "M8ActionProof",
    "M8EventClassification",
    "M8EventWitness",
    "M8InfluenceClassification",
    "M8InfluenceWitness",
    "M8OracleDecision",
    "M8OracleRequest",
    "M8ReferenceResult",
    "build_m8_action_proof",
    "score_reference_event",
]
