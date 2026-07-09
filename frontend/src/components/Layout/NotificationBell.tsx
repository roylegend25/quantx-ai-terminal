import { useCallback, useEffect, useRef, useState } from "react";
import { Bell, CheckCheck } from "lucide-react";
import { api } from "../../services/api";

/** Topbar notification bell for MLOps events. Self-contained: polls
 *  GET /api/ml/notifications every 30s, shows the unread badge, and a
 *  dropdown with the latest events + mark-read actions. */

type Item = {
  id: number;
  event: string;
  severity: "info" | "success" | "warning" | "error";
  title: string;
  message: string | null;
  read: boolean;
  created_at: string | null;
};

const POLL_MS = 30_000;

function ago(iso: string | null): string {
  if (!iso) return "";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function NotificationBell() {
  const [items, setItems] = useState<Item[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await api.mlNotifications(15);
      setItems(res?.notifications || []);
      setUnread(res?.unread ?? 0);
    } catch {
      /* backend restarting - badge just stays as it was */
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  // close on any outside click
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    return () => window.removeEventListener("mousedown", onDown);
  }, [open]);

  async function markRead(id: number) {
    try {
      await api.mlNotificationRead(id);
      await refresh();
    } catch { /* keep the row - retry on next poll */ }
  }

  async function markAll() {
    try {
      await api.mlNotificationsReadAll();
      await refresh();
    } catch { /* ignore */ }
  }

  return (
    <div className="notif-bell" ref={rootRef}>
      <button className="icon-btn" onClick={() => setOpen((v) => !v)} title="MLOps notifications">
        <Bell size={18} />
        {unread > 0 && <span className="notif-badge">{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && (
        <div className="notif-dropdown">
          <div className="notif-drop-head">
            <b>Notifications</b>
            {unread > 0 && (
              <button className="notif-readall" onClick={markAll} title="Mark all read">
                <CheckCheck size={13} /> Mark all read
              </button>
            )}
          </div>
          {items.length === 0 ? (
            <p className="notif-empty">No notifications yet — training, promotion, and drift events land here.</p>
          ) : (
            <div className="notif-drop-list">
              {items.map((n) => (
                <button
                  key={n.id}
                  className={`notif-drop-item${n.read ? " read" : ""}`}
                  onClick={() => !n.read && markRead(n.id)}
                  title={n.read ? undefined : "Click to mark read"}
                >
                  <span className={`mll-notif-dot sev-${n.severity}`} />
                  <span className="notif-drop-body">
                    <b>{n.title}</b>
                    {n.message && <i>{n.message}</i>}
                    <em>{ago(n.created_at)}</em>
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
