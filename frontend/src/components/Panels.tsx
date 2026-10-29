import { useState } from "react";
import { PRIORITIES, api, type Alert, type CommandRow, type EventRow } from "../api";

const SEVERITY_ICON: Record<string, string> = { critical: "▲", warning: "◆", info: "●" };

export function StatTile({ label, value, status, note }: { label: string; value: string; status?: "good" | "warning" | "critical"; note?: string }) {
  return (
    <div className="card tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {status && (
        <div className={`status ${status}`}>
          <span aria-hidden>{SEVERITY_ICON[status === "good" ? "info" : status]}</span> {status}
        </div>
      )}
      {note && <div className="muted small">{note}</div>}
    </div>
  );
}

export function AlertsPanel({ alerts, onChange }: { alerts: Alert[]; onChange: () => void }) {
  return (
    <section className="card panel">
      <h2>Alerts</h2>
      <table>
        <thead>
          <tr><th>Time (UTC)</th><th>Severity</th><th>Source</th><th>Message</th><th /></tr>
        </thead>
        <tbody>
          {alerts.map((a) => (
            <tr key={a.id} className={a.acknowledged ? "acked" : ""}>
              <td className="mono">{a.created_at.slice(5, 19).replace("T", " ")}</td>
              <td><span className={`status ${a.severity}`}><span aria-hidden>{SEVERITY_ICON[a.severity]}</span> {a.severity}</span></td>
              <td>{a.source}</td>
              <td>{a.message}</td>
              <td>{!a.acknowledged && <button onClick={() => api.ackAlert(a.id).then(onChange)}>Ack</button>}</td>
            </tr>
          ))}
          {alerts.length === 0 && <tr><td colSpan={5} className="muted">No alerts</td></tr>}
        </tbody>
      </table>
    </section>
  );
}

const COMMANDS: Record<string, { label: string; args: () => Record<string, unknown> }> = {
  noop: { label: "NOOP", args: () => ({}) },
  ping: { label: "PING", args: () => ({ token: Math.floor(Math.random() * 1e9) }) },
  set_mode_safe: { label: "SET_MODE SAFE", args: () => ({ mode: "SAFE" }) },
  set_mode_nominal: { label: "SET_MODE NOMINAL", args: () => ({ mode: "NOMINAL" }) },
  set_mode_payload: { label: "SET_MODE PAYLOAD", args: () => ({ mode: "PAYLOAD" }) },
  capture: { label: "CAPTURE x4", args: () => ({ frames: 4 }) },
};

export function CommandPanel({ commands, onChange }: { commands: CommandRow[]; onChange: () => void }) {
  const [cmd, setCmd] = useState("noop");
  const [prio, setPrio] = useState<string>("NORMAL");
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setErr(null);
    const name = cmd.startsWith("set_mode") ? "set_mode" : cmd;
    try {
      await api.submitCommand(name, COMMANDS[cmd].args(), prio);
      onChange();
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <section className="card panel">
      <h2>Command queue</h2>
      <div className="row">
        <select value={cmd} onChange={(e) => setCmd(e.target.value)} aria-label="Command">
          {Object.entries(COMMANDS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
        </select>
        <select value={prio} onChange={(e) => setPrio(e.target.value)} aria-label="Priority">
          {PRIORITIES.map((p) => <option key={p}>{p}</option>)}
        </select>
        <button onClick={submit}>Queue</button>
        {err && <span className="error">{err}</span>}
      </div>
      <table>
        <thead>
          <tr><th>#</th><th>Command</th><th>Priority</th><th>State</th><th>Seq</th><th>Attempts</th><th>Acked (UTC)</th></tr>
        </thead>
        <tbody>
          {commands.map((c) => (
            <tr key={c.id}>
              <td className="mono">{c.id}</td>
              <td>{c.name}</td>
              <td>{PRIORITIES[c.priority]}</td>
              <td><span className={`chip ${c.state}`}>{c.state}</span></td>
              <td className="mono">{c.seq ?? "-"}</td>
              <td className="mono">{c.attempts}</td>
              <td className="mono">{c.acked_at ? c.acked_at.slice(11, 19) : "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

export function EventsPanel({ events }: { events: EventRow[] }) {
  return (
    <section className="card panel">
      <h2>Event log</h2>
      <ul className="events">
        {events.map((e) => (
          <li key={e.id}>
            <span className="mono muted">{e.t.slice(11, 19)}</span> <b>{e.category}</b> {e.name}{" "}
            <span className="muted">{e.detail}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
