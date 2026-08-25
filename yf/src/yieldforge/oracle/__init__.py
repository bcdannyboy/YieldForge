"""M8 exact full-horizon rollout oracle."""

from yieldforge.oracle.checker import (
    M8PreparedCheckerContext,
    M8ProofCheckResult,
    check_action_proof,
    check_action_proofs,
    check_prepared_action_proofs,
    prepare_m8_checker_context,
)
from yieldforge.oracle.contracts import M8ActionScore, M8OracleDecision
from yieldforge.oracle.proofs import (
    M8ActionProof,
    M8EventClassification,
    M8EventWitness,
    M8InfluenceClassification,
    M8InfluenceWitness,
    build_m8_action_proof,
)
from yieldforge.oracle.reference import (
    M8OracleRequest,
    M8ReferenceResult,
    score_reference_action,
    score_reference_event,
)
from yieldforge.oracle.sparse import (
    M8PreparedGeneratorContext,
    prepare_m8_generator_context,
    score_prepared_certificate_actions,
)
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
    "M8PreparedCheckerContext",
    "M8PreparedGeneratorContext",
    "M8ProofCheckResult",
    "M8ReferenceResult",
    "build_m8_action_proof",
    "check_action_proof",
    "check_action_proofs",
    "check_prepared_action_proofs",
    "prepare_m8_checker_context",
    "prepare_m8_generator_context",
    "score_reference_action",
    "score_reference_event",
    "score_prepared_certificate_actions",
]
