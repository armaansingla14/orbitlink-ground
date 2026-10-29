import { useCallback, useEffect, useState } from "react";
import { api, type Alert, type CommandRow, type EventRow, type Limit, type PassRow, type Point, type Status } from "./api";
import { AlertsPanel, CommandPanel, EventsPanel, StatTile } from "./components/Panels";
import { PassTimeline } from "./components/PassTimeline";
import { Sparkline } from "./components/Sparkline";

const TRENDS = [
  { param: "batt_v", title: "Battery voltage", unit: "V", digits: 2 },
  { param: "current_a", title: "Bus current", unit: "A", digits: 2 },
  { param: "temp_c", title: "Board temperature", unit: "°C", digits: 1 },
];

const LIMIT_STATUS: Record<string, "good" | "warning" | "critical"> = { nominal: "good", yellow: "warning", red: "critical" };

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [series, setSeries] = useState<Record<string, Point[]>>({});
  const [limits, setLimits] = useState<Limit[]>([]);
  const [passes, setPasses] = useState<PassRow[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [commands, setCommands] = useState<CommandRow[]>([]);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, l, p, a, c, e] = await Promise.all([
        api.status(), api.limits(), api.passes(), api.alerts(), api.commands(), api.events(),
      ]);
      const hist = await Promise.all(TRENDS.map((t) => api.history(t.param)));
      setStatus(s);
      setLimits(l);
      setPasses(p);
      setAlerts(a);
      setCommands(c);
      setEvents(e);
      setSeries(Object.fromEntries(TRENDS.map((t, i) => [t.param, hist[i]])));
      setError(null);
    } catch (err) {
      setError(String(err));
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  const worst = status
    ? Object.values(status.limit_states).reduce<"good" | "warning" | "critical">((acc, s) => {
        const st = LIMIT_STATUS[s];
        return st === "critical" || acc === "critical" ? "critical" : st === "warning" || acc === "warning" ? "warning" : "good";
      }, "good")
    : undefined;

  return (
    <div className="viz-root">
      <header>
        <h1>OrbitLink ground station</h1>
        <span className="muted">
          {status ? `${status.time.slice(0, 19)}Z, propagator ${status.propagator}` : "connecting..."}
        </span>
        <button onClick={() => api.planPasses(24).then(refresh)}>Plan passes (24 h)</button>
      </header>
      {error && <div className="banner">{error}</div>}

      <section className="tiles">
        <StatTile label="Spacecraft mode" value={status?.mode ?? "--"}
          status={status?.mode === "SAFE" ? "warning" : status?.mode ? "good" : undefined}
          note={status?.latest_hk_at ? `HK ${status.latest_hk_at.slice(11, 19)}Z` : "no telemetry yet"} />
        <StatTile label="Limit status" value={worst ? worst.toUpperCase() : "--"} status={worst} />
        <StatTile label="Open alerts" value={String(status?.open_alerts ?? "--")}
          status={status ? (status.open_alerts ? "warning" : "good") : undefined} />
        <StatTile label="Command queue" value={String(status?.queue_depth ?? "--")} note="queued + in flight" />
        <StatTile label="Contact" value={status?.contact ? status.contact.station : "none"}
          note={status?.contact ? `LOS ${status.contact.los.slice(11, 19)}Z` : "between passes"} />
      </section>

      <section className="trends">
        {TRENDS.map((t) => (
          <Sparkline key={t.param} title={t.title} unit={t.unit} digits={t.digits}
            points={series[t.param] ?? []} limit={limits.find((l) => l.param === t.param)} />
        ))}
      </section>

      <PassTimeline passes={passes} />

      <div className="grid2">
        <CommandPanel commands={commands} onChange={refresh} />
        <AlertsPanel alerts={alerts} onChange={refresh} />
      </div>
      <EventsPanel events={events} />
    </div>
  );
}
