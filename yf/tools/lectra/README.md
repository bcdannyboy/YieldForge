# Locked Lectra qualifier

The four Lectra source files are gzip-compressed Python pickles. Treat them as executable,
untrusted input. The normal YieldForge process never imports pandas or opens them. This image
is the only qualification boundary: it verifies the exact published byte count and MD5 for all
four files before deserializing any of them, then emits one passive JSON report.

## Build the production image

Run from `yf/` after `uv.lock` is current:

```bash
docker build --pull \
  --file tools/lectra/Dockerfile \
  --tag yieldforge-lectra-qualifier:7030786-v1.1 \
  .
```

Both the Python 3.12.11 base and uv 0.10.8 source are pinned by multi-architecture digest. The
deny-by-default `.dockerignore` admits only the lock inputs and the minimal qualifier code. The
runtime image receives the locked data-group environment, dataset contracts, audit code, pinned
manifest, and entry point—no repository metadata, credentials, archives, or application solver.

## Run against the verified release

Create an empty host output directory first. The input must contain exactly `tasks.gz`,
`parts.gz`, `shapes.gz`, and `constraints.gz` from the pinned release. Replace the two absolute
host paths below; do not mount a home directory or a replacement manifest.

On macOS Docker Desktop, add the numeric host identity shown below so the non-root process can
write to the bind mount. `id -u` and `id -g` typically expand to values such as `501:20`; neither
value may be zero. On Linux, either use the same override or make the empty output directory
writable by the image's numeric `65532:65532` user.

```bash
docker run --rm --init \
  --network none \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --pids-limit 128 \
  --memory 8g \
  --cpus 4 \
  --ulimit nofile=1024:1024 \
  --ulimit nproc=128:128 \
  --ipc none \
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
  --user "$(id -u):$(id -g)" \
  --mount type=bind,src=/ABSOLUTE/PATH/TO/raw/lectra-7030786-v1.1,dst=/input,readonly \
  --mount type=bind,src=/ABSOLUTE/PATH/TO/empty-report-directory,dst=/output \
  yieldforge-lectra-qualifier:7030786-v1.1
```

The command refuses missing files, extra files, symbolic links, byte-count or checksum mismatch,
and any pre-existing output entry. A successful run leaves exactly
`/output/lectra-audit.json`. Keep the raw mount read-only and validate that passive report with
the normal `yieldforge datasets audit-check` command.

## Trusted boundary smoke test

`make_trusted_fixture.py` creates—not reads—four tiny pandas pickle fixtures with the published
table schema. Its optional manifest is test-only. To exercise the production Dockerfile without
weakening the production image, assemble a temporary build context containing the same reviewed
files but substitute the generated manifest there. Never copy that fixture manifest into this
repository.

Use a new empty temporary path, then run from `yf/`:

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

docker run --rm --init \
  --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true \
  --pids-limit 128 --memory 2g --cpus 2 \
  --ulimit nofile=1024:1024 --ulimit nproc=128:128 \
  --ipc none --tmpfs /tmp:rw,noexec,nosuid,nodev,size=64m \
  --user "$(id -u):$(id -g)" \
  --mount type=bind,src=/tmp/yieldforge-lectra-fixture/input,dst=/input,readonly \
  --mount type=bind,src=/tmp/yieldforge-lectra-fixture/output,dst=/output \
  yieldforge-lectra-qualifier:trusted-smoke
```

The smoke succeeds only if the output directory contains exactly `lectra-audit.json`. The test
image and fixture manifest establish container mechanics only; they are not evidence about the
published corpus.
