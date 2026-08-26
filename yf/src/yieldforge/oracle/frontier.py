"""Deterministic Pareto reduction for conservative M8 rejection scalars."""

from __future__ import annotations

import math
from dataclasses import dataclass

from yieldforge.baseline.archives import VerifiedCandidateRejectionLayout


@dataclass(frozen=True, slots=True)
class RejectionScalar:
    """One portable candidate necessary-fit vector and its frozen bindings."""

    problem_id: str
    problem_sha256: str
    candidate_set_id: str
    candidate_set_sha256: str
    candidate_id: str
    source_transform_sha256: str
    material_partition: str
    fit_config_sha256: str
    area: float
    width: float
    height: float

    def __post_init__(self) -> None:
        identities = (
            self.problem_id,
            self.problem_sha256,
            self.candidate_set_id,
            self.candidate_set_sha256,
            self.candidate_id,
            self.source_transform_sha256,
            self.material_partition,
            self.fit_config_sha256,
        )
        if any(not isinstance(value, str) or not value for value in identities):
            raise ValueError("rejection scalar identities must be nonempty strings")
        if any(
            type(value) not in (int, float) or not math.isfinite(value) or value <= 0
            for value in (self.area, self.width, self.height)
        ):
            raise ValueError("rejection scalar dimensions must be finite and positive")

    @classmethod
    def from_verified(
        cls,
        retained: VerifiedCandidateRejectionLayout,
    ) -> RejectionScalar:
        return cls(
            problem_id=retained.problem_id,
            problem_sha256=retained.problem_sha256,
            candidate_set_id=retained.candidate_set_id,
            candidate_set_sha256=retained.candidate_set_sha256,
            candidate_id=retained.candidate_id,
            source_transform_sha256=retained.source_transform_sha256,
            material_partition=retained.material_binding_scope,
            fit_config_sha256=retained.fit_config_sha256,
            area=retained.layout_area,
            width=retained.layout_width,
            height=retained.layout_height,
        )

    @property
    def partition_key(self) -> tuple[str, ...]:
        return (
            self.problem_id,
            self.problem_sha256,
            self.candidate_set_id,
            self.candidate_set_sha256,
            self.material_partition,
            self.fit_config_sha256,
        )

    @property
    def identity_key(self) -> tuple[str, ...]:
        return (*self.partition_key, self.candidate_id)

    @property
    def vector(self) -> tuple[float, float, float]:
        return (self.area, self.width, self.height)


def dominates(left: RejectionScalar, right: RejectionScalar) -> bool:
    """Return whether left is no harder to fit, with canonical duplicate ownership."""

    if left.partition_key != right.partition_key or left.candidate_id == right.candidate_id:
        return False
    componentwise = (
        left.area <= right.area
        and left.width <= right.width
        and left.height <= right.height
    )
    if not componentwise:
        return False
    return left.vector != right.vector or left.candidate_id < right.candidate_id


@dataclass(frozen=True, slots=True)
class DominanceEdge:
    """Auditable assignment from one eliminated member to one retained dominator."""

    partition_key: tuple[str, ...]
    dominated_candidate_id: str
    retained_candidate_id: str

    def __post_init__(self) -> None:
        if len(self.partition_key) != 6 or any(not value for value in self.partition_key):
            raise ValueError("dominance edge partition must be complete")
        if (
            not self.dominated_candidate_id
            or not self.retained_candidate_id
            or self.dominated_candidate_id == self.retained_candidate_id
        ):
            raise ValueError("dominance edge candidates must be distinct and nonempty")


@dataclass(frozen=True, slots=True)
class ParetoFrontier:
    """Complete scalar membership, minimal retained set, and dominance witnesses."""

    members: tuple[RejectionScalar, ...]
    retained: tuple[RejectionScalar, ...]
    dominated_by: tuple[DominanceEdge, ...]

    def __post_init__(self) -> None:
        if self.members != tuple(sorted(self.members, key=lambda item: item.identity_key)):
            raise ValueError("frontier members must be canonically ordered")
        member_keys = tuple(item.identity_key for item in self.members)
        if len(member_keys) != len(set(member_keys)):
            raise ValueError("frontier candidate identities must be unique")
        retained_keys = {item.identity_key for item in self.retained}
        if not retained_keys <= set(member_keys):
            raise ValueError("frontier retained entries must belong to its members")
        member_by_partition_id = {
            (item.partition_key, item.candidate_id): item for item in self.members
        }
        dominated_keys: set[tuple[tuple[str, ...], str]] = set()
        for edge in self.dominated_by:
            dominated_key = (edge.partition_key, edge.dominated_candidate_id)
            retained_key = (edge.partition_key, edge.retained_candidate_id)
            if dominated_key in dominated_keys:
                raise ValueError("frontier member has multiple dominance assignments")
            dominated = member_by_partition_id.get(dominated_key)
            retained = member_by_partition_id.get(retained_key)
            if (
                dominated is None
                or retained is None
                or retained.identity_key not in retained_keys
                or not dominates(retained, dominated)
            ):
                raise ValueError("frontier dominance edge is not componentwise valid")
            dominated_keys.add(dominated_key)
        classified = retained_keys | {
            (*partition_key, candidate_id)
            for partition_key, candidate_id in dominated_keys
        }
        if classified != set(member_keys):
            raise ValueError("frontier does not classify every scalar member exactly once")
        if any(
            dominates(left, right)
            for left in self.retained
            for right in self.retained
            if left is not right
        ):
            raise ValueError("frontier retains a dominated scalar")


