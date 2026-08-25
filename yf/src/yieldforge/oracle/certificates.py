"""Exact no-fit and policy-dominance certificates for one M8 future event."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from yieldforge.baseline.contracts import LayoutFitSearchResult
from yieldforge.baseline.policies import PolicyRank, rank_policy_action
from yieldforge.baseline.replay import (
    M7ActionDescriptor,
    M7PolicyActionBinding,
    M7ReplayCursor,
    M7ReplayRuntime,
    enumerate_m7_single_remnant_competitor,
)
from yieldforge.experiments.contracts import semantic_sha256
from yieldforge.oracle.compiled import (
    CompiledTranslationRejection,
    compile_translation_rejections,
)
from yieldforge.oracle.proofs import M8EventWitness, M8InfluenceWitness
from yieldforge.replay.contracts import InventoryItem


def _item_ids(items: tuple[InventoryItem, ...]) -> tuple[str, ...]:
    return tuple(item.remnant.remnant_id for item in items)


@dataclass(frozen=True)
class BranchInventoryDelta:
    """Exact branch inventory differences relative to one common M7 cursor."""

    added: tuple[InventoryItem, ...]
    removed: tuple[InventoryItem, ...]

    def __post_init__(self) -> None:
        added_ids = _item_ids(self.added)
        removed_ids = _item_ids(self.removed)
        if added_ids != tuple(sorted(set(added_ids))):
            raise ValueError("M8 added remnant identities must be sorted unique")
        if removed_ids != tuple(sorted(set(removed_ids))):
            raise ValueError("M8 removed remnant identities must be sorted unique")
        if set(added_ids) & set(removed_ids):
            raise ValueError("M8 added and removed remnant identities must be disjoint")


@dataclass(frozen=True)
class EventPassivityResult:
    passive: bool
    witness: M8EventWitness | None
    exact_search_count: int

    def __post_init__(self) -> None:
        if self.exact_search_count < 0:
            raise ValueError("M8 exact search count cannot be negative")
        if self.passive != (self.witness is not None):
            raise ValueError("M8 passive result must carry exactly one event witness")


def _rank_payload(rank: PolicyRank) -> dict[str, object]:
    return {
        "policy": rank.policy.value,
        "comparison_key": rank.comparison_key,
        "decision_key": rank.decision_key,
    }


def _rejection_payload(
    rejections: tuple[CompiledTranslationRejection, ...],
) -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "candidate_id": item.candidate_id,
            "certificate": asdict(item.certificate),
        }
        for item in rejections
    )


def _search_payload(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    item: InventoryItem,
) -> tuple[dict[str, object], ...]:
    binding = runtime.replay_input.instances[event_position]
    verified = runtime.runtime_candidates[binding.problem_id]
    cache_key = (
        item.remnant.remnant_id,
        binding.problem_id,
        verified.evidence.candidate_set_id,
    )
    searches = runtime.fit_search_cache.get(cache_key)
    if searches is None:
        raise ValueError("M8 exact single-remnant search did not populate frozen evidence")
    canonical = tuple(
        LayoutFitSearchResult.model_validate(search.model_dump(mode="python"), strict=True)
        for search in searches
    )
    if (
        tuple(search.candidate_id for search in canonical)
        != tuple(candidate.candidate_id for candidate in verified.candidates)
        or any(search.remnant_id != item.remnant.remnant_id for search in canonical)
        or any(search.config != runtime.replay_input.search_config for search in canonical)
    ):
        raise ValueError("M8 exact search identities differ from the frozen request")
    return tuple(search.model_dump(mode="json") for search in canonical)


def _evidence_sha256(
    runtime: M7ReplayRuntime,
    *,
    event_position: int,
    item: InventoryItem,
    direction: str,
    delta: BranchInventoryDelta,
    common: M7PolicyActionBinding,
    common_rank: PolicyRank,
    common_action_id: str,
    branch_action_id: str,
    state_before_sha256: str,
    state_after_sha256: str,
    rejections: tuple[CompiledTranslationRejection, ...],
    searches: tuple[dict[str, object], ...],
    competitor: M7ActionDescriptor | None,
    competitor_rank: PolicyRank | None,
) -> str:
    replay_input = runtime.replay_input
    binding = replay_input.instances[event_position]
    problem = next(
        problem for problem in replay_input.problems if problem.problem_id == binding.problem_id
    )
    verified = runtime.runtime_candidates[binding.problem_id]
    competitor_evidence = None
    if competitor is not None:
        if competitor.evidence is None:
            raise ValueError("M8 remnant competitor lacks exact materialized evidence")
        competitor_evidence = {
            "catalog_action_id": competitor.action_id,
            "materialized_action_id": competitor.evidence.action_id,
            "action": competitor.evidence.model_dump(mode="json"),
            "rank": _rank_payload(competitor_rank) if competitor_rank is not None else None,
        }
    payload = {
        "schema_version": "yieldforge.m8-event-influence-evidence.v1",
        "replay_input_id": replay_input.input_id,
        "replay_input_sha256": replay_input.content_sha256,
        "engine": replay_input.engine.model_dump(mode="json"),
        "collision_backend": replay_input.collision_backend,
        "jagua_container_guard": replay_input.jagua_container_guard,
        "event_position": event_position,
        "binding": binding.model_dump(mode="json"),
        "problem": problem.model_dump(mode="json"),
        "candidate_set": verified.evidence.model_dump(mode="json"),
        "candidates": tuple(candidate.model_dump(mode="json") for candidate in verified.candidates),
        "fit_config": replay_input.fit_config.model_dump(mode="json"),
        "search_config": replay_input.search_config.model_dump(mode="json"),
        "policy": replay_input.policy.model_dump(mode="json"),
        "direction": direction,
        "delta_added_ids": _item_ids(delta.added),
        "delta_removed_ids": _item_ids(delta.removed),
        "inventory_item": item.model_dump(mode="json"),
        "remnant": item.remnant.model_dump(mode="json"),
        "common": {
            "catalog_action_id": common.catalog_action_id,
            "materialized_action_id": common.materialized_action_id,
            "context": asdict(common.context),
            "rank": _rank_payload(common_rank),
        },
        "common_action_id": common_action_id,
        "branch_action_id": branch_action_id,
        "state_before_sha256": state_before_sha256,
        "state_after_sha256": state_after_sha256,
        "cheap_rejections": _rejection_payload(rejections),
        "exact_searches": searches,
        "competitor": competitor_evidence,
    }
    return f"sha256:{semantic_sha256(payload)}"


def _influence(
    runtime: M7ReplayRuntime,
    *,
    cursor_template: M7ReplayCursor,
    event_position: int,
    item: InventoryItem,
    direction: str,
    delta: BranchInventoryDelta,
    common: M7PolicyActionBinding,
    common_rank: PolicyRank,
    common_action_id: str,
    branch_action_id: str,
    state_before_sha256: str,
    state_after_sha256: str,
) -> tuple[M8InfluenceWitness | None, int]:
    rejections = compile_translation_rejections(
        runtime,
        event_position=event_position,
        item=item,
    )
    if rejections and all(entry.certificate.impossible for entry in rejections):
        digest = _evidence_sha256(
            runtime,
            event_position=event_position,
            item=item,
            direction=direction,
            delta=delta,
            common=common,
            common_rank=common_rank,
            common_action_id=common_action_id,
            branch_action_id=branch_action_id,
            state_before_sha256=state_before_sha256,
            state_after_sha256=state_after_sha256,
            rejections=rejections,
            searches=(),
            competitor=None,
            competitor_rank=None,
        )
        return (
            M8InfluenceWitness(
                remnant_id=item.remnant.remnant_id,
                classification="no_fit",
                evidence_sha256=digest,
                common_action_id=common_action_id,
                common_catalog_action_id=common.catalog_action_id,
                common_decision_key=common_rank.decision_key,
            ),
            0,
        )

    competitor, context = enumerate_m7_single_remnant_competitor(
        runtime,
        event_position=event_position,
        item=item,
        cursor_template=cursor_template,
    )
    searches = _search_payload(runtime, event_position=event_position, item=item)
    if competitor is None or context is None:
        if competitor is not None or context is not None:
            raise ValueError("M8 exact competitor descriptor and context must appear together")
        digest = _evidence_sha256(
            runtime,
            event_position=event_position,
            item=item,
            direction=direction,
            delta=delta,
            common=common,
            common_rank=common_rank,
            common_action_id=common_action_id,
            branch_action_id=branch_action_id,
            state_before_sha256=state_before_sha256,
            state_after_sha256=state_after_sha256,
            rejections=rejections,
            searches=searches,
            competitor=None,
            competitor_rank=None,
        )
        return (
            M8InfluenceWitness(
                remnant_id=item.remnant.remnant_id,
                classification="no_fit",
                evidence_sha256=digest,
                common_action_id=common_action_id,
                common_catalog_action_id=common.catalog_action_id,
                common_decision_key=common_rank.decision_key,
            ),
            1,
        )

    competitor_rank = rank_policy_action(runtime.replay_input.policy.name, context)
    if not common_rank <= competitor_rank:
        return None, 1
    if competitor.evidence is None:
        raise ValueError("M8 exact remnant competitor lacks materialized evidence")
    digest = _evidence_sha256(
        runtime,
        event_position=event_position,
        item=item,
        direction=direction,
        delta=delta,
        common=common,
        common_rank=common_rank,
        common_action_id=common_action_id,
        branch_action_id=branch_action_id,
        state_before_sha256=state_before_sha256,
        state_after_sha256=state_after_sha256,
        rejections=rejections,
        searches=searches,
        competitor=competitor,
        competitor_rank=competitor_rank,
    )
    return (
        M8InfluenceWitness(
            remnant_id=item.remnant.remnant_id,
            candidate_id=competitor.candidate_id,
            classification="policy_dominated",
            evidence_sha256=digest,
            common_action_id=common_action_id,
            common_catalog_action_id=common.catalog_action_id,
            competing_action_id=competitor.evidence.action_id,
            competing_catalog_action_id=competitor.action_id,
            common_decision_key=common_rank.decision_key,
            competing_decision_key=competitor_rank.decision_key,
        ),
        1,
    )


def certify_event_passivity(
    runtime: M7ReplayRuntime,
    *,
    cursor_template: M7ReplayCursor,
    event_position: int,
    common: M7PolicyActionBinding,
    delta: BranchInventoryDelta,
    common_action_id: str,
    branch_action_id: str,
    state_before_sha256: str,
    state_after_sha256: str,
) -> EventPassivityResult:
    """Prove one branch event selects the exact common M7 action, or fail closed."""

    if event_position != cursor_template.next_event_position:
        raise ValueError("M8 certificate event position differs from cursor")
    if common.materialized_action_id != common_action_id:
        raise ValueError("M8 supplied common action differs from its materialized binding")
    common_ids = set(_item_ids(cursor_template.inventory))
    if any(item.remnant.remnant_id in common_ids for item in delta.added):
        raise ValueError("M8 added remnant is already present in the common inventory")
    if any(item.remnant.remnant_id not in common_ids for item in delta.removed):
        raise ValueError("M8 removed remnant is absent from the common inventory")
    if branch_action_id != common_action_id or not delta.added and not delta.removed:
        return EventPassivityResult(passive=False, witness=None, exact_search_count=0)
    if common.context.selected_stock_id in set(_item_ids(delta.removed)):
        return EventPassivityResult(passive=False, witness=None, exact_search_count=0)

    common_rank = rank_policy_action(runtime.replay_input.policy.name, common.context)
    influences = []
    exact_search_count = 0
    for direction, items in (("added", delta.added), ("removed", delta.removed)):
        for item in items:
            influence, searches = _influence(
                runtime,
                cursor_template=cursor_template,
                event_position=event_position,
                item=item,
                direction=direction,
                delta=delta,
                common=common,
                common_rank=common_rank,
                common_action_id=common_action_id,
                branch_action_id=branch_action_id,
                state_before_sha256=state_before_sha256,
                state_after_sha256=state_after_sha256,
            )
            exact_search_count += searches
            if influence is None:
                return EventPassivityResult(
                    passive=False,
                    witness=None,
                    exact_search_count=exact_search_count,
                )
            influences.append(influence)

    classification = (
        "no_fit"
        if all(item.classification == "no_fit" for item in influences)
        else "policy_dominated"
    )
    return EventPassivityResult(
        passive=True,
        witness=M8EventWitness(
            event_position=event_position,
            classification=classification,
            common_action_id=common_action_id,
            branch_action_id=branch_action_id,
            state_before_sha256=state_before_sha256,
            state_after_sha256=state_after_sha256,
            influences=tuple(influences),
        ),
        exact_search_count=exact_search_count,
    )


__all__ = [
    "BranchInventoryDelta",
    "EventPassivityResult",
    "certify_event_passivity",
]
