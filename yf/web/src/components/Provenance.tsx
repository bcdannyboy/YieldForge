import type { ProvenanceKind } from "../contracts";

const labels: Record<ProvenanceKind, { code: string; label: string; glyph: string }> = {
  source_real: { code: "SRC", label: "Source real", glyph: "■" },
  derived: { code: "DRV", label: "Derived", glyph: "◆" },
  generated: { code: "GEN", label: "Generated", glyph: "●" },
  assumed: { code: "ASM", label: "Assumed", glyph: "▲" },
};

export function ProvenanceMark({ kind }: { kind: ProvenanceKind }) {
  const item = labels[kind];
  return (
    <span className={`provenance provenance--${kind}`} aria-label={`${item.code} ${item.label}`}>
      <span aria-hidden="true">{item.glyph}</span> <strong>{item.code}</strong> {item.label}
    </span>
  );
}

export function mapOrderBookProvenance(
  kind: "source_observed" | "derived" | "generated" | "assumed",
): ProvenanceKind {
  return kind === "source_observed" ? "source_real" : kind;
}
