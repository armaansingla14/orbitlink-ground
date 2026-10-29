// Typed client for the OrbitLink FastAPI backend.

export interface Limit {
  param: string;
  red_low: number | null;
  yellow_low: number | null;
  yellow_high: number | null;
  red_high: number | null;
  persistence: number;
  enabled: number | boolean;
}

export interface Status {
  time: string;
  latest_hk_at: string | null;
  mode: string | null;
  open_alerts: number;
  queue_depth: number;
  limit_states: Record<string, "nominal" | "yellow" | "red">;
  contact: PassRow | null;
  propagator: string;
}

export interface Point {
  t: string;
  value: number;
}

export interface PassRow {
  id: number;
  station: string;
  aos: string;
  los: string;
  tca: string;
  max_el_deg: number;
  duration_s: number;
  scheduled: number | boolean;
}

export interface Alert {
  id: number;
  created_at: string;
  severity: "info" | "warning" | "critical";
  source: string;
  message: string;
  acknowledged: number | boolean;
}

export interface CommandRow {
  id: number;
  created_at: string;
  name: string;
  priority: number;
  state: string;
  seq: number | null;
  attempts: number;
  acked_at: string | null;
  ack_status: number | null;
}

export interface EventRow {
  id: number;
  t: string;
  category: string;
  name: string;
  detail: string;
}

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`/api${path}`);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return (await r.json()) as T;
}

async function send<T>(method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`/api${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = await r.text();
    throw new Error(`${path}: HTTP ${r.status} ${detail}`);
  }
  return (await r.json()) as T;
}

export const api = {
  status: () => get<Status>("/status"),
  history: (param: string, limit = 120) => get<Point[]>(`/telemetry/history/${param}?limit=${limit}`),
  limits: () => get<Limit[]>("/limits"),
  passes: () => get<PassRow[]>("/passes?limit=300"),
  planPasses: (hours = 24) => send<{ predicted: number; scheduled: number }>("POST", `/passes/plan?hours=${hours}`),
  alerts: () => get<Alert[]>("/alerts?limit=50"),
  ackAlert: (id: number) => send<unknown>("POST", `/alerts/${id}/ack`),
  commands: () => get<CommandRow[]>("/commands?limit=50"),
  submitCommand: (name: string, args: Record<string, unknown>, priority: string) =>
    send<{ id: number }>("POST", "/commands", { name, args, priority }),
  events: () => get<EventRow[]>("/events?limit=60"),
};

export const PRIORITIES = ["CRITICAL", "HIGH", "NORMAL", "BULK"] as const;
