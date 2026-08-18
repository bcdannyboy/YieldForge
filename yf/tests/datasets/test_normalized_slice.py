import copy
import json
import subprocess
import sys

import pytest
from pydantic import TypeAdapter, ValidationError

from yieldforge.datasets.normalized_slice import (
    CONSTRAINT_OPAQUE_FIELD_ORDER,
    ConstraintSourceRow,
    DerivedShapeGeometry,
    NormalizationStatus,
    NormalizedSlice,
    NormalizedSliceSource,
    OpaqueBoolean,
    OpaqueInteger,
    OpaqueMissing,
    OpaqueNumber,
    OpaqueSequence,
    OpaqueString,
    OpaqueValue,
    PartSourceRow,
    ProjectionStatus,
    ProvenanceGroup,
    ProvenanceKind,
    ShapeSourceRow,
    SourceChecksum,
    SourceUnit,
    SupportStatus,
    TaskDisposition,
    TaskSourceRow,
)


def source_identity() -> NormalizedSliceSource:
    return NormalizedSliceSource(
        dataset_id="lectra-7030786-v1.1",
        source_checksums=(
            SourceChecksum(name="parts.gz", checksum="1" * 32),
            SourceChecksum(name="constraints.gz", checksum="2" * 32),
            SourceChecksum(name="shapes.gz", checksum="3" * 32),
            SourceChecksum(name="tasks.gz", checksum="4" * 32),
        ),
        source_manifest_sha256="a" * 64,
        audit_report_sha256="b" * 64,
        doi="10.5281/zenodo.7030786",
        license="CC-BY-4.0",
        source_unit=SourceUnit(literal_label="m^-4", interpretation=None),
        conversion_ruleset_version="lectra-slice-rules.v1",
    )


def opaque_constraint_values() -> tuple[OpaqueValue, ...]:
    missing = OpaqueMissing(kind="missing")
    return (
        OpaqueSequence(kind="sequence", items=(OpaqueInteger(kind="integer", value=7),)),
        missing,
        missing,
        OpaqueSequence(kind="sequence", items=(OpaqueNumber(kind="number", value=0.0),)),
        OpaqueSequence(kind="sequence", items=(OpaqueNumber(kind="number", value=1.0),)),
        OpaqueSequence(kind="sequence", items=(OpaqueBoolean(kind="boolean", value=False),)),
        missing,
        missing,
        missing,
        missing,
        missing,
        missing,
        missing,
        missing,
        missing,
        missing,
        missing,
        missing,
        missing,
        missing,
    )


def valid_slice() -> NormalizedSlice:
    return NormalizedSlice(
        schema_version="yieldforge.normalized-slice.v1",
        source=source_identity(),
        tasks=(
            TaskSourceRow(
                source_row_index=10,
                duration=304,
                efficiency=81.25,
                sheet_width=14500.0,
                sheet_length=20000.0,
                sheet_type=0,
                tasks_index=17,
                is_train=True,
                is_val=False,
                is_test=False,
            ),
        ),
        parts=(
            PartSourceRow(
                source_row_index=20,
                tasks_index=17,
                part_id=7,
                shape_hash=101,
            ),
        ),
        shapes=(
            ShapeSourceRow(
                source_row_index=30,
                shape_hash=101,
                raw=(0, 0.0, 2, 0.0, 0, 3.0),
                sizes=(6,),
            ),
        ),
        constraints=(
            ConstraintSourceRow(
                source_row_index=40,
                tasks_index=17,
                type="s1",
                values=opaque_constraint_values(),
            ),
        ),
        constraint_value_columns=CONSTRAINT_OPAQUE_FIELD_ORDER,
        derived_geometry=(
            DerivedShapeGeometry(
                shape_hash=101,
                paired_points=((0, 0.0), (2, 0.0), (0, 3.0)),
                closed_ring=((0, 0.0), (2, 0.0), (0, 3.0), (0, 0.0)),
                raw_scalar_count=6,
                ring_closure_added=True,
                is_simple=True,
                is_valid=True,
                has_nonzero_area=True,
                area=3.0,
                bounds=(0, 0.0, 2, 3.0),
            ),
        ),
        task_dispositions=(
            TaskDisposition(
                tasks_index=17,
                normalization_status=NormalizationStatus.SOURCE_LOSSLESS,
                support_status=SupportStatus.RUNNABLE_WITH_EXPLICIT_ASSUMPTIONS,
                projection_status=ProjectionStatus.ELIGIBLE,
                reason_codes=("opaque_s1_only", "positive_physical_sheet"),
                assumption_codes=("assume_free_rotation", "ignore_opaque_s1"),
            ),
        ),
        provenance=(
            ProvenanceGroup(
                kind=ProvenanceKind.SOURCE_REAL,
                field_paths=("constraints", "parts", "shapes", "tasks"),
                note="Verbatim selected source rows.",
            ),
            ProvenanceGroup(
                kind=ProvenanceKind.DERIVED,
                field_paths=("derived_geometry",),
                note="Reversible adjacent-scalar pairing and ring closure.",
            ),
            ProvenanceGroup(
                kind=ProvenanceKind.ASSUMED,
                field_paths=("task_dispositions.assumption_codes",),
                note="Declared projection assumptions only.",
            ),
        ),
    )


