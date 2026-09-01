# Public release candidate record

**Status:** content approved and technically prepared, subject to exact final verification.

YieldForge is prepared as a public research archive. The owner has reviewed and approved the
tracked publication boundary described below. This records an owner decision about what belongs in
the public archive; it is not an independent legal certification of every upstream source.

The GitHub repository remains **private**. Changing its visibility is a separate external action
that must not happen without a new explicit command after final verification of the exact commit.

## Approved tracked boundary

The intended public archive includes the complete tracked research tree and history, including:

- the YieldForge proposal and all six tracked diagrams under `Docs/Attachments/`;
- the source code, tests, notebooks, documentation, experiment contracts, and historical plans;
- the tracked source-derived datasets, normalized benchmarks, fixtures, and canonical evidence;
- the three compact M11 calibration, validity, and central manifests, including their disclosed
  embedded checkpoint, receipt, and segment-summary records; and
- existing Git author metadata and the YieldForge working name.

Upstream material retains factual citations, versions, checksums, and transformation provenance in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Excluded local boundary

The public archive does not include material outside the tracked tree, including:

- ignored local state under `yf/var/`;
- the external closeout archive and its complete M11 replay packet;
- raw or resumable observations, sidecars, checkpoints, execution packets, and oversized local
  intermediate results that were deliberately kept outside Git;
- local environments, dependency trees, caches, browser downloads, build output, and test output;
  or
- credentials, tokens, machine-specific configuration, and other secrets.

The tracked M11 manifests intentionally disclose compact experiment records but omit the standalone
raw packet. A clean clone can authenticate and inspect those manifests; it cannot reconstruct or
fully replay the omitted raw sidecars from hashes alone.

## Resolved publication decisions

- The proposal, six diagrams, tracked source-derived research material, and current research
  history are approved for publication.
- Existing Git author names and email metadata are approved for retention.
- “YieldForge” is approved as the public research-project name.
- The tracked datasets, benchmarks, canonical evidence, and compact M11 manifests are within the
  approved publication boundary.
- Source attribution and transformation provenance remain part of the archive.

These decisions do not expand YieldForge's claims. The M11 result remains a semi-synthetic
disposition of the current modeled product and algorithms, not a measurement of representative
factory ROI or proof about every possible future algorithm.

## Large tracked research artifacts

The tracked tree retains two research-relevant JSON artifacts:

- `yf/experiments/results/residual-geometry-input-yfgi-2fe5b848ea643d282c284f90.json`
  (approximately 70.12 MiB); and
- `yf/experiments/results/m11-gate1-yfm11g1run-c35f10fa4f4d7b6b01c59c29.json`
  (approximately 52.43 MiB).

The latter produced GitHub's 52.43 MiB advisory warning. Both are intentionally retained because
they are research-relevant parts of the lineage and remain below GitHub's 100 MiB per-file hard
limit. This is a repository-size tradeoff, not a failed release check. See
[GitHub's large-file guidance](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).

## Final technical verification

Before changing visibility, verify the exact candidate commit and its complete intended public
history from a clean checkout:

- authenticate the selected M11 manifests and confirm the tracked artifact set;
- scan both the checkout and every reachable commit in the intended public history for credentials,
  local machine paths, unintended raw packets, and unreviewed generated output;
- resolve tracked Markdown links and source attributions;
- run the focused Python policy/evidence tests, Ruff checks, web tests, typecheck, and production
  build; and
- build and inspect the Python source distribution against its explicit allowlist.

Record the verified commit before the separate visibility change. Final verification confirms that
the approved boundary was implemented correctly; it is not a new content-approval gate.

## Operational boundary

Publishing the workbench source does not make the development services production-ready. The
FastAPI and web workbench remain local research tools and have not been hardened for exposure to
untrusted networks. Repository publication must not be treated as approval to deploy or expose
those services.
