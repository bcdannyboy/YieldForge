# Locked Lectra qualifier

The four Lectra source files are gzip-compressed Python pickles and therefore executable,
untrusted input. The normal YieldForge process never opens them. Qualification has two separate
halves:

1. The untrusted container verifies each opened source while copying it into a Linux sealed memfd.
   Pandas receives that same sealed, rewound handle; it never reopens a host path.
2. The trusted host runner captures one bounded JSON artifact from stdout, validates it against
   the audit or normalized-slice contract and exact source evidence, then publishes it with atomic
   no-clobber semantics.

No host output path is mounted into the container. The root filesystem is read-only, so pickle
code has no writable route to the eventual report directory.

## Build the production image

Run from `yf/` after `uv.lock` is current:

```bash
docker build --pull \
  --file tools/lectra/Dockerfile \
  --tag yieldforge-lectra-qualifier:7030786-v1.1 \
  .
```

Python 3.12.11 and uv 0.10.8 are pinned by multi-architecture digest. The deny-by-default
`.dockerignore` admits only the lock inputs and minimal qualifier code. The final image contains
the locked data-group environment, contracts, audit code, pinned manifest, and qualifier—no Git
metadata, credentials, host runner, normal solver, or repository home.

## Qualify the verified release

The input directory must contain exactly `tasks.gz`, `parts.gz`, `shapes.gz`, and
`constraints.gz`. Create a new, empty output directory. Then run the trusted publisher from
`yf/`, replacing the two absolute paths:

```bash
mkdir -p /ABSOLUTE/PATH/TO/empty-report-directory

uv run python tools/lectra/run_qualifier.py \
  --image yieldforge-lectra-qualifier:7030786-v1.1 \
  --input /ABSOLUTE/PATH/TO/raw/lectra-7030786-v1.1 \
  --output /ABSOLUTE/PATH/TO/empty-report-directory \
  --manifest datasets/sources/lectra-7030786-v1.1.json \
  --timeout-seconds 900
```

The runner refuses root UID or GID, symbolic links, extra input files, a nonempty output
directory, oversized output, non-finite, duplicate-key, or trailing JSON, schema mismatch, and
dataset/checksum identity mismatch. Before Docker starts it opens the output directory with
`O_DIRECTORY`, `O_NOFOLLOW`, and `O_CLOEXEC`, records its device and inode, and holds that
descriptor through container cleanup, validation, and publication. On macOS Docker Desktop it
passes the numeric host UID and GID (commonly `501:20`) into the container so publication does
not require a root container. Do not run the publisher with `sudo`.

For each run the runner generates a unique container name. It invokes Docker without
`shell=True` and applies:

- `--pull never`, `--network none`, and `--read-only`;
- `--cap-drop ALL` and `--security-opt no-new-privileges:true`;
- numeric non-root `--user`, `--pids-limit 128`, `--memory 16g`, `--memory-swap 16g`, and
  `--cpus 4`;
- `nofile` and `nproc` limits, `--ipc none`, an isolated 64 MiB `/tmp` tmpfs, and
  `--log-driver none`;
- exactly one bind mount: the input at `/input`, read-only.

Stdout is capped at 4 MiB, stderr at 1 MiB, and runtime at the requested timeout. On every exit
path the runner retries `docker rm --force`, then uses `docker inspect --type container` to prove
the unique generated name is absent. Cleanup failure is fatal and names the unconfirmed
container; the local Docker client is terminated even if abort cleanup fails.

The 16 GiB memory ceiling is based on the canonical full-corpus qualification whose cgroup peak was
9,721,896,960 bytes; it leaves headroom above the observed audit while keeping the container
bounded. The default 900-second timeout likewise exceeds the observed qualification. The
qualifier records each sealed-staging, table-validation, and audit-completion stage on stderr,
including bounded cgroup-v2 `memory.current` and `memory.peak` values when Linux exposes them.

After a clean exit, the runner accepts exactly one finite JSON payload from stdout, recursively
rejects duplicate object keys, and validates the full Pydantic contract and pinned manifest
identity. It discards the captured representation and serializes canonical finite JSON from the
validated report. Temporary creation, listing, hard-link publication, bounded no-follow reading,
stat checks, and cleanup all operate relative to the held directory descriptor. It never
overwrites. Before returning, the final postcondition requires exactly `lectra-audit.json` with
the canonical bytes and proves the path still resolves to the held device and inode. A moved or
swapped output path causes descriptor-relative cleanup and failure.

## Export the representative slice

Slice mode reruns the same sealed-input qualification and applies one fixed selector. It searches
only the top 256 training, sheet-type-zero, positive-length tasks with 20–50 parts, ranked by
`abs(parts-35) + abs(unique_shapes-9) + abs(repeated_part_rows-23)` and then `tasks_index`.
The runnable task must contain only one well-formed `s1` row per part, degenerate finite rotation
entries, strict integer-zero flip flags, and simple valid nonzero single-ring polygons without
repair. A separate positive task containing a non-`s1` constraint is retained as a view-only
exclusion example. Failure never relaxes these rules.

The slice is also bound to the exact bytes of an already validated audit report and source
manifest. The host runner hashes both passive inputs and passes only the mode and two lowercase
SHA-256 values to the container; neither evidence path and no host output path is mounted. Use a
new empty output directory:

```bash
mkdir -p /ABSOLUTE/PATH/TO/empty-slice-directory

uv run python tools/lectra/run_qualifier.py \
  --mode slice \
  --image yieldforge-lectra-qualifier:7030786-v1.1 \
  --input /ABSOLUTE/PATH/TO/raw/lectra-7030786-v1.1 \
  --output /ABSOLUTE/PATH/TO/empty-slice-directory \
  --manifest datasets/sources/lectra-7030786-v1.1.json \
  --audit-report /ABSOLUTE/PATH/TO/lectra-audit.json \
  --timeout-seconds 900
```

