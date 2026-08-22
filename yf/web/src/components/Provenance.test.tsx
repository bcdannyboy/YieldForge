import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProvenanceMark } from "./Provenance";

describe("provenance mark", () => {
  it.each([
    ["source_real", "SRC Source real"],
    ["derived", "DRV Derived"],
    ["generated", "GEN Generated"],
    ["assumed", "ASM Assumed"],
  ] as const)("gives %s a textual accessible identity", (kind, name) => {
    render(<ProvenanceMark kind={kind} />);
    expect(screen.getByLabelText(name)).toBeVisible();
  });
});
