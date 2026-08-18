import copy
import json
import subprocess
import sys

import pytest
from pydantic import TypeAdapter, ValidationError

from yieldforge.datasets.normalized_slice import (
    ASSUMED_PROVENANCE_PATH,
    CONSTRAINT_OPAQUE_FIELD_ORDER,
    DERIVED_PROVENANCE_PATHS,
    SOURCE_REAL_PROVENANCE_PATHS,
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
                field_paths=SOURCE_REAL_PROVENANCE_PATHS,
                note="Verbatim selected source rows.",
            ),
            ProvenanceGroup(
                kind=ProvenanceKind.DERIVED,
                field_paths=DERIVED_PROVENANCE_PATHS,
                note="Reversible adjacent-scalar pairing and ring closure.",
            ),
            ProvenanceGroup(
                kind=ProvenanceKind.ASSUMED,
                field_paths=(ASSUMED_PROVENANCE_PATH,),
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


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("duration", 304.0),
        ("efficiency", 81),
        ("sheet_width", 14500),
        ("sheet_length", 20000),
    ],
)
def test_task_source_row_preserves_exact_audited_scalar_dtypes(
    field: str, bad_value: int | float
) -> None:
    data = valid_slice().tasks[0].model_dump()
    data[field] = bad_value

    with pytest.raises(ValidationError, match=field):
        TaskSourceRow.model_validate(data)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_task_source_floats_must_be_finite(bad: float) -> None:
    data = valid_slice().tasks[0].model_dump()
    data["efficiency"] = bad

    with pytest.raises(ValidationError, match="efficiency"):
        TaskSourceRow.model_validate(data)


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


def test_shape_sizes_must_match_the_exact_bound_v11_encoding() -> None:
    data = valid_slice().shapes[0].model_dump()
    data["sizes"] = (3, 3)

    with pytest.raises(ValidationError, match=r"sizes.*\(len\(raw\),\)"):
        ShapeSourceRow.model_validate(data)


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

    data = source_identity().model_dump()
    data["conversion_ruleset_version"] = "arbitrary-rules"
    with pytest.raises(ValidationError, match="conversion_ruleset_version"):
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


@pytest.mark.parametrize(
    ("column_index", "bad_value", "message"),
    [
        (0, OpaqueInteger(kind="integer", value=7), "must be missing or a sequence"),
        (
            0,
            OpaqueSequence(kind="sequence", items=(OpaqueNumber(kind="number", value=7.0),)),
            "integral elements",
        ),
        (
            6,
            OpaqueSequence(kind="sequence", items=(OpaqueMissing(kind="missing"),)),
            "integral elements",
        ),
    ],
)
def test_constraint_part_reference_cells_reject_malformed_typed_values(
    column_index: int, bad_value: OpaqueValue, message: str
) -> None:
    data = valid_slice().model_dump()
    values = list(data["constraints"][0]["values"])
    values[column_index] = bad_value.model_dump()
    data["constraints"][0]["values"] = tuple(values)

    with pytest.raises(ValidationError, match=message):
        NormalizedSlice.model_validate(data)


def test_constraint_part_references_must_resolve_within_the_constraint_task() -> None:
    data = valid_slice().model_dump()
    values = list(data["constraints"][0]["values"])
    values[0] = OpaqueSequence(
        kind="sequence", items=(OpaqueInteger(kind="integer", value=999),)
    ).model_dump()
    data["constraints"][0]["values"] = tuple(values)

    with pytest.raises(ValidationError, match=r"unresolved parts_1 reference.*999"):
        NormalizedSlice.model_validate(data)


def test_slice_validates_source_references_geometry_and_dispositions() -> None:
    data = valid_slice().model_dump()
    data["parts"][0]["shape_hash"] = 999
    with pytest.raises(ValidationError, match="unresolved shape_hash"):
        NormalizedSlice.model_validate(data)

    data = valid_slice().model_dump()
    raw = list(data["shapes"][0]["raw"])
    raw[0] = 9
    data["shapes"][0]["raw"] = tuple(raw)
    with pytest.raises(ValidationError, match="adjacent raw scalars"):
        NormalizedSlice.model_validate(data)

    data = valid_slice().model_dump()
    data["task_dispositions"] = ()
    with pytest.raises(ValidationError, match="exactly one disposition"):
        NormalizedSlice.model_validate(data)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("area", 4.0, "area.*planar polygon"),
        ("is_valid", False, "is_valid.*planar polygon"),
        ("is_simple", False, "is_simple.*planar polygon"),
    ],
)
def test_derived_geometry_flags_and_area_must_match_shapely_truth(
    field: str, value: float | bool, message: str
) -> None:
    data = valid_slice().derived_geometry[0].model_dump()
    data[field] = value

    with pytest.raises(ValidationError, match=message):
        DerivedShapeGeometry.model_validate(data)