def build_pareto_frontier(
    scalars: tuple[RejectionScalar, ...],
) -> ParetoFrontier:
    """Build a deterministic O(n^2) frontier for the bounded verified corpus."""

    members = tuple(sorted(scalars, key=lambda item: item.identity_key))
    identities = tuple(item.identity_key for item in members)
    if len(identities) != len(set(identities)):
        raise ValueError("frontier candidate identities must be unique")
    retained = tuple(
        candidate
        for candidate in members
        if not any(dominates(other, candidate) for other in members)
    )
    retained_keys = {item.identity_key for item in retained}
    edges = []
    for candidate in members:
        if candidate.identity_key in retained_keys:
            continue
        dominators = tuple(item for item in retained if dominates(item, candidate))
        if not dominators:
            raise ValueError("dominated scalar lacks a retained transitive dominator")
        selected = min(
            dominators,
            key=lambda item: (*item.vector, item.candidate_id),
        )
        edges.append(
            DominanceEdge(
                partition_key=candidate.partition_key,
                dominated_candidate_id=candidate.candidate_id,
                retained_candidate_id=selected.candidate_id,
            )
        )
    return ParetoFrontier(
        members=members,
        retained=retained,
        dominated_by=tuple(
            sorted(
                edges,
                key=lambda item: (
                    item.partition_key,
                    item.dominated_candidate_id,
                    item.retained_candidate_id,
                ),
            )
        ),
    )


def _require_rejection_query(
    *,
    remnant_area: float,
    remnant_width: float,
    remnant_height: float,
    area_tolerance: float,
    coordinate_tolerance: float,
) -> None:
    if any(
        type(value) not in (int, float) or not math.isfinite(value)
        for value in (
            remnant_area,
            remnant_width,
            remnant_height,
            area_tolerance,
            coordinate_tolerance,
        )
    ):
        raise ValueError("frontier rejection query measurements must be finite")
    if (
        remnant_area <= 0
        or remnant_width <= 0
        or remnant_height <= 0
        or area_tolerance < 0
        or coordinate_tolerance < 0
    ):
        raise ValueError("frontier rejection query measurements are outside bounds")


def certify_scalar_set_impossible(
    scalars: tuple[RejectionScalar, ...],
    *,
    material_matches: bool,
    remnant_area: float,
    remnant_width: float,
    remnant_height: float,
    area_tolerance: float,
    coordinate_tolerance: float,
) -> bool:
    """Apply only the frozen conservative necessary-fit inequalities."""

    if type(material_matches) is not bool:
        raise TypeError("frontier material match flag must be an exact boolean")
    _require_rejection_query(
        remnant_area=remnant_area,
        remnant_width=remnant_width,
        remnant_height=remnant_height,
        area_tolerance=area_tolerance,
        coordinate_tolerance=coordinate_tolerance,
    )
    if not scalars:
        return False
    return all(
        not material_matches
        or scalar.area > remnant_area + area_tolerance
        or scalar.width > remnant_width + coordinate_tolerance
        or scalar.height > remnant_height + coordinate_tolerance
        for scalar in scalars
    )


def certify_frontier_impossible(
    frontier: ParetoFrontier,
    **query: object,
) -> bool:
    """Fail closed on empty frontiers and otherwise test only retained scalars."""

    return certify_scalar_set_impossible(frontier.retained, **query)  # type: ignore[arg-type]


__all__ = [
    "DominanceEdge",
    "ParetoFrontier",
    "RejectionScalar",
    "build_pareto_frontier",
    "certify_frontier_impossible",
    "certify_scalar_set_impossible",
    "dominates",
]
