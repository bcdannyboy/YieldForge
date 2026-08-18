# Locked Lectra qualifier

The four Lectra source files are gzip-compressed Python pickles and therefore executable,
untrusted input. The normal YieldForge process never opens them. Qualification has two separate
halves:

1. The untrusted container verifies each opened source while copying it into a Linux sealed memfd.
   Pandas receives that same sealed, rewound handle; it never reopens a host path.
2. The trusted host runner captures a single JSON report from stdout, validates it against the
   pinned report schema and source manifest, then publishes it with atomic no-clobber semantics.

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

The 16 GiB memory ceiling is based on a full-corpus qualification probe whose cgroup peak was
9,534,488,576 bytes; it leaves headroom above the observed audit while keeping the container
bounded. The default 900-second timeout likewise exceeds the observed 306.6-second probe. The
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

## Trusted Docker smoke fixtures

`make_trusted_fixture.py` creates—never reads—four tiny pandas pickle files with the published
columns and a representative constraint. Its optional manifest is test-only. A separate flag
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
  /tmp/yieldforge-lectra-fixture/output
cp pyproject.toml uv.lock .dockerignore /tmp/yieldforge-lectra-fixture/context/
cp src/yieldforge/__init__.py src/yieldforge/domain.py \
  /tmp/yieldforge-lectra-fixture/context/src/yieldforge/
cp src/yieldforge/datasets/__init__.py src/yieldforge/datasets/source_manifest.py \
  src/yieldforge/datasets/lectra_audit.py \
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
  --output /tmp/yieldforge-lectra-fixture/output \
  --manifest /tmp/yieldforge-lectra-fixture/fixture-manifest.json \
  --timeout-seconds 30
```

The smoke succeeds only when the output contains exactly `lectra-audit.json`. The fixture image
tests isolation mechanics; it is not evidence about the published corpus.
