# Milestone Roadmap

This is the working implementation sequence. It is adapted from [[Proposal/09 7. MVP Milestones and Acceptance Gates|Section 7 of the proposal]], but it corrects two practical dependency problems:

- Static polygon data is needed early for Sparrow, while chronological benchmark data belongs after the simulator exists.
- Exact residual diversity can only be accepted after residual geometry and material accounting exist.

The milestones answer a chain of increasingly expensive questions:

> rules → shared language → candidate layouts → trustworthy remnants → actual reuse → time → controlled futures → credible baseline → future-information value → search confidence → decision

## Sequence

1. [[M0 - Experiment contract]] — decide what would count as a valid result.
2. [[M1 - Minimal canonical model]] — give every component a common language.
3. [[M2 - Static data and Sparrow]] — turn Sparrow into a reproducible candidate source.
4. [[M3 - Residual geometry truth]] — determine what material actually remains.
5. [[M4 - Remnant reuse proof]] — prove an exact remnant can satisfy later work.
6. [[M5 - Deterministic replay]] — connect material decisions through time.
7. [[M6 - Temporal benchmark data]] — create controlled chronological test worlds.
8. [[M7 - Strong baseline]] — build an opponent worth beating.
9. [[M8 - Rollout oracle]] — measure the value of knowing the future.
10. [[M9 - Search validation]] — determine whether the oracle is trustworthy.
11. [[M10 - Experiment and verdict]] — run the evidence program and decide what comes next.

## Planning rule

Only the active milestone receives a detailed implementation plan. Later milestone notes explain their meaning and acceptance boundary, but we will plan them when we reach them.
