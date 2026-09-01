# Existing-workflow overlay framing design

**Status:** approved on 2026-09-01.

## Goal

Make the public entry points lead with YieldForge's intended workflow advantage: a manufacturer
would keep its existing nesting system and normal nesting workflow while YieldForge added
future-aware selection over the strong layouts that system already produced.

## Semantic model

The incumbent nesting system remains responsible for producing process-feasible layouts optimized
for immediate sheet usage. YieldForge sits above that system as a decision layer. It receives
several strong, near-tied candidate layouts, values how each layout's residual geometry could serve
later jobs, and selects among those existing candidates using cumulative material cost rather than
replacing the underlying nesting engine.

Lead with the practical implication: adopting the idea was meant to preserve the incumbent solver,
operator habits, and core nesting workflow. The research-side Sparrow/Spyrrow boundary is an
example of this architecture, not the product itself. Sparrow was selected because it was already
a strong immediate-space optimizer and exposed progressive intermediate/alternative nests through
Spyrrow. That made it possible to test whether YieldForge could add value by choosing among the
incumbent's strong candidates rather than by replacing the incumbent.

## Claim boundary

This is the intended product thesis, not a completed integration result. The experiment tested
candidate generation and re-ranking through one reproducible adapter and immutable archives. It
did not prove compatibility with arbitrary commercial nesting systems, access to their near-tied
candidates, live remnant/inventory synchronization, operator acceptance, or literally zero
integration work.

The corresponding requirement for another incumbent system is access to multiple strong candidate
nests, or an equivalent extension that exposes them. That is an integration requirement, not a
requirement to switch the operator's core nesting workflow.

The closeout result therefore evaluates the tested overlay mechanism on top of a strong spatial
nester. It does not evaluate a wholesale nesting-engine replacement, and the negative result does
not imply that customers would have needed to abandon their established nesting workflow.

## Documentation scope

- Update `README.md` so the workflow-preserving overlay is explicit in the opening description and
  `The idea` section.
- Update `Docs/Home.md` with the same concise conceptual boundary.
- Reinforce the Sparrow/Spyrrow paragraph only as needed to connect the experiment to the overlay
  architecture.
- Remove the superseded publication-blocker language from every tracked current-tree document,
  including earlier closeout plans, so the prepared public checkout has one coherent boundary.
- Do not alter experiment outcomes, protocol rules, milestone results, or artifact identities.

## Prepared-publication boundary

The owner has approved the retained proposal, diagrams, source-derived research material, project
name, and existing Git author metadata for publication. Current release documentation will retain
factual source attribution and transformation provenance without presenting those approved
materials as unresolved publication blockers. No project-level permissions file will be added or
treated as a release requirement.

The tracked research tree, compact M11 manifests, proposal, and attachments are candidates for the
public archive. Ignored raw/resumable evidence under `yf/var`, the external preservation archive,
local environments, caches, credentials, and future private packets remain outside the publication
boundary. The research workbench may be published as source but must continue to be described as a
local development tool, not a hardened hosted service.

`PUBLIC_RELEASE.md` will become a release-candidate record: owner decisions resolved, publishable
and excluded content explicit, technical verification required at the final commit, and repository
visibility left private until a separate explicit command.

## Validation

Review the final wording for these four claims:

1. the incumbent system still creates valid, space-efficient nests;
2. YieldForge selects among strong, near-tied incumbent candidates;
3. the intended benefit does not require replacing the core nesting workflow; and
4. real cross-vendor integration and workflow friction were not tested.

Run tracked Markdown link checks, path/privacy scans, and `git diff --check`. No executable code or
experiment artifact should change. Re-run the focused public-evidence and packaging tests, source
distribution inspection, secret/path scans, large-object inventory, and clean Git/remote checks
before pushing the prepared candidate to private `main`. A whole-tree semantic scan must find no
remaining statement that LOCo-derived material, the lack of a project-level permissions file, the
proposal/assets, author metadata, or project name blocks publication.
