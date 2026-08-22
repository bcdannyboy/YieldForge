export type ReadonlyPoint = readonly [number, number];

function requireFinite(value: number, label: string): number {
  if (!Number.isFinite(value)) throw new TypeError(`${label} must be finite`);
  return value;
}

export function transformPlacedPoints(
  points: readonly ReadonlyPoint[],
  rotationDegrees: number,
  translation: ReadonlyPoint,
  sheetWidth: number,
): Array<[number, number]> {
  const radians = (requireFinite(rotationDegrees, "rotation") * Math.PI) / 180;
  const cosine = Math.cos(radians);
  const sine = Math.sin(radians);
  const translateX = requireFinite(translation[0], "translation x");
  const translateY = requireFinite(translation[1], "translation y");
  const height = requireFinite(sheetWidth, "sheet width");
  const nearZero = (value: number) => (Math.abs(value) < 1e-10 ? 0 : value);

  return points.map(([rawX, rawY]) => {
    const x = requireFinite(rawX, "point x");
    const y = requireFinite(rawY, "point y");
    const rotatedX = x * cosine - y * sine;
    const rotatedY = x * sine + y * cosine;
    return [
      nearZero(rotatedX + translateX),
      nearZero(height - (rotatedY + translateY)),
    ];
  });
}

export function toSvgPoints(points: readonly ReadonlyPoint[]): string {
  return points.map(([x, y]) => `${x},${y}`).join(" ");
}
