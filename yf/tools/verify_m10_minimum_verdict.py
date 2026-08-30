"""Independently verify the committed minimum M10 investment verdict."""

import argparse
import hashlib
import json
import math
import os
import stat
from pathlib import Path

MAX_PARENT_BYTES = 4 * 1024 * 1024
MAX_ARTIFACT_BYTES = 1024 * 1024
READ_CHUNK_BYTES = 64 * 1024
ARTIFACT_PREFIX = "m10-minimum-investment-verdict"

PARENT_SPECS = (
    (
        "m0_contract",
        "experiments/m0-contract-v1.json",
        "yieldforge.m0-contract.v1",
        "contract_id",
        "yfm0-29b7efe8ac2a0a9995c4f907",
        "sha256:29b7efe8ac2a0a9995c4f907a56d7ce0cb9b61217b167f0737f6973c648b9a5f",
        "sha256:8ad20ca2ffaa4873588a4829d0d4fccfc85269429c6e2363b59d29be150d1c99",
    ),
    (
        "m6_contract",
        "benchmarks/temporal/m6-contract-v1.json",
        "yieldforge.temporal-benchmark-contract.v1",
        "contract_id",
        "yfm6-3eeda3f4feb80813807c501a",
        "sha256:3eeda3f4feb80813807c501ae71299a2add07ed76b75009e2f744daddae5a8aa",
        "sha256:461220a0a0860a7d31df48f50c3c5f8034a0c5fb5896ab40ae72bd74578bdd35",
    ),
    (
        "m6_population",
        "benchmarks/temporal/m6-population-v1.json",
        "yieldforge.temporal-population.v1",
        "population_id",
        "yftp-49bd7ce5fd34b2779440c52f",
        "sha256:49bd7ce5fd34b2779440c52fabdf2acb8ef80f39b025cdf5a9c6f8a1d2c958f9",
        "sha256:f864f10b6d3dced65d0ba6acd2c7201b3b01d06865fec748b028bbc4802a1e5b",
    ),
    (
        "m7_evaluation",
        "experiments/results/m7-evaluation-yfm7eval-f2cb310c4b7e879d119e8f94.json",
        "yieldforge.m7-evaluation-result.v1",
        "result_id",
        "yfm7eval-f2cb310c4b7e879d119e8f94",
        "sha256:f2cb310c4b7e879d119e8f940d5a3dc88cd4b26d48087b46323b7be848144931",
        "sha256:ba8fdbeaddb2ec9c289ead27627fca6a59f83012cd0093f557848b35c710b91f",
    ),
    (
        "m8_gate3",
        "experiments/results/m8-gate3-decision-yfm8g3decision-c13ec320e9fcd02873bf649c.json",
        "yieldforge.m8-gate3-decision.v2",
        "decision_id",
        "yfm8g3decision-c13ec320e9fcd02873bf649c",
        "sha256:c13ec320e9fcd02873bf649c4f8d84a66c48fb5c4a8e67ebf2fb2f5de268b03c",
        "sha256:8e1ca24321b5fd15445e06ebd680225ee42e10d895207cfbb496544d1613b551",
    ),
    (
        "m9_repair",
        "experiments/results/m9-two-ply-repair-validation-yfm9r-db0829451b1b0393f2d22559.json",
        "yieldforge.m9-two-ply-repair-validation.v1",
        "result_id",
        "yfm9r-db0829451b1b0393f2d22559",
        "sha256:db0829451b1b0393f2d2255990ade1ce783b27a8527f73f3c7bf07e6716438ba",
        "sha256:16444e2e0f1a6fa5fb57398b290a7b0b66fda271997a48202a403c499060b858",
    ),
)

MISSING_FORMAL_MEASUREMENTS = [
    "oracle_savings_percent",
    "unknown_future_contribution_percentage_points",
]

MISSING_REQUIRED_CONTROLS = [
    "full_future_oracle_evaluation",
    "known_only_information_ablation",
    "no_signal_oracle_control",
    "terminal_value_evaluation_sensitivity",
    "remnant_eligibility_evaluation_sensitivity",
    "ordinary_vs_expanded_search_evaluation",
    "rollout_vs_beam_evaluation",
    "strong_vs_myopic_baseline_evaluation",
]

REOPEN_CONDITIONS = [
    "permissioned_real_manufacturer_chronology_and_remnant_history",
    "observed_material_identities_and_economically_meaningful_costs",
    "independent_second_geometry_corpus",
    "buyer_or_operator_owned_bounded_decision_and_cost",
]

CLAIM_CEILING = (
    "investment_decision_only_not_formal_m0_economic_band_oracle_savings_unknown_future_"
    "contribution_physical_recoverability_factory_representativeness_adoption_realized_roi_"
    "integration_reliability_buyer_demand_or_commercial_proof"
)


