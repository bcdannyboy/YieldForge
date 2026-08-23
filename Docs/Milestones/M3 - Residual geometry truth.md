# M3 — Residual geometry truth

**Status:** Next — M2 passed; implementation planning pending

M3 determines what material actually remains after placed parts, clearance, kerf, and forbidden regions are removed. It extracts connected residual polygons, separates retained remnants from scrap, and accounts for every unit of modeled material.

> **Question:** Do candidate nests create genuinely different, correctly accounted-for remnants?

## Acceptance boundary

Adversarial fixtures satisfy the material-reconciliation invariant, invalid geometry fails loudly, and M2 candidates show exact residual differences rather than merely visual placement differences.
