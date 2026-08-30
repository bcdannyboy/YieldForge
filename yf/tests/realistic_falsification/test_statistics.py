from __future__ import annotations

import importlib
from collections import Counter
from decimal import Decimal

import numpy as np
import pytest
from pydantic import ValidationError


def _statistics():
    try:
        return importlib.import_module("yieldforge.realistic_falsification.statistics")
    except ModuleNotFoundError as error:
        pytest.fail(f"Task 5 statistics module is missing: {error}")


def _pair(savings: str, unknown: str):
    return _statistics().Gate1CellMetricPair(
        savings_percent=Decimal(savings),
        unknown_contribution_points=Decimal(unknown),
    )


def test_cell_metrics_use_decimal_inputs_without_percent_quantization() -> None:
    statistics = _statistics()

    result = statistics.calculate_gate1_cell_metrics(
        baseline=Decimal("3"),
        known_only=Decimal("2"),
        lower_bound=Decimal("1"),
    )

    assert result.savings_percent == Decimal("66.666666666666666666666666666666666666666666666667")
    assert result.unknown_contribution_points == Decimal(
        "33.333333333333333333333333333333333333333333333333"
    )
    assert result.savings_percent.as_tuple().exponent < -6


@pytest.mark.parametrize(
    ("baseline", "known_only", "lower_bound"),
    [
        (Decimal("0"), Decimal("1"), Decimal("0")),
        (Decimal("NaN"), Decimal("1"), Decimal("0")),
        (Decimal("1"), Decimal("Infinity"), Decimal("0")),
        (Decimal("1"), Decimal("1"), Decimal("2")),
    ],
)
def test_cell_metrics_fail_closed_on_nonfinite_or_invalid_bounds(
    baseline: Decimal, known_only: Decimal, lower_bound: Decimal
) -> None:
    with pytest.raises(ValueError):
        _statistics().calculate_gate1_cell_metrics(
            baseline=baseline,
            known_only=known_only,
            lower_bound=lower_bound,
        )


def test_equal_corpus_pool_never_raw_concatenates_unequal_corpora() -> None:
    statistics = _statistics()

    assert statistics.equal_corpus_mean(
        (Decimal("0"), Decimal("10")),
        (Decimal("100"),),
    ) == Decimal("52.5")


def test_type_7_linear_quantiles_are_frozen() -> None:
    statistics = _statistics()

    assert statistics.linear_quantile((0.0, 1.0), 0.5) == 0.5
    assert statistics.linear_quantile(tuple(float(value) for value in range(40)), 0.5) == 19.5
    assert statistics.linear_quantile(tuple(float(value) for value in range(10_000)), 0.975) == (
        9749.025
    )


def test_q975_not_q95_controls_the_joint_upper_bound() -> None:
    statistics = _statistics()
    catcher = (-1.0,) * 9_600 + (1.0,) * 400

    assert statistics.linear_quantile(catcher, 0.95) < 0
    assert statistics.linear_quantile(catcher, 0.975) > 0
    assert (
        statistics.optimistic_ceiling_falsified(statistics.linear_quantile(catcher, 0.975)) is False
    )


def test_pcg64_seed_draws_complete_lectra_matrix_before_loco() -> None:
    statistics = _statistics()

    lectra, loco = statistics._draw_gate1_bootstrap_indices(2, 2)

    assert lectra[:5].tolist() == [[1, 1], [1, 0], [0, 0], [0, 0], [0, 1]]
    assert loco[:5].tolist() == [[0, 1], [0, 1], [0, 1], [0, 1], [1, 1]]


def test_paired_bootstrap_joint_margin_vector_is_exact() -> None:
    statistics = _statistics()
    lectra = (_pair("0", "0"), _pair("4", "2"))
    loco = (_pair("1", "0"), _pair("3", "4"))

    arrays = statistics._bootstrap_gate1_arrays(lectra, loco)

    assert Counter(arrays.joint_adverse_margin.tolist()) == {
        -0.5: 635,
        0.5: 5_065,
        1.5: 3_692,
        2.0: 608,
    }
    assert statistics.linear_quantile(arrays.joint_adverse_margin, 0.975) == 2.0


def test_s_and_u_are_resampled_as_one_paired_stream_vector() -> None:
    statistics = _statistics()
    lectra = (_pair("0", "0"), _pair("4", "2"))
    loco = (_pair("1", "0"), _pair("3", "4"))
    arrays = statistics._bootstrap_gate1_arrays(lectra, loco)
    lectra_indices, loco_indices = statistics._draw_gate1_bootstrap_indices(2, 2)
    lectra_matrix = np.asarray([[0.0, 0.0], [4.0, 2.0]])
    loco_matrix = np.asarray([[1.0, 0.0], [3.0, 4.0]])

    expected_lectra = lectra_matrix[lectra_indices].mean(axis=1)
    expected_loco = loco_matrix[loco_indices].mean(axis=1)

    assert np.array_equal(arrays.lectra_group_means, expected_lectra)
    assert np.array_equal(arrays.loco_group_means, expected_loco)
    assert not np.array_equal(
        arrays.lectra_group_means[:, 1],
        lectra_matrix[loco_indices].mean(axis=1)[:, 1],
    )