def test_contract_is_frozen_strict_and_has_exact_schema_version() -> None:
    normalized = valid_slice()

    with pytest.raises(ValidationError, match="frozen"):
        normalized.schema_version = "other"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        NormalizedSlice.model_validate(
            {**normalized.model_dump(mode="json"), "schema_version": "other"}
        )
    with pytest.raises(ValidationError):
        TaskSourceRow.model_validate({**normalized.tasks[0].model_dump(), "source_row_index": "10"})
    with pytest.raises(ValidationError, match="extra"):
        NormalizedSlice.model_validate({**normalized.model_dump(), "surprise": True})


def test_raw_sizes_and_numeric_kinds_round_trip_exactly() -> None:
    normalized = valid_slice()
    payload = normalized.model_dump_json().encode()

    restored = NormalizedSlice.model_validate_json(payload)

    assert restored.shapes[0].raw == (0, 0.0, 2, 0.0, 0, 3.0)
    assert [type(value) for value in restored.shapes[0].raw] == [
        int,
        float,
        int,
        float,
        int,
        float,
    ]
    assert restored.shapes[0].sizes == (6,)
    assert restored.model_dump_json() == normalized.model_dump_json()


def test_opaque_values_preserve_missing_boolean_integer_number_string_and_sequence() -> None:
    values: tuple[OpaqueValue, ...] = (
        OpaqueMissing(kind="missing"),
        OpaqueBoolean(kind="boolean", value=False),
        OpaqueInteger(kind="integer", value=0),
        OpaqueNumber(kind="number", value=0.0),
        OpaqueString(kind="string", value="0"),
        OpaqueSequence(
            kind="sequence",
            items=(
                OpaqueMissing(kind="missing"),
                OpaqueBoolean(kind="boolean", value=True),
                OpaqueInteger(kind="integer", value=1),
                OpaqueNumber(kind="number", value=1.0),
                OpaqueString(kind="string", value="1"),
            ),
        ),
    )

    adapter = TypeAdapter(OpaqueValue)
    restored = tuple(adapter.validate_json(value.model_dump_json()) for value in values)

    # The contract's discriminators, not Python truthiness, retain every source kind.
    assert [value.kind for value in restored] == [
        "missing",
        "boolean",
        "integer",
        "number",
        "string",
        "sequence",
    ]
    assert [item.kind for item in values[-1].items] == [  # type: ignore[union-attr]
        "missing",
        "boolean",
        "integer",
        "number",
        "string",
    ]
    with pytest.raises(ValidationError):
        OpaqueInteger(kind="integer", value=True)
    with pytest.raises(ValidationError, match="source float"):
        OpaqueNumber(kind="number", value=1)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_contract_rejects_nonfinite_source_and_derived_numbers(bad: float) -> None:
    with pytest.raises(ValidationError):
        ShapeSourceRow(source_row_index=0, shape_hash=1, raw=(0, bad), sizes=(2,))
    with pytest.raises(ValidationError):
        OpaqueNumber(kind="number", value=bad)
    with pytest.raises(ValidationError):
        DerivedShapeGeometry(
            shape_hash=1,
            paired_points=((0, 0), (1, 0), (0, 1)),
            closed_ring=((0, 0), (1, 0), (0, 1), (0, 0)),
            raw_scalar_count=6,
            ring_closure_added=True,
            is_simple=True,
            is_valid=True,
            has_nonzero_area=True,
            area=bad,
            bounds=(0, 0, 1, 1),
        )


def test_source_rows_remain_in_source_order_with_explicit_indexes() -> None:
    data = valid_slice().model_dump()
    first = data["parts"][0]
    second = copy.deepcopy(first)
    second["source_row_index"] = 19
    second["part_id"] = 8
    data["parts"] = (first, second)

    with pytest.raises(ValidationError, match="source_row_index.*increasing"):
        NormalizedSlice.model_validate(data)

    data = valid_slice().model_dump()
    data["parts"][0].pop("source_row_index")
    with pytest.raises(ValidationError, match="source_row_index"):
        NormalizedSlice.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_manifest_sha256", "A" * 64),
        ("source_manifest_sha256", "a" * 63),
        ("audit_report_sha256", "not-a-hash"),
    ],
)
def test_source_identity_rejects_invalid_sha256(field: str, value: str) -> None:
    data = source_identity().model_dump()
    data[field] = value

    with pytest.raises(ValidationError, match=field):
        NormalizedSliceSource.model_validate(data)


