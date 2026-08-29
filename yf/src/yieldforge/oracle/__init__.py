"""M8 exact full-horizon rollout oracle."""

from yieldforge.oracle.checker import (
    M8ProofCheckResult,
    check_action_proof,
    check_action_proofs,
)
from yieldforge.oracle.contracts import M8ActionScore, M8OracleDecision
from yieldforge.oracle.facts import (
    M8ActionRootV2,
    M8CandidateScalarFactV2,
    M8CommonTransitionLemmaV2,
    M8FrontierFactV2,
    M8InfluenceFactV2,
    M8PortableTranslationBatch,
    M8StandardCandidateFactV2,
    M8UncheckedFactBundleV2,
)
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
    score_reference_actions,
    score_reference_event,
)
from yieldforge.oracle.visibility import FullRealizedVisibility, KnownOnlyVisibility

__all__ = [
    "FullRealizedVisibility",
    "KnownOnlyVisibility",
    "M8ActionScore",
    "M8ActionProof",
    "M8ActionRootV2",
    "M8CandidateScalarFactV2",
    "M8CommonTransitionLemmaV2",
    "M8EventClassification",
    "M8EventWitness",
    "M8FrontierFactV2",
    "M8InfluenceFactV2",
    "M8InfluenceClassification",
    "M8InfluenceWitness",
    "M8OracleDecision",
    "M8OracleRequest",
    "M8ProofCheckResult",
    "M8PortableTranslationBatch",
    "M8ReferenceResult",
    "M8StandardCandidateFactV2",
    "M8UncheckedFactBundleV2",
    "build_m8_action_proof",
    "check_action_proof",
    "check_action_proofs",
    "score_reference_action",
    "score_reference_actions",
    "score_reference_event",
]
