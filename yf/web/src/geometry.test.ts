import { describe, expect, it } from "vitest";

import { transformPlacedPoints } from "./geometry";

describe("explicit placement transform", () => {
  it.each([
    [0, [[12, 15], [13, 15], [13, 14]]],
    [90, [[12, 15], [12, 14], [11, 14]]],
    [180, [[12, 15], [11, 15], [11, 16]]],
  ] as const)("rotates %s degrees at origin, translates, then flips Y", (rotation, expected) => {
    const source = [[0, 0], [1, 0], [1, 1]] as const;
    const snapshot = structuredClone(source);

    expect(transformPlacedPoints(source, rotation, [12, 5], 20)).toEqual(expected);
    expect(source).toEqual(snapshot);
  });

  it("rejects non-finite geometry", () => {
    expect(() => transformPlacedPoints([[0, Number.NaN]], 0, [0, 0], 10)).toThrow(
      /finite/,
    );
  });
});
