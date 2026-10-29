import { useMemo, useState } from "react";
import type { Limit, Point } from "../api";

interface Props {
  title: string;
  unit: string;
  points: Point[];
  limit?: Limit;
  digits?: number;
}

const W = 320;
const H = 96; // plot height
const AXIS = 18; // x-axis band below the plot
const PAD_L = 40;
const PAD_R = 8;

/**
 * Single-series telemetry trend. Limit zones are drawn as faint status washes
 * (warning / critical) behind a 2px line; the title names the series, so no
 * legend box. Hover shows a crosshair and the exact value.
 */
export function Sparkline({ title, unit, points, limit, digits = 2 }: Props) {
  const [hover, setHover] = useState<number | null>(null);

  const geo = useMemo(() => {
    const vals = points.map((p) => p.value);
    const bounds = [limit?.yellow_low, limit?.yellow_high].filter((v): v is number => v != null);
    let lo = Math.min(...vals, ...bounds);
    let hi = Math.max(...vals, ...bounds);
    if (!isFinite(lo) || !isFinite(hi)) {
      lo = 0;
      hi = 1;
    }
    const span = hi - lo || Math.abs(hi) || 1;
    lo -= span * 0.1;
    hi += span * 0.1;
    const x = (i: number) => PAD_L + (i / Math.max(points.length - 1, 1)) * (W - PAD_L - PAD_R);
    const y = (v: number) => H - ((v - lo) / (hi - lo)) * H;
    return { lo, hi, x, y };
  }, [points, limit]);

  const path = points.map((p, i) => `${i ? "L" : "M"}${geo.x(i).toFixed(1)},${geo.y(p.value).toFixed(1)}`).join("");
  const latest = points.length ? points[points.length - 1].value : undefined;

  const band = (from: number | null | undefined, to: number | null | undefined, cls: string, key: string) => {
    if (from == null && to == null) return null;
    const a = Math.min(Math.max(from ?? geo.lo, geo.lo), geo.hi);
    const b = Math.min(Math.max(to ?? geo.hi, geo.lo), geo.hi);
    if (b <= a) return null;
    return <rect key={key} className={cls} x={PAD_L} width={W - PAD_L - PAD_R} y={geo.y(b)} height={geo.y(a) - geo.y(b)} />;
  };

  const onMove = (e: React.MouseEvent<SVGRectElement>) => {
    if (!points.length) return;
    const box = e.currentTarget.getBoundingClientRect();
    const fx = ((e.clientX - box.left) / box.width) * (W - PAD_L - PAD_R);
    const i = Math.round((fx / (W - PAD_L - PAD_R)) * (points.length - 1));
    setHover(Math.max(0, Math.min(points.length - 1, i)));
  };

  const hp = hover != null ? points[hover] : undefined;

  return (
    <figure className="card spark">
      <figcaption>
        <span className="title">{title}</span>
        <span className="value">
          {latest !== undefined ? latest.toFixed(digits) : "--"} <small>{unit}</small>
        </span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H + AXIS}`} role="img" aria-label={`${title} trend, latest ${latest ?? "no data"} ${unit}`}>
        {limit && (
          <g>
            {band(null, limit.red_low, "zone-critical", "rl")}
            {band(limit.red_low, limit.yellow_low, "zone-warning", "yl")}
            {band(limit.yellow_high, limit.red_high, "zone-warning", "yh")}
            {band(limit.red_high, null, "zone-critical", "rh")}
          </g>
        )}
        <line className="grid" x1={PAD_L} x2={W - PAD_R} y1={0.5} y2={0.5} />
        <line className="axis" x1={PAD_L} x2={W - PAD_R} y1={H - 0.5} y2={H - 0.5} />
        <text className="tick" x={PAD_L - 4} y={10} textAnchor="end">{geo.hi.toFixed(digits)}</text>
        <text className="tick" x={PAD_L - 4} y={H} textAnchor="end">{geo.lo.toFixed(digits)}</text>
        <text className="tick" x={PAD_L} y={H + 13}>{points.length ? `${points.length} samples` : "no data"}</text>
        <path className="series" d={path} />
        {hp && hover != null && (
          <g>
            <line className="crosshair" x1={geo.x(hover)} x2={geo.x(hover)} y1={0} y2={H} />
            <circle className="marker" cx={geo.x(hover)} cy={geo.y(hp.value)} r={4} />
            <text className="tip" x={W - PAD_R} y={H + 13} textAnchor="end">
              {new Date(hp.t).toISOString().slice(11, 19)}Z  {hp.value.toFixed(digits)} {unit}
            </text>
          </g>
        )}
        <rect className="hit" x={PAD_L} y={0} width={W - PAD_L - PAD_R} height={H} onMouseMove={onMove} onMouseLeave={() => setHover(null)} />
      </svg>
    </figure>
  );
}
