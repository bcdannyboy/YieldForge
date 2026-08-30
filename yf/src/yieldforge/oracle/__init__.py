"""M8 exact full-horizon rollout oracle with lazy public exports."""

from __future__ import annotations

from importlib import import_module

_EXPORT_MODULES = {
    "FullRealizedVisibility": "visibility",
    "KnownOnlyVisibility": "visibility",
    "M8ActionProof": "proofs",
    "M8ActionRootV2": "facts",
    "M8ActionScore": "contracts",
    "M8CandidateScalarFactV2": "facts",
    "M8CommonTransitionLemmaV2": "facts",
    "M8EventClassification": "proofs",
    "M8EventWitness": "proofs",
    "M8FrontierFactV2": "facts",
    "M8InfluenceClassification": "proofs",
    "M8InfluenceFactV2": "facts",
    "M8InfluenceWitness": "proofs",
    "M8OracleDecision": "contracts",
    "M8OracleRequest": "reference",
    "M8PortableTranslationBatch": "facts",
    "M8ProofCheckResult": "checker",
    "M8ReferenceResult": "reference",
    "M8StandardCandidateFactV2": "facts",
    "M8UncheckedFactBundleV2": "facts",
    "build_m8_action_proof": "proofs",
    "check_action_proof": "checker",
    "check_action_proofs": "checker",
    "score_reference_action": "reference",
    "score_reference_actions": "reference",
    "score_reference_event": "reference",
}

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


def __getattr__(name: str) -> object:
    """Import one legacy public export only when it is first requested."""

    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f"{__name__}.{module_name}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy public names to normal package introspection."""

    return sorted({*globals(), *__all__})
