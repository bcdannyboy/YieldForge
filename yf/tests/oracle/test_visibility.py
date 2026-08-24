from __future__ import annotations

from tests.baseline.test_replay import _two_event_runtime


def test_m6_known_only_has_no_visible_suffix() -> None:
    from yieldforge.oracle.visibility import KnownOnlyVisibility

    stream = _two_event_runtime().replay_input.instances
    assert KnownOnlyVisibility(stream).visible_suffix(current_position=0) == ()


def test_hidden_suffix_does_not_change_known_only() -> None:
    from yieldforge.oracle.visibility import KnownOnlyVisibility

    stream = _two_event_runtime().replay_input.instances
    mutated = stream[:1] + tuple(reversed(stream[1:]))
    assert KnownOnlyVisibility(stream).visible_suffix(current_position=0) == (
        KnownOnlyVisibility(mutated).visible_suffix(current_position=0)
    )


def test_full_visibility_exposes_exact_post_current_suffix() -> None:
    from yieldforge.oracle.visibility import FullRealizedVisibility

    stream = _two_event_runtime().replay_input.instances
    assert FullRealizedVisibility(stream).visible_suffix(current_position=0) == stream[1:]
