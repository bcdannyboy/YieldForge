import type { TaskDetail } from "../contracts";

function number(value: number | string): number {
  const parsed = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(parsed)) throw new TypeError("geometry coordinate must be finite");
  return parsed;
}

export function SourceGeometry({ detail }: { detail: TaskDetail }) {
  return (
    <div className="geometry-strip" aria-label="Source polygon geometry">
      {detail.derived_geometry.map((shape) => {
        const points = shape.closed_ring.map(([x, y]) => [number(x), number(y)] as const);
        const [minX, minY, maxX, maxY] = shape.bounds.map(number) as [
          number,
          number,
          number,
          number,
        ];
        const width = Math.max(maxX - minX, 1);
        const height = Math.max(maxY - minY, 1);
        const svgPoints = points.map(([x, y]) => `${x},${maxY - y + minY}`).join(" ");
        return (
          <figure key={shape.shape_hash} className="shape-figure">
            <svg
              viewBox={`${minX} ${minY} ${width} ${height}`}
              role="img"
              aria-label={`Source shape ${shape.shape_hash}`}
              preserveAspectRatio="xMidYMid meet"
            >
              <polygon points={svgPoints} vectorEffect="non-scaling-stroke" />
            </svg>
            <figcaption>{shape.shape_hash}</figcaption>
          </figure>
        );
      })}
    </div>
  );
}