def test_source_identity_pins_release_unit_and_checksum_inventory() -> None:
    data = source_identity().model_dump()
    data["dataset_id"] = "lectra-other"
    with pytest.raises(ValidationError):
        NormalizedSliceSource.model_validate(data)

    data = source_identity().model_dump()
    data["source_unit"]["interpretation"] = "millimetres"
    with pytest.raises(ValidationError):
        NormalizedSliceSource.model_validate(data)

    data = source_identity().model_dump()
    data["source_checksums"] = tuple(reversed(data["source_checksums"]))
    with pytest.raises(ValidationError, match="source checksum inventory"):
        NormalizedSliceSource.model_validate(data)


@pytest.mark.parametrize("field", ["reason_codes", "assumption_codes"])
def test_reason_and_assumption_codes_must_be_sorted_and_unique(field: str) -> None:
    data = valid_slice().task_dispositions[0].model_dump()
    data[field] = ("z_code", "a_code", "a_code")

    with pytest.raises(ValidationError, match=f"{field}.*sorted and unique"):
        TaskDisposition.model_validate(data)


def test_status_families_are_separate_and_enforce_explanations() -> None:
    disposition = valid_slice().task_dispositions[0]
    assert disposition.normalization_status is NormalizationStatus.SOURCE_LOSSLESS
    assert disposition.support_status is SupportStatus.RUNNABLE_WITH_EXPLICIT_ASSUMPTIONS
    assert disposition.projection_status is ProjectionStatus.ELIGIBLE

    data = disposition.model_dump()
    data["assumption_codes"] = ()
    with pytest.raises(ValidationError, match="explicit assumptions"):
        TaskDisposition.model_validate(data)

    data = disposition.model_dump()
    data["projection_status"] = ProjectionStatus.BLOCKED
    data["reason_codes"] = ()
    with pytest.raises(ValidationError, match="blocked.*reason"):
        TaskDisposition.model_validate(data)


def test_constraint_requires_exact_observed_opaque_field_order() -> None:
    data = valid_slice().constraints[0].model_dump()
    data["values"] = tuple(reversed(data["values"]))

    # Values are positional by the documented constant; count protects against silent loss.
    assert len(valid_slice().constraints[0].values) == 20
    assert ConstraintSourceRow.model_validate(data).values != valid_slice().constraints[0].values

    data["values"] = data["values"][:-1]
    with pytest.raises(ValidationError, match="exactly 20"):
        ConstraintSourceRow.model_validate(data)

    slice_data = valid_slice().model_dump()
    slice_data["constraint_value_columns"] = tuple(reversed(slice_data["constraint_value_columns"]))
    with pytest.raises(ValidationError, match="exact observed source order"):
        NormalizedSlice.model_validate(slice_data)


def test_slice_validates_source_references_geometry_and_dispositions() -> None:
    data = valid_slice().model_dump()
    data["parts"][0]["shape_hash"] = 999
    with pytest.raises(ValidationError, match="unresolved shape_hash"):
        NormalizedSlice.model_validate(data)

    data = valid_slice().model_dump()
    points = list(data["derived_geometry"][0]["paired_points"])
    points[0] = (9, 9)
    data["derived_geometry"][0]["paired_points"] = tuple(points)
    data["derived_geometry"][0]["closed_ring"] = tuple(points) + (points[0],)
    data["derived_geometry"][0]["bounds"] = (0, 0.0, 9, 9)
    with pytest.raises(ValidationError, match="adjacent raw scalars"):
        NormalizedSlice.model_validate(data)

    data = valid_slice().model_dump()
    data["task_dispositions"] = ()
    with pytest.raises(ValidationError, match="exactly one disposition"):
        NormalizedSlice.model_validate(data)


def test_normalized_contract_imports_without_pandas_or_pickle() -> None:
    code = """
import sys
import yieldforge.datasets.normalized_slice
assert "pandas" not in sys.modules
assert "pickle" not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_json_contract_is_stable_and_duplicate_free() -> None:
    serialized = valid_slice().model_dump_json()
    decoded = json.loads(serialized)

    assert decoded["schema_version"] == "yieldforge.normalized-slice.v1"
    assert len(decoded["source"]["source_checksums"]) == 4