The only successful output is canonical `lectra-slice.json`. It preserves selected task, part,
shape, and constraint source rows in source-index order, including exact `raw`, `sizes`, and typed
opaque constraint cells in the observed column order. Adjacent-scalar pairing, reversible ring
closure, geometry facts, support status, and the single
`interpret_s1_degenerate_entries_as_allowed_rotations` assumption remain separately labelled.
The literal source unit stays `m^-4` with no inferred interpretation.

## Export the qualified catalog

Catalog mode uses the same sealed-input container and exact manifest/audit evidence binding as
slice mode, but calls only the deterministic `export_catalog_slice` path. It exports exactly 256
fully display-safe tasks, including continuity tasks `13958` and `25801`, under the
`lectra-catalog-rules.v2` ruleset. This bounded research catalog is not a prevalence sample of the
full release. Uniform binary `s1` flip states are retained: a source-recorded projection interprets
`flip_x = 1` as local-x negation before rotation, and a separately labeled no-flip projection is
available only as an intervention-backed ablation. Mixed-within-row and nonbinary flip states, plus
all non-`s1` constraints, remain blocked rather than guessed.

Use a new empty output directory and the exact validated audit report whose bytes should bind the
catalog:

```bash
mkdir -p /ABSOLUTE/PATH/TO/empty-catalog-directory

uv run python tools/lectra/run_qualifier.py \
  --mode catalog \
  --image yieldforge-lectra-qualifier:7030786-v1.1 \
  --input /ABSOLUTE/PATH/TO/raw/lectra-7030786-v1.1 \
  --output /ABSOLUTE/PATH/TO/empty-catalog-directory \
  --manifest datasets/sources/lectra-7030786-v1.1.json \
  --audit-report /ABSOLUTE/PATH/TO/lectra-audit.json \
  --timeout-seconds 900
```

The only successful output is canonical `lectra-catalog.json`. Catalog stdout, strict validation,
canonical serialization, atomic publication, and descriptor-relative readback each use a separate
64 MiB ceiling. Audit and representative-slice mode remain capped at 4 MiB throughout those same
stages. The trusted runner still publishes into an initially empty held directory without
overwriting, and Docker security, timeout, stderr, and cleanup requirements are unchanged.

## Trusted Docker smoke fixtures

`make_trusted_fixture.py` creates—never reads—four tiny pandas pickle files with the published
columns, a 35-part runnable `s1` task, and a separate 20-part non-`s1` view task. Its optional
manifest is test-only. A separate flag
adds a deliberate pickle code-execution probe that attempts to write `/output/pickle-escape`;
the hardened container has no output mount, so the only host artifact remains the validated JSON
published afterward by the trusted runner.

Assemble a temporary build context rather than replacing the repository manifest. Starting with
a new empty temporary path, run from `yf/`:

```bash
uv run --group data python tools/lectra/make_trusted_fixture.py \
  --output /tmp/yieldforge-lectra-fixture/input \
  --manifest-output /tmp/yieldforge-lectra-fixture/fixture-manifest.json

mkdir -p /tmp/yieldforge-lectra-fixture/context/datasets/sources \
  /tmp/yieldforge-lectra-fixture/context/src/yieldforge/datasets \
  /tmp/yieldforge-lectra-fixture/context/tools/lectra \
  /tmp/yieldforge-lectra-fixture/audit-output \
  /tmp/yieldforge-lectra-fixture/slice-output
cp pyproject.toml uv.lock .dockerignore /tmp/yieldforge-lectra-fixture/context/
cp src/yieldforge/__init__.py src/yieldforge/domain.py \
  /tmp/yieldforge-lectra-fixture/context/src/yieldforge/
cp src/yieldforge/datasets/__init__.py src/yieldforge/datasets/source_manifest.py \
  src/yieldforge/datasets/lectra_audit.py src/yieldforge/datasets/normalized_slice.py \
  src/yieldforge/datasets/lectra_slice.py \
  /tmp/yieldforge-lectra-fixture/context/src/yieldforge/datasets/
cp tools/lectra/Dockerfile tools/lectra/qualify.py \
  /tmp/yieldforge-lectra-fixture/context/tools/lectra/
cp /tmp/yieldforge-lectra-fixture/fixture-manifest.json \
  /tmp/yieldforge-lectra-fixture/context/datasets/sources/lectra-7030786-v1.1.json

docker build \
  --file /tmp/yieldforge-lectra-fixture/context/tools/lectra/Dockerfile \
  --tag yieldforge-lectra-qualifier:trusted-smoke \
  /tmp/yieldforge-lectra-fixture/context

uv run python tools/lectra/run_qualifier.py \
  --image yieldforge-lectra-qualifier:trusted-smoke \
  --input /tmp/yieldforge-lectra-fixture/input \
  --output /tmp/yieldforge-lectra-fixture/audit-output \
  --manifest /tmp/yieldforge-lectra-fixture/fixture-manifest.json \
  --timeout-seconds 30

uv run python tools/lectra/run_qualifier.py \
  --mode slice \
  --image yieldforge-lectra-qualifier:trusted-smoke \
  --input /tmp/yieldforge-lectra-fixture/input \
  --output /tmp/yieldforge-lectra-fixture/slice-output \
  --manifest /tmp/yieldforge-lectra-fixture/fixture-manifest.json \
  --audit-report /tmp/yieldforge-lectra-fixture/audit-output/lectra-audit.json \
  --timeout-seconds 30
```

The two smokes succeed only when their outputs contain exactly `lectra-audit.json` and
`lectra-slice.json`, respectively. The fixture image tests isolation mechanics; it is not evidence
about the published corpus.
