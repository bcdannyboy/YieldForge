from __future__ import annotations

import random
from itertools import combinations

import pytest

from yieldforge.realistic_falsification.matching import (
    MAX_EDGE_REWARD_MICRO_UNITS,
    MatchingEdge,
    maximum_reward_matching,
)


def _edge(edge_id: str, origin_id: str, target_id: str, reward: int) -> MatchingEdge:
    return MatchingEdge(
        edge_id=edge_id,
        origin_id=origin_id,
        target_id=target_id,
        reward_micro_units=reward,
    )


def test_empty_population_and_edgeless_population_have_zero_reward() -> None:
    assert maximum_reward_matching(origin_ids=(), target_ids=(), edges=()).selected_edge_ids == ()

    result = maximum_reward_matching(
        origin_ids=("origin-a", "origin-b"),
        target_ids=("target-a",),
        edges=(),
    )

    assert result.selected_edge_ids == ()
    assert result.total_reward_micro_units == 0


def test_matching_selects_at_most_one_edge_per_origin_and_target() -> None:
    edges = (
        _edge("edge-a", "origin-a", "target-a", 9),
        _edge("edge-b", "origin-a", "target-b", 8),
        _edge("edge-c", "origin-b", "target-a", 7),
        _edge("edge-d", "origin-b", "target-b", 1),
    )

    result = maximum_reward_matching(
        origin_ids=("origin-a", "origin-b"),
        target_ids=("target-a", "target-b"),
        edges=edges,
    )

    assert result.selected_edge_ids == ("edge-b", "edge-c")
    assert result.total_reward_micro_units == 15


def test_global_optimum_beats_greedy_highest_edge() -> None:
    result = maximum_reward_matching(
        origin_ids=("origin-a", "origin-b"),
        target_ids=("target-a", "target-b"),
        edges=(
            _edge("greedy", "origin-a", "target-a", 10),
            _edge("complement-left", "origin-a", "target-b", 9),
            _edge("complement-right", "origin-b", "target-a", 9),
            _edge("weak", "origin-b", "target-b", 1),
        ),
    )

    assert result.selected_edge_ids == ("complement-left", "complement-right")
    assert result.total_reward_micro_units == 18


def test_nonpositive_edges_are_always_left_unmatched() -> None:
    result = maximum_reward_matching(
        origin_ids=("origin-a", "origin-b", "origin-c"),
        target_ids=("target-a", "target-b", "target-c"),
        edges=(
            _edge("positive", "origin-a", "target-a", 1),
            _edge("zero", "origin-b", "target-b", 0),
            _edge("negative", "origin-c", "target-c", -1),
        ),
    )

    assert result.selected_edge_ids == ("positive",)
    assert result.total_reward_micro_units == 1


def test_exact_reward_ties_choose_lexicographically_smallest_edge_set() -> None:
    edges = [
        _edge("edge-a", "origin-a", "target-a", 5),
        _edge("edge-b", "origin-a", "target-b", 5),
        _edge("edge-c", "origin-b", "target-a", 5),
        _edge("edge-d", "origin-b", "target-b", 5),
    ]

    expected = ("edge-a", "edge-d")
    for seed in range(20):
        shuffled = list(edges)
        random.Random(seed).shuffle(shuffled)
        result = maximum_reward_matching(
            origin_ids=tuple(reversed(("origin-a", "origin-b")))
            if seed % 2
            else (
                "origin-a",
                "origin-b",
            ),
            target_ids=tuple(reversed(("target-a", "target-b")))
            if seed % 3
            else (
                "target-a",
                "target-b",
            ),
            edges=shuffled,
        )
        assert result.selected_edge_ids == expected
        assert result.total_reward_micro_units == 10


def test_small_random_graphs_match_exhaustive_exact_oracle() -> None:
    generator = random.Random(7)
    origins = tuple(f"origin-{index}" for index in range(3))
    targets = tuple(f"target-{index}" for index in range(3))

    for graph_ordinal in range(50):
        edges = tuple(
            _edge(
                f"edge-{origin_index}-{target_index}",
                origin_id,
                target_id,
                generator.randint(-1, 5),
            )
            for origin_index, origin_id in enumerate(origins)
            for target_index, target_id in enumerate(targets)
            if generator.random() < 0.8
        )
        best_reward = 0
        best_ids: tuple[str, ...] = ()
        for edge_count in range(len(edges) + 1):
            for candidate in combinations(edges, edge_count):
                if any(edge.reward_micro_units <= 0 for edge in candidate):
                    continue
                if len({edge.origin_id for edge in candidate}) != len(candidate):
                    continue
                if len({edge.target_id for edge in candidate}) != len(candidate):
                    continue
                reward = sum(edge.reward_micro_units for edge in candidate)
                edge_ids = tuple(sorted(edge.edge_id for edge in candidate))
                if reward > best_reward or (reward == best_reward and edge_ids < best_ids):
                    best_reward = reward
                    best_ids = edge_ids

        shuffled = list(edges)
        generator.shuffle(shuffled)
        result = maximum_reward_matching(
            origin_ids=tuple(reversed(origins)) if graph_ordinal % 2 else origins,
            target_ids=tuple(reversed(targets)) if graph_ordinal % 3 else targets,
            edges=shuffled,
        )

        assert result.selected_edge_ids == best_ids
        assert result.total_reward_micro_units == best_reward


@pytest.mark.parametrize(
    ("origin_ids", "target_ids", "edges", "match"),
    [
        (("origin-a", "origin-a"), ("target-a",), (), "duplicate origin"),
        (("origin-a",), ("target-a", "target-a"), (), "duplicate target"),
        (
            ("origin-a",),
            ("target-a",),
            (
                _edge("edge-a", "origin-a", "target-a", 1),
                _edge("edge-a", "origin-a", "target-a", 2),
            ),
            "duplicate edge",
        ),
        (
            ("origin-a",),
            ("target-a",),
            (_edge("edge-a", "missing", "target-a", 1),),
            "unknown origin",
        ),
        (
            ("origin-a",),
            ("target-a",),
            (_edge("edge-a", "origin-a", "missing", 1),),
            "unknown target",
        ),
    ],
)
def test_duplicate_or_dangling_identifiers_reject(
    origin_ids, target_ids, edges, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        maximum_reward_matching(origin_ids=origin_ids, target_ids=target_ids, edges=edges)


@pytest.mark.parametrize("identifier", ["", " edge", "edge ", 1, None])
def test_malformed_identifiers_reject(identifier) -> None:
    with pytest.raises((TypeError, ValueError)):
        MatchingEdge(
            edge_id=identifier,
            origin_id="origin-a",
            target_id="target-a",
            reward_micro_units=1,
        )


@pytest.mark.parametrize(
    "reward",
    [
        True,
        False,
        1.0,
        "1",
        None,
        MAX_EDGE_REWARD_MICRO_UNITS + 1,
        -MAX_EDGE_REWARD_MICRO_UNITS - 1,
    ],
)
def test_weights_require_bounded_exact_integer_micro_units(reward) -> None:
    with pytest.raises((TypeError, ValueError)):
        MatchingEdge(
            edge_id="edge-a",
            origin_id="origin-a",
            target_id="target-a",
            reward_micro_units=reward,
        )