def invalid_bowtie_slice_data() -> dict[str, object]:
    data = valid_slice().model_dump()
    data["shapes"][0].update(
        raw=(0, 0, 2, 2, 0, 2, 2, 0),
        sizes=(8,),
    )
    data["derived_geometry"][0].update(
        paired_points=((0, 0), (2, 2), (0, 2), (2, 0)),
        closed_ring=((0, 0), (2, 2), (0, 2), (2, 0), (0, 0)),
        raw_scalar_count=8,
        is_simple=False,
        is_valid=False,
        has_nonzero_area=False,
        area=0.0,
        bounds=(0, 0, 2, 2),
    )
    return data


def test_eligible_tasks_reject_truthfully_invalid_or_zero_area_shapes() -> None:
    with pytest.raises(ValidationError, match="eligible.*simple, valid, and nonzero"):
        NormalizedSlice.model_validate(invalid_bowtie_slice_data())


def test_truthfully_invalid_geometry_remains_available_for_view_only_exclusions() -> None:
    data = invalid_bowtie_slice_data()
    data["task_dispositions"][0].update(
        support_status=SupportStatus.VIEW_ONLY,
        projection_status=ProjectionStatus.BLOCKED,
        reason_codes=("invalid_geometry",),
        assumption_codes=(),
    )
    data["provenance"] = tuple(
        group for group in data["provenance"] if group["kind"] != ProvenanceKind.ASSUMED
    )

    normalized = NormalizedSlice.model_validate(data)

    assert normalized.derived_geometry[0].is_valid is False


def test_every_selected_task_has_parts_and_every_selected_shape_is_referenced() -> None:
    data = valid_slice().model_dump()
    second_task = copy.deepcopy(data["tasks"][0])
    second_task["source_row_index"] = 11
    second_task["tasks_index"] = 18
    data["tasks"] = (*data["tasks"], second_task)
    second_disposition = copy.deepcopy(data["task_dispositions"][0])
    second_disposition["tasks_index"] = 18
    data["task_dispositions"] = (*data["task_dispositions"], second_disposition)
    with pytest.raises(ValidationError, match=r"task 18.*at least one part"):
        NormalizedSlice.model_validate(data)

    data = valid_slice().model_dump()
    extra_shape = copy.deepcopy(data["shapes"][0])
    extra_shape["source_row_index"] = 31
    extra_shape["shape_hash"] = 102
    data["shapes"] = (*data["shapes"], extra_shape)
    extra_geometry = copy.deepcopy(data["derived_geometry"][0])
    extra_geometry["shape_hash"] = 102
    data["derived_geometry"] = (*data["derived_geometry"], extra_geometry)
    with pytest.raises(ValidationError, match=r"shape 102.*referenced"):
        NormalizedSlice.model_validate(data)


def test_view_only_or_rejected_dispositions_require_reason_codes() -> None:
    data = valid_slice().task_dispositions[0].model_dump()
    data.update(
        support_status=SupportStatus.VIEW_ONLY,
        projection_status=ProjectionStatus.BLOCKED,
        reason_codes=(),
    )
    with pytest.raises(ValidationError, match="view-only.*reason"):
        TaskDisposition.model_validate(data)

    data = valid_slice().task_dispositions[0].model_dump()
    data.update(
        normalization_status=NormalizationStatus.REJECTED,
        projection_status=ProjectionStatus.BLOCKED,
        reason_codes=(),
    )
    with pytest.raises(ValidationError, match="rejected normalization.*reason"):
        TaskDisposition.model_validate(data)