def test_constant_low_falsifies_but_exact_zero_boundary_survives() -> None:
    statistics = _statistics()

    low = statistics._bootstrap_gate1_statistics(
        (_pair("0", "0"),) * 20,
        (_pair("0", "0"),) * 20,
    )
    boundary = statistics._bootstrap_gate1_statistics(
        (_pair("1.5", "0.5"),) * 20,
        (_pair("1.5", "0.5"),) * 20,
    )

    assert low.joint_upper_adverse_margin == -1.5
    assert low.falsifies_optimistic_ceiling is True
    assert boundary.joint_upper_adverse_margin == 0.0
    assert boundary.falsifies_optimistic_ceiling is False


def test_crossed_corpus_failures_survive_through_equal_corpus_pool() -> None:
    statistics = _statistics()

    result = statistics._bootstrap_gate1_statistics(
        (_pair("3", "0"),) * 20,
        (_pair("0", "2"),) * 20,
    )
    groups = {item.group: item for item in result.groups}

    assert groups["lectra-m3-m4"].adverse_margin == -0.5
    assert groups["loco-2dics"].adverse_margin == -1.5
    assert groups["equal-corpus-pool"].adverse_margin == 0.0
    assert result.joint_upper_adverse_margin == 0.0
    assert result.falsifies_optimistic_ceiling is False


@pytest.mark.parametrize(
    ("successes", "expected"),
    [
        (0, (0.0, 16.112515805281937)),
        (1, (0.8881448800795402, 23.613119344674203)),
        (10, (29.929800819821228, 70.07019918017878)),
        (19, (76.3868806553258, 99.11185511992046)),
        (20, (83.88748419471807, 100.0)),
    ],
)
def test_wilson_interval_n20_frozen_values(successes: int, expected: tuple[float, float]) -> None:
    assert _statistics().wilson_interval_percent(successes, 20) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("successes", "total"),
    [(True, 20), (1, True), (-1, 20), (21, 20), (0, 0)],
)
def test_wilson_interval_rejects_non_strict_or_invalid_counts(successes, total) -> None:
    with pytest.raises((TypeError, ValueError)):
        _statistics().wilson_interval_percent(successes, total)


def test_bootstrap_summary_is_strict_frozen_and_content_addressed() -> None:
    statistics = _statistics()
    result = statistics._bootstrap_gate1_statistics(
        (_pair("1", "1"),) * 20,
        (_pair("1", "1"),) * 20,
    )
    payload = result.model_dump(mode="python", round_trip=True)
    payload["joint_upper_adverse_margin"] += 1.0

    with pytest.raises(ValidationError, match="identity|joint|falsification"):
        statistics.Gate1BootstrapSummary.model_validate(payload, strict=True)
    with pytest.raises(ValidationError):
        statistics.Gate1BootstrapSummary.model_validate(
            {**result.model_dump(mode="python"), "extra": True}, strict=True
        )
    with pytest.raises(ValidationError):
        result.joint_upper_adverse_margin = 0.0


@pytest.mark.parametrize(
    ("lectra_size", "loco_size"),
    [(1, 1), (19, 20), (20, 19), (21, 20), (20, 21)],
)
def test_registered_bootstrap_requires_exact_twenty_by_twenty_census_before_rng(
    lectra_size: int,
    loco_size: int,
    monkeypatch,
) -> None:
    statistics = _statistics()
    draw_count = 0

    def forbidden_draw(*_args):
        nonlocal draw_count
        draw_count += 1
        raise AssertionError("RNG must not run for an invalid census")

    monkeypatch.setattr(statistics, "_draw_gate1_bootstrap_indices", forbidden_draw)

    with pytest.raises(ValueError, match="20|census"):
        statistics._bootstrap_gate1_statistics(
            (_pair("0", "0"),) * lectra_size,
            (_pair("0", "0"),) * loco_size,
        )

    assert draw_count == 0


@pytest.mark.parametrize(("lectra_size", "loco_size"), [(21, 20), (23, 24)])
def test_private_bootstrap_allocator_has_a_hard_twenty_stream_cap(
    lectra_size: int,
    loco_size: int,
) -> None:
    with pytest.raises(ValueError, match="20|cap|bounded"):
        _statistics()._draw_gate1_bootstrap_indices(lectra_size, loco_size)


@pytest.mark.parametrize(
    ("savings", "unknown"),
    [("-0.1", "0"), ("0", "-0.1"), ("-1e-10000", "0")],
)
def test_negative_metric_rejects_before_any_rng_draw(
    savings: str,
    unknown: str,
    monkeypatch,
) -> None:
    statistics = _statistics()
    draw_count = 0

    def forbidden_draw(*_args):
        nonlocal draw_count
        draw_count += 1
        raise AssertionError("RNG must not run for invalid metrics")

    monkeypatch.setattr(statistics, "_draw_gate1_bootstrap_indices", forbidden_draw)
    valid = (_pair("0", "0"),) * 20
    invalid = (_pair(savings, unknown),) + valid[1:]

    with pytest.raises(ValueError, match="nonnegative|metric"):
        statistics._bootstrap_gate1_statistics(invalid, valid)

    assert draw_count == 0