class M10VerificationError(ValueError):
    """The artifact or one of its frozen parents failed independent verification."""


def _canonical_compact(value):
    try:
        return json.dumps(
            value,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as error:
        raise M10VerificationError("M10 value is not canonically serializable") from error


def _canonical_pretty(value):
    try:
        return (json.dumps(value, allow_nan=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    except (TypeError, ValueError, OverflowError) as error:
        raise M10VerificationError("M10 value is not canonically serializable") from error


def _directory_flags():
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    return flags


def _open_directory_no_follow(path, label):
    absolute = Path(os.path.abspath(os.fspath(path)))
    descriptor = None
    try:
        descriptor = os.open(os.path.sep, _directory_flags())
        for component in absolute.parts[1:]:
            child = os.open(component, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise M10VerificationError(f"{label} is not a directory")
        return descriptor
    except M10VerificationError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise M10VerificationError(
            f"{label} directory path is unavailable or contains a symlink"
        ) from error


def _read_regular_file_unchecked(path, max_bytes, label):
    absolute = Path(os.path.abspath(os.fspath(path)))
    if not absolute.name:
        raise M10VerificationError(f"{label} must name a regular file")
    directory_descriptor = _open_directory_no_follow(absolute.parent, label)
    file_descriptor = None
    try:
        name = absolute.name
        named = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
        if not stat.S_ISREG(named.st_mode):
            raise M10VerificationError(f"{label} must be a regular file")
        if named.st_size > max_bytes:
            raise M10VerificationError(f"{label} exceeds its size limit")

        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        file_descriptor = os.open(name, flags, dir_fd=directory_descriptor)
        opened = os.fstat(file_descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise M10VerificationError(f"{label} must remain a regular file")
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise M10VerificationError(f"{label} identity changed before reading")
        if opened.st_size > max_bytes:
            raise M10VerificationError(f"{label} exceeds its size limit")

        chunks = []
        total = 0
        while total <= max_bytes:
            chunk = os.read(
                file_descriptor,
                min(READ_CHUNK_BYTES, max_bytes + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > max_bytes:
            raise M10VerificationError(f"{label} exceeds its size limit")
        raw = b"".join(chunks)
        final = os.fstat(file_descriptor)
        before = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
            opened.st_ctime_ns,
        )
        after = (
            final.st_dev,
            final.st_ino,
            final.st_size,
            final.st_mtime_ns,
            final.st_ctime_ns,
        )
        if before != after or len(raw) != final.st_size:
            raise M10VerificationError(f"{label} changed during bounded read")
        return raw
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        os.close(directory_descriptor)


def _read_regular_file(path, max_bytes, label):
    if type(max_bytes) is not int or max_bytes <= 0:
        raise M10VerificationError("M10 size limit must be a positive integer")
    try:
        return _read_regular_file_unchecked(path, max_bytes, label)
    except M10VerificationError:
        raise
    except OSError as error:
        raise M10VerificationError(f"{label} could not be read safely") from error


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise M10VerificationError(f"M10 JSON contains duplicate key {key!r}")
        result[key] = value
    return result


def _reject_nonfinite(value):
    raise M10VerificationError(f"M10 JSON contains non-finite value {value}")


def _finite_float(value):
    parsed = float(value)
    if not math.isfinite(parsed):
        raise M10VerificationError("M10 JSON contains a non-finite number")
    return parsed


def _strict_json(raw, label):
    if type(raw) is not bytes:
        raise M10VerificationError(f"{label} input must be exact bytes")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite,
            parse_float=_finite_float,
        )
    except M10VerificationError:
        raise
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        OverflowError,
        RecursionError,
        ValueError,
    ) as error:
        raise M10VerificationError(f"{label} is not strict finite JSON") from error
    if type(value) is not dict:
        raise M10VerificationError(f"{label} root must be an object")
    if raw != _canonical_pretty(value):
        raise M10VerificationError(f"{label} is not canonical pretty JSON")
    return value


def _semantic_digest(payload, excluded_fields):
    semantic = dict(payload)
    for field in excluded_fields:
        semantic.pop(field, None)
    return hashlib.sha256(_canonical_compact(semantic)).hexdigest()


def _mapping(value, label):
    if type(value) is not dict:
        raise M10VerificationError(f"{label} must be an object")
    return value


def _sequence(value, label):
    if type(value) is not list:
        raise M10VerificationError(f"{label} must be an array")
    return value


def _require_exact(value, expected, label):
    if type(value) is not type(expected) or value != expected:
        raise M10VerificationError(f"{label} differs from the frozen value")
    return value


def _exactly_equal(left, right):
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return set(left) == set(right) and all(
            _exactly_equal(left[key], right[key]) for key in left
        )
    if type(left) is list:
        return len(left) == len(right) and all(
            _exactly_equal(item, expected) for item, expected in zip(left, right, strict=True)
        )
    return left == right


def _load_parent(evidence_root, spec):
    role, repository_path, schema, id_field, semantic_id, content_sha, raw_sha = spec
    raw = _read_regular_file(
        Path(evidence_root) / repository_path,
        MAX_PARENT_BYTES,
        f"M10 {role} parent",
    )
    observed_raw_sha = f"sha256:{hashlib.sha256(raw).hexdigest()}"
    if observed_raw_sha != raw_sha:
        raise M10VerificationError(f"M10 {role} raw SHA-256 differs")
    payload = _strict_json(raw, f"M10 {role} parent")
    _require_exact(payload.get("schema_version"), schema, f"M10 {role} schema")
    _require_exact(payload.get(id_field), semantic_id, f"M10 {role} semantic ID")
    _require_exact(
        payload.get("content_sha256"),
        content_sha,
        f"M10 {role} content SHA-256",
    )
    digest = _semantic_digest(payload, {id_field, "content_sha256"})
    if content_sha != f"sha256:{digest}" or semantic_id != (
        f"{semantic_id.rsplit('-', 1)[0]}-{digest[:24]}"
    ):
        raise M10VerificationError(f"M10 {role} semantic identity does not reconcile")
    binding = {
        "role": role,
        "repository_path": repository_path,
        "schema_version": schema,
        "semantic_id": semantic_id,
        "content_sha256": content_sha,
        "raw_file_sha256": observed_raw_sha,
    }
    return binding, payload


def _provenance_kind(m6_contract, field):
    entries = _sequence(
        m6_contract.get("field_provenance"),
        "M10 M6 field provenance",
    )
    matching = []
    for entry in entries:
        item = _mapping(entry, "M10 M6 provenance entry")
        if item.get("field") == field:
            kind = item.get("kind")
            if type(kind) is not str:
                raise M10VerificationError(f"M10 M6 {field} provenance kind must be a string")
            matching.append(kind)
    if len(matching) != 1:
        raise M10VerificationError(f"M10 M6 {field} provenance must occur exactly once")
    return matching[0]


def _expected_evidence(evidence_root):
    loaded = [_load_parent(evidence_root, spec) for spec in PARENT_SPECS]
    bindings = [item[0] for item in loaded]
    parents = {spec[0]: item[1] for spec, item in zip(PARENT_SPECS, loaded, strict=True)}
    m0 = parents["m0_contract"]
    m6_contract = parents["m6_contract"]
    m6_population = parents["m6_population"]
    m7 = parents["m7_evaluation"]
    m8 = parents["m8_gate3"]
    m9 = parents["m9_repair"]

    supporting = _mapping(
        _mapping(m0.get("decision_gates"), "M10 M0 decision gates").get("supporting"),
        "M10 M0 supporting gates",
    )
    required_corpora = _require_exact(
        supporting.get("minimum_geometry_corpora_with_positive_evidence"),
        2,
        "M10 M0 required corpus count",
    )
    source_catalog = _mapping(
        m6_contract.get("source_catalog"),
        "M10 M6 source catalog",
    )
    dataset_id = source_catalog.get("dataset_id")
    if type(dataset_id) is not str or not dataset_id:
        raise M10VerificationError("M10 M6 dataset ID must be a nonempty string")

    m0_spec = PARENT_SPECS[0]
    m6_spec = PARENT_SPECS[1]
    _require_exact(
        m6_contract.get("m0_contract_id"),
        m0_spec[4],
        "M10 M6-to-M0 semantic ID",
    )
    _require_exact(
        m6_contract.get("m0_contract_sha256"),
        m0_spec[5],
        "M10 M6-to-M0 content SHA-256",
    )
    _require_exact(
        m6_population.get("contract_id"),
        m6_spec[4],
        "M10 M6 population contract ID",
    )
    _require_exact(
        m6_population.get("contract_sha256"),
        m6_spec[5],
        "M10 M6 population contract SHA-256",
    )
    _require_exact(
        m6_population.get("source_catalog_sha256"),
        source_catalog.get("artifact_sha256"),
        "M10 M6 source catalog binding",
    )
    _require_exact(
        m7.get("m0_contract_id"),
        m0_spec[4],
        "M10 M7-to-M0 semantic ID",
    )
    _require_exact(
        m7.get("m0_contract_sha256"),
        m0_spec[5],
        "M10 M7-to-M0 content SHA-256",
    )
    stream_count = _require_exact(m7.get("stream_count"), 36, "M10 M7 stream count")
    repeat_count = _require_exact(m7.get("repeat_count"), 2, "M10 M7 repeat count")
    repeat_match = _require_exact(
        m7.get("repeat_content_identity_match"),
        True,
        "M10 M7 repeat identity",
    )
    _require_exact(
        m7.get("evaluation_partition_opened"),
        True,
        "M10 M7 evaluation state",
    )

    m8_decision = _require_exact(
        m8.get("decision"),
        "hold_performance",
        "M10 M8 decision",
    )
    evaluation_opened = _require_exact(
        m8.get("evaluation_opened"),
        False,
        "M10 M8 evaluation state",
    )
    _require_exact(
        m8.get("official_six_cell_executed"),
        False,
        "M10 M8 official six-cell state",
    )
    for forbidden_metric in MISSING_FORMAL_MEASUREMENTS:
        if forbidden_metric in m8:
            raise M10VerificationError(f"M10 M8 unexpectedly supplies {forbidden_metric}")

    evaluator = _mapping(m9.get("evaluator_result"), "M10 M9 evaluator result")
    m9_decision = _require_exact(
        evaluator.get("decision"),
        "pass_decision_feasibility",
        "M10 M9 decision",
    )

    return {
        "parents": bindings,
        "geometry_corpus_ids": [dataset_id],
        "required_positive_geometry_corpus_count": required_corpora,
        "chronology_provenance": _provenance_kind(m6_contract, "chronology"),
        "economics_provenance": _provenance_kind(m6_contract, "economics"),
        "material_provenance": _provenance_kind(m6_contract, "material"),
        "baseline_stream_count": stream_count,
        "baseline_repeat_count": repeat_count,
        "baseline_repeat_identity_match": repeat_match,
        "m8_decision": m8_decision,
        "oracle_evaluation_opened": evaluation_opened,
        "oracle_savings_percent": None,
        "unknown_future_contribution_percentage_points": None,
        "m9_decision": m9_decision,
    }


def _expected_semantic(evidence):
    return {
        "schema_version": "yieldforge.m10-minimum-investment-verdict.v1",
        "evidence": evidence,
        "decision_basis": "current_evidence_ceiling_without_formal_numeric_m0_band",
        "formal_economic_band": "not_computed",
        "formal_numeric_m10_complete": False,
        "investment_verdict": "acquire_real_manufacturer_history",
        "productization_decision": "do_not_productize",
        "additional_virtual_oracle_investment": "stop",
        "roadmap_decision_complete": True,
        "green_eligible": False,
        "missing_formal_measurements": MISSING_FORMAL_MEASUREMENTS,
        "missing_required_controls": MISSING_REQUIRED_CONTROLS,
        "reopen_conditions": REOPEN_CONDITIONS,
        "claim_ceiling": CLAIM_CEILING,
    }


def verify_m10_minimum_verdict(artifact_path, evidence_root):
    artifact_path = Path(artifact_path)
    raw = _read_regular_file(
        artifact_path,
        MAX_ARTIFACT_BYTES,
        "M10 verdict artifact",
    )
    payload = _strict_json(raw, "M10 verdict artifact")
    expected_semantic = _expected_semantic(_expected_evidence(Path(evidence_root)))
    actual_semantic = dict(payload)
    actual_semantic.pop("result_id", None)
    actual_semantic.pop("content_sha256", None)
    if not _exactly_equal(actual_semantic, expected_semantic):
        raise M10VerificationError(
            "M10 verdict fields differ from the independently derived decision"
        )

    digest = hashlib.sha256(_canonical_compact(expected_semantic)).hexdigest()
    expected = dict(expected_semantic)
    expected["result_id"] = f"yfm10-{digest[:24]}"
    expected["content_sha256"] = f"sha256:{digest}"
    if not _exactly_equal(payload, expected):
        raise M10VerificationError("M10 verdict semantic identity differs")
    expected_name = f"{ARTIFACT_PREFIX}-{expected['result_id']}.json"
    if artifact_path.name != expected_name:
        raise M10VerificationError("M10 verdict filename differs from its result ID")
    if raw != _canonical_pretty(expected):
        raise M10VerificationError("M10 verdict canonical bytes differ")
    return expected


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--evidence-root", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    try:
        verified = verify_m10_minimum_verdict(args.artifact, args.evidence_root)
    except M10VerificationError as error:
        raise SystemExit(f"M10 verification failed: {error}") from error
    print(
        json.dumps(
            {
                "artifact_path": str(args.artifact),
                "formal_economic_band": verified["formal_economic_band"],
                "investment_verdict": verified["investment_verdict"],
                "productization_decision": verified["productization_decision"],
                "result_id": verified["result_id"],
                "verified": True,
            },
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