def test_assumptions_require_assumed_provenance_and_paths_require_real_roots() -> None:
    data = valid_slice().model_dump()
    data["provenance"] = tuple(
        group for group in data["provenance"] if group["kind"] != ProvenanceKind.ASSUMED
    )
    with pytest.raises(ValidationError, match="assumptions require.*ASSUMED provenance"):
        NormalizedSlice.model_validate(data)

    with pytest.raises(ValidationError, match="JSON-pointer-like"):
        ProvenanceGroup(
            kind=ProvenanceKind.SOURCE_REAL,
            field_paths=("tasks",),
            note="Not a rooted path.",
        )
    with pytest.raises(ValidationError, match="artifact root"):
        ProvenanceGroup(
            kind=ProvenanceKind.SOURCE_REAL,
            field_paths=("/task",),
            note="Nonexistent root.",
        )
    with pytest.raises(ValidationError, match="nested field"):
        ProvenanceGroup(
            kind=ProvenanceKind.SOURCE_REAL,
            field_paths=("/tasks/nonexistent",),
            note="Nonexistent nested field.",
        )


@pytest.mark.parametrize(
    ("kind", "path", "message"),
    [
        (ProvenanceKind.SOURCE_REAL, "/derived_geometry", "SOURCE_REAL.*derived"),
        (
            ProvenanceKind.SOURCE_REAL,
            "/source/source_manifest_sha256",
            "SOURCE_REAL.*derived",
        ),
        (ProvenanceKind.DERIVED, "/tasks", "DERIVED.*source"),
        (ProvenanceKind.DERIVED, "/source/doi", "DERIVED.*source"),
        (
            ProvenanceKind.ASSUMED,
            "/task_dispositions/reason_codes",
            "ASSUMED.*assumption_codes",
        ),
        (ProvenanceKind.GENERATED, "/tasks", "GENERATED.*not supported"),
    ],
)
def test_provenance_kinds_cannot_claim_other_evidence_families(
    kind: ProvenanceKind, path: str, message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        ProvenanceGroup(kind=kind, field_paths=(path,), note="Wrong evidence family.")


@pytest.mark.parametrize(
    ("kind", "missing_path", "message"),
    [
        (ProvenanceKind.SOURCE_REAL, "/source/doi", "SOURCE_REAL.*minimum coverage"),
        (
            ProvenanceKind.DERIVED,
            "/task_dispositions/reason_codes",
            "DERIVED.*minimum coverage",
        ),
        (
            ProvenanceKind.DERIVED,
            "/source/audit_report_sha256",
            "DERIVED.*minimum coverage",
        ),
    ],
)
def test_slice_requires_minimum_source_and_derived_provenance_coverage(
    kind: ProvenanceKind, missing_path: str, message: str
) -> None:
    data = valid_slice().model_dump()
    for group in data["provenance"]:
        if group["kind"] == kind:
            group["field_paths"] = tuple(
                path for path in group["field_paths"] if path != missing_path
            )

    with pytest.raises(ValidationError, match=message):
        NormalizedSlice.model_validate(data)


def test_assumed_provenance_may_be_absent_only_when_no_assumptions_exist() -> None:
    data = valid_slice().model_dump()
    disposition = data["task_dispositions"][0]
    disposition.update(
        support_status=SupportStatus.VIEW_ONLY,
        projection_status=ProjectionStatus.BLOCKED,
        assumption_codes=(),
        reason_codes=("unsupported_constraint",),
    )
    with pytest.raises(ValidationError, match="ASSUMED provenance must be absent"):
        NormalizedSlice.model_validate(data)

    data["provenance"] = tuple(
        group for group in data["provenance"] if group["kind"] != ProvenanceKind.ASSUMED
    )

    normalized = NormalizedSlice.model_validate(data)

    assert ProvenanceKind.ASSUMED not in {group.kind for group in normalized.provenance}


def test_whole_source_provenance_is_rejected_in_favor_of_exhaustive_leaf_families() -> None:
    with pytest.raises(ValidationError, match="SOURCE_REAL.*leaf-level"):
        ProvenanceGroup(
            kind=ProvenanceKind.SOURCE_REAL,
            field_paths=("/source",),
            note="Too coarse to distinguish observed and derived identity fields.",
        )


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
