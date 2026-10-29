import { useState } from "react";
import type { PassRow } from "../api";

interface Props {
  passes: PassRow[];
  hours?: number;
  now?: Date;
}

const W = 900;
const ROW = 30;
const BAR = 14;
const LABEL = 110;
const AXIS = 22;

/**
 * Contact timeline: one row per ground station, one bar per predicted pass.
 * Scheduled contacts use the accent; predicted-but-unscheduled passes are muted.
 * Rows are labelled directly so colour never carries station identity.
 */
export function PassTimeline({ passes, hours = 24, now = new Date() }: Props) {
  const [hover, setHover] = useState<PassRow | null>(null);
  const t0 = now.getTime() - 3600_000;
  const t1 = t0 + hours * 3600_000;
  const stations = Array.from(new Set(passes.map((p) => p.station))).sort();
  const x = (t: number) => LABEL + ((t - t0) / (t1 - t0)) * (W - LABEL - 8);
  const H = stations.length * ROW + AXIS;
  const ticks = Array.from({ length: Math.floor(hours / 3) + 1 }, (_, k) => t0 + k * 3 * 3600_000);

  return (
    <figure className="card timeline">
      <figcaption>
        <span className="title">Contact plan, next {hours} h</span>
        <span className="legend">
          <i className="sw sched" /> scheduled <i className="sw pred" /> predicted only
        </span>
      </figcaption>
      {stations.length === 0 ? (
        <p className="muted">No passes yet. Use "Plan passes" to run the predictor.</p>
      ) : (
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Ground station pass timeline">
          {ticks.map((t) => (
            <g key={t}>
              <line className="grid" x1={x(t)} x2={x(t)} y1={0} y2={H - AXIS} />
              <text className="tick" x={x(t)} y={H - 6} textAnchor="middle">
                {new Date(t).toISOString().slice(11, 16)}
              </text>
            </g>
          ))}
          <line className="now" x1={x(now.getTime())} x2={x(now.getTime())} y1={0} y2={H - AXIS} />
          {stations.map((s, r) => (
            <g key={s}>
              <text className="rowlabel" x={LABEL - 8} y={r * ROW + ROW / 2 + 4} textAnchor="end">{s}</text>
              {passes
                .filter((p) => p.station === s)
                .map((p) => {
                  const a = Math.max(new Date(p.aos).getTime(), t0);
                  const b = Math.min(new Date(p.los).getTime(), t1);
                  if (b <= a) return null;
                  return (
                    <rect
                      key={p.id}
                      className={p.scheduled ? "pass sched" : "pass pred"}
                      x={x(a)}
                      y={r * ROW + (ROW - BAR) / 2}
                      width={Math.max(x(b) - x(a), 2)}
                      height={BAR}
                      rx={4}
                      onMouseEnter={() => setHover(p)}
                      onMouseLeave={() => setHover(null)}
                    />
                  );
                })}
            </g>
          ))}
        </svg>
      )}
      <p className="tooltip">
        {hover
          ? `${hover.station}: AOS ${hover.aos.slice(11, 19)}Z, ${(hover.duration_s / 60).toFixed(1)} min, max el ${hover.max_el_deg.toFixed(1)} deg${hover.scheduled ? ", scheduled" : ""}`
          : "Hover a pass for details"}
      </p>
    </figure>
  );
}
