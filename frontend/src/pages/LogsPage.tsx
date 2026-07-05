import { useEffect, useRef, useState } from "react";
import Card from "../components/Layout/Card";
import { api } from "../services/api";

type LogEvent = {
  id: number;
  timestamp: string;
  level: string;
  logger?: string;
  message: string;
  category?: string;
  [key: string]: unknown;
};

const LEVELS = ["INFO", "WARNING", "ERROR"];
const CATEGORIES = ["prediction", "trading", "risk", "api"];
const POLL_MS = 2500;
const MAX_EVENTS = 300;

function renderMeta(e: LogEvent): string {
  const parts: string[] = [];
  if (typeof e.symbol === "string") parts.push(e.symbol);
  if (typeof e.side === "string") parts.push(String(e.side));
  if (typeof e.direction === "string") parts.push(String(e.direction));
  if (typeof e.confidence === "number") parts.push(`conf ${e.confidence}%`);
  if (typeof e.endpoint === "string") parts.push(`${(e.method as string) || ""} ${e.endpoint}`.trim());
  if (typeof e.status_code === "number") parts.push(`status ${e.status_code}`);
  if (typeof e.latency_ms === "number") parts.push(`${e.latency_ms}ms`);
  if (typeof e.pnl === "number") parts.push(`pnl ${e.pnl}`);
  if (typeof e.reason === "string") parts.push(e.reason);
  if (typeof e.error === "string") parts.push(e.error);
  return parts.join(" · ");
}

export default function LogsPage() {
  const [events, setEvents] = useState<LogEvent[]>([]);
  const [level, setLevel] = useState<string | null>(null);
  const [category, setCategory] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const [connected, setConnected] = useState(true);
  const sinceIdRef = useRef(0);
  const streamRef = useRef<HTMLDivElement | null>(null);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  // Filters partition the ring buffer's id sequence differently, so
  // since_id continuity from one filter doesn't carry over to another -
  // switching level/category starts the feed fresh.
  useEffect(() => {
    let cancelled = false;
    sinceIdRef.current = 0;
    setEvents([]);

    async function tick() {
      try {
        const res = await api.logsRecent({
          sinceId: sinceIdRef.current || undefined,
          level: level || undefined,
          category: category || undefined,
          limit: 200,
        });
        if (cancelled) return;
        setConnected(true);
        const newEvents: LogEvent[] = res?.events || [];
        if (newEvents.length) {
          sinceIdRef.current = newEvents[newEvents.length - 1].id;
          setEvents((prev) => [...prev, ...newEvents].slice(-MAX_EVENTS));
        }
      } catch {
        if (!cancelled) setConnected(false);
      }
    }

    tick();
    const id = window.setInterval(() => {
      if (!pausedRef.current) tick();
    }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [level, category]);

  useEffect(() => {
    const el = streamRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [events]);

  return (
    <div className="page-grid">
      <Card
        title="System Logs"
        full
        right={
          <span className={`badge ${connected ? "badge-green" : "badge-red"}`}>
            {connected ? "Live" : "Disconnected"}
          </span>
        }
      >
        <div className="logs-filters">
          <div className="chip-row" style={{ marginTop: 0 }}>
            <button className={!level ? "tf-btn active" : "tf-btn"} onClick={() => setLevel(null)}>
              All Levels
            </button>
            {LEVELS.map((l) => (
              <button key={l} className={level === l ? "tf-btn active" : "tf-btn"} onClick={() => setLevel(l)}>
                {l}
              </button>
            ))}
          </div>
          <div className="chip-row">
            <button className={!category ? "tf-btn active" : "tf-btn"} onClick={() => setCategory(null)}>
              All Categories
            </button>
            {CATEGORIES.map((c) => (
              <button
                key={c}
                className={category === c ? "tf-btn active" : "tf-btn"}
                style={{ textTransform: "capitalize" }}
                onClick={() => setCategory(c)}
              >
                {c}
              </button>
            ))}
          </div>
          <button className="mini-btn" onClick={() => setPaused((p) => !p)}>
            {paused ? "Resume" : "Pause"}
          </button>
        </div>

        <div className="logs-stream" ref={streamRef}>
          {events.length === 0 && <p className="analytics-empty">Waiting for backend events…</p>}
          {events.map((e) => (
            <div key={e.id} className="log-row">
              <span className="log-time">{new Date(e.timestamp).toLocaleTimeString()}</span>
              <span className={`log-level log-${e.level?.toLowerCase()}`}>{e.level}</span>
              <span className="log-category">{e.category || "system"}</span>
              <span className="log-message">{e.message}</span>
              <span className="log-meta">{renderMeta(e)}</span>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
