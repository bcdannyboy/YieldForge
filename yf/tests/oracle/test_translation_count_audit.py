from __future__ import annotations

import pytest
from shapely import box

from tests.oracle.fixtures import inventory_item, two_problem_runtime
from yieldforge.baseline.contracts import LayoutFitSearchConfig
from yieldforge.baseline.geometry import (
    generate_layout_translations,
    prepare_layout_footprint,
    prepare_remnant_geometry,
)
from yieldforge.oracle.translation_count_audit import (
    audit_layout_translation_batch,
    audit_layout_translation_counts,
)


@pytest.mark.parametrize(
    "search_config",
    (
        LayoutFitSearchConfig(grid_columns=2, grid_rows=2, maximum_candidates=1),
        LayoutFitSearchConfig(grid_columns=3, grid_rows=4, maximum_candidates=5),
        LayoutFitSearchConfig(grid_columns=8, grid_rows=7, maximum_candidates=10_000),
    ),
)
def test_vectorized_count_audit_matches_registered_generator(
    search_config: LayoutFitSearchConfig,
) -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    problem = next(
        item
        for item in runtime.replay_input.problems
        if item.problem_id == binding.problem_id
    )
    verified = runtime.runtime_candidates[binding.problem_id]
    item = inventory_item(
        box(0.0, 0.0, 10.0, 10.0),
        material=binding.material,
        token=f"count-audit-{search_config.maximum_candidates}",
    )
    prepared_remnant = prepare_remnant_geometry(item.remnant)

    for candidate in verified.candidates:
        layout = prepare_layout_footprint(
            problem.problem,
            candidate,
            runtime.replay_input.fit_config,
        )
        exact = generate_layout_translations(
            item.remnant,
            candidate,
            fit_config=runtime.replay_input.fit_config,
            search_config=search_config,
            prepared_layout=layout,
            prepared_remnant=prepared_remnant,
        )
        audited = audit_layout_translation_counts(
            remnant=prepared_remnant,
            layout=layout,
            expected=exact,
            fit_config=runtime.replay_input.fit_config,
            search_config=search_config,
        )

        assert audited.generated_candidate_count == exact.generated_candidate_count
        assert audited.duplicate_candidate_count == exact.duplicate_candidate_count
        assert audited.evaluated_candidate_count == len(exact.translations)
        assert audited.budget_truncated == exact.budget_truncated


def test_vectorized_count_audit_matches_impossible_bounds() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    problem = next(
        item
        for item in runtime.replay_input.problems
        if item.problem_id == binding.problem_id
    )
    candidate = runtime.runtime_candidates[binding.problem_id].candidates[0]
    item = inventory_item(
        box(0.0, 0.0, 1.0, 1.0),
        material=binding.material,
        token="count-audit-impossible-bounds",
    )
    prepared_remnant = prepare_remnant_geometry(item.remnant)
    layout = prepare_layout_footprint(
        problem.problem,
        candidate,
        runtime.replay_input.fit_config,
    )
    exact = generate_layout_translations(
        item.remnant,
        candidate,
        fit_config=runtime.replay_input.fit_config,
        search_config=runtime.replay_input.search_config,
        prepared_layout=layout,
        prepared_remnant=prepared_remnant,
    )
    audited = audit_layout_translation_counts(
        remnant=prepared_remnant,
        layout=layout,
        expected=exact,
        fit_config=runtime.replay_input.fit_config,
        search_config=runtime.replay_input.search_config,
    )

    assert exact.translations == ()
    assert audited.generated_candidate_count == exact.generated_candidate_count == 0
    assert audited.duplicate_candidate_count == exact.duplicate_candidate_count == 0
    assert audited.evaluated_candidate_count == 0
    assert not audited.budget_truncated


def test_count_audit_batch_matches_registered_generator_in_forked_workers() -> None:
    runtime = two_problem_runtime(first_width=4.0, second_width=4.0)
    binding = runtime.replay_input.instances[1]
    problem = next(
        item
        for item in runtime.replay_input.problems
        if item.problem_id == binding.problem_id
    )
    candidate = runtime.runtime_candidates[binding.problem_id].candidates[0]
    item = inventory_item(
        box(0.0, 0.0, 10.0, 10.0),
        material=binding.material,
        token="count-audit-forked-batch",
    )
    prepared_remnant = prepare_remnant_geometry(item.remnant)
    layout = prepare_layout_footprint(
        problem.problem,
        candidate,
        runtime.replay_input.fit_config,
    )
    exact = generate_layout_translations(
        item.remnant,
        candidate,
        fit_config=runtime.replay_input.fit_config,
        search_config=runtime.replay_input.search_config,
        prepared_layout=layout,
        prepared_remnant=prepared_remnant,
    )

    audited = audit_layout_translation_batch(
        remnant=prepared_remnant,
        layouts=(layout,) * 32,
        expected=(exact,) * 32,
        fit_config=runtime.replay_input.fit_config,
        search_config=runtime.replay_input.search_config,
        process_count=2,
    )

    assert len(audited) == 32
    assert all(
        result.generated_candidate_count == exact.generated_candidate_count
        for result in audited
    )
    assert all(
        result.duplicate_candidate_count == exact.duplicate_candidate_count
        for result in audited
    )
    assert all(result.evaluated_candidate_count == len(exact.translations) for result in audited)
    assert all(result.budget_truncated == exact.budget_truncated for result in audited)
