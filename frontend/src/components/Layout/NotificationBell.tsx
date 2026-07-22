import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion, type PanInfo } from "framer-motion";
import {
  AlertTriangle,
  Bell,
  CheckCheck,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Inbox,
  Info,
  RefreshCw,
  X,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { api } from "../../services/api";
import { useMediaQuery } from "../../hooks/useMediaQuery";
import { Skeleton } from "../MLLab/widgets";
import LocalTime from "../LocalTime";

/** Topbar notification bell, merging two independent feeds: MLOps events
 *  (GET /api/ml/notifications) and indicator-eligibility/settings events
 *  (GET /api/indicators/notifications - Bot Settings Part 8: moved to
 *  shadow-only, recommended for reactivation, shadow performance
 *  deteriorated, insufficient data quality, config threshold changed,
 *  settings copied). Both feeds are merged by created_at into one list
 *  with one summed unread badge - not a second bell - and mark-read routes
 *  to whichever backend the item actually came from.
 *
 *  Below 768px the panel is a bottom sheet (backdrop, drag handle, safe-area
 *  padding, scroll-locked background); at 768px+ it's a bell-anchored
 *  popover. See the ".notif-*" rules in App.css. */

type Source = "ml" | "indicator";

type Item = {
  id: number;
  source: Source;
  event: string;
  severity: "info" | "success" | "warning" | "error";
  title: string;
  message: string | null;
  read: boolean;
  created_at: string | null;
};

type Status = "loading" | "ready" | "error";

const POLL_MS = 30_000;
const MOBILE_QUERY = "(max-width: 767px)";
// Beyond this many characters a message is clamped to 4 lines behind a
// "Show more" toggle - short messages never get an unnecessary toggle.
const LONG_MESSAGE_CHARS = 160;
// Drag-to-dismiss thresholds for the mobile sheet: far enough down, or
// fast enough, counts as an intentional swipe-away.
const DISMISS_OFFSET_PX = 120;
const DISMISS_VELOCITY = 800;

const SEVERITY_ICON: Record<Item["severity"], LucideIcon> = {
  info: Info,
  success: CheckCircle2,
  warning: AlertTriangle,
  error: XCircle,
};

type Props = {
  /** Current page key - closes the panel on navigation (App.tsx has no
   *  router/URL to key off, so the caller passes its NavKey state here). */
  routeKey?: string;
};

export default function NotificationBell({ routeKey }: Props) {
  const [items, setItems] = useState<Item[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<Status>("loading");
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const rootRef = useRef<HTMLDivElement | null>(null);
  const hasLoadedOnce = useRef(false);
  const isMobile = useMediaQuery(MOBILE_QUERY);
  const prefersReducedMotion = useReducedMotion();

  const refresh = useCallback(async () => {
    try {
      const [mlRes, indicatorRes] = await Promise.allSettled([api.mlNotifications(15), api.indicatorNotifications(15)]);
      const mlItems: Item[] =
        mlRes.status === "fulfilled" ? (mlRes.value?.notifications || []).map((n: any) => ({ ...n, source: "ml" as const })) : [];
      const indicatorItems: Item[] =
        indicatorRes.status === "fulfilled"
          ? (indicatorRes.value?.notifications || []).map((n: any) => ({ ...n, source: "indicator" as const }))
          : [];
      const merged = [...mlItems, ...indicatorItems].sort((a, b) => {
        const at = a.created_at ? new Date(a.created_at).getTime() : 0;
        const bt = b.created_at ? new Date(b.created_at).getTime() : 0;
        return bt - at;
      });
      const mlUnread = mlRes.status === "fulfilled" ? mlRes.value?.unread ?? 0 : 0;
      const indicatorUnread = indicatorRes.status === "fulfilled" ? indicatorRes.value?.unread ?? 0 : 0;
      setItems(merged.slice(0, 15));
      setUnread(mlUnread + indicatorUnread);
      // Only report a hard error if BOTH feeds failed - one feed being
      // down must not blank an otherwise-working panel.
      if (mlRes.status === "rejected" && indicatorRes.status === "rejected") {
        if (!hasLoadedOnce.current) setStatus("error");
      } else {
        setStatus("ready");
        hasLoadedOnce.current = true;
      }
    } catch {
      if (!hasLoadedOnce.current) setStatus("error");
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  // Close on: outside click (desktop popover only - mobile uses the
  // backdrop), Escape (both), navigating to another page.
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (isMobile) return;
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open, isMobile]);

  useEffect(() => {
    setOpen(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeKey]);

  // Lock the page behind the mobile sheet so only the notification list
  // scrolls, per the spec's "page must stay fixed while panel is open".
  useEffect(() => {
    if (!open || !isMobile) return;
    document.body.classList.add("notif-scroll-lock");
    return () => document.body.classList.remove("notif-scroll-lock");
  }, [open, isMobile]);

  async function markRead(item: Item) {
    try {
      if (item.source === "indicator") await api.indicatorNotificationRead(item.id);
      else await api.mlNotificationRead(item.id);
      await refresh();
    } catch {
      /* keep the row - retry on next poll */
    }
  }

  async function markAll() {
    try {
      await Promise.allSettled([api.mlNotificationsReadAll(), api.indicatorNotificationsReadAll()]);
      await refresh();
    } catch {
      /* ignore */
    }
  }

  function retry() {
    setStatus("loading");
    refresh();
  }

  function toggleExpanded(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function handleDragEnd(_: unknown, info: PanInfo) {
    if (info.offset.y > DISMISS_OFFSET_PX || info.velocity.y > DISMISS_VELOCITY) setOpen(false);
  }

  const spring = prefersReducedMotion ? { duration: 0.01 } : { type: "spring" as const, stiffness: 380, damping: 34 };
  const showSkeleton = status === "loading" && items.length === 0;
  const showError = status === "error" && items.length === 0;
  const showEmpty = status === "ready" && items.length === 0;

  return (
    <div className="notif-bell" ref={rootRef}>
      <button
        className="icon-btn"
        onClick={() => setOpen((v) => !v)}
        title="Notifications"
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <Bell size={18} />
        {unread > 0 && <span className="notif-badge">{unread > 99 ? "99+" : unread}</span>}
      </button>

      <AnimatePresence>
        {open && (
          <>
            {isMobile && (
              <motion.div
                className="notif-backdrop"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: prefersReducedMotion ? 0.01 : 0.18 }}
                onClick={() => setOpen(false)}
              />
            )}

            <motion.div
              className={isMobile ? "notif-sheet" : "notif-popover"}
              role="dialog"
              aria-modal={isMobile || undefined}
              aria-label="Notifications"
              initial={isMobile ? { y: "100%" } : { opacity: 0, scale: 0.95, y: -8 }}
              animate={isMobile ? { y: 0 } : { opacity: 1, scale: 1, y: 0 }}
              exit={isMobile ? { y: "100%" } : { opacity: 0, scale: 0.96, y: -6 }}
              transition={spring}
              drag={isMobile ? "y" : false}
              dragConstraints={{ top: 0, bottom: 0 }}
              dragElastic={{ top: 0, bottom: 0.5 }}
              onDragEnd={isMobile ? handleDragEnd : undefined}
            >
              {isMobile && <span className="notif-sheet-handle" aria-hidden="true" />}

              <div className="notif-panel-head">
                <div className="notif-panel-title">
                  <b>Notifications</b>
                  {unread > 0 && <span className="notif-count-pill">{unread > 99 ? "99+" : unread}</span>}
                </div>
                <div className="notif-panel-actions">
                  {unread > 0 && (
                    <button className="notif-readall" onClick={markAll} title="Mark all read">
                      <CheckCheck size={13} /> Mark all read
                    </button>
                  )}
                  {isMobile && (
                    <button className="icon-btn notif-close" onClick={() => setOpen(false)} title="Close" aria-label="Close">
                      <X size={18} />
                    </button>
                  )}
                </div>
              </div>

              <div className="notif-panel-body">
                {showSkeleton && (
                  <div className="notif-skeleton-list" aria-hidden="true">
                    {[0, 1, 2, 3].map((i) => (
                      <div className="notif-skeleton-row" key={i}>
                        <Skeleton h={28} w={28} style={{ borderRadius: 8, flex: "0 0 auto" }} />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <Skeleton h={11} w="60%" />
                          <Skeleton h={10} w="90%" style={{ marginTop: 6 }} />
                          <Skeleton h={9} w="35%" style={{ marginTop: 6 }} />
                        </div>
                      </div>
                    ))}
                  </div>
                )}

                {showError && (
                  <div className="mll-empty notif-state">
                    <AlertTriangle size={20} className="yellow" />
                    <b>Couldn't load notifications</b>
                    <p>The AI Lab notifications endpoint didn't respond. Check your connection and try again.</p>
                    <button className="notif-readall" onClick={retry}>
                      <RefreshCw size={13} /> Retry
                    </button>
                  </div>
                )}

                {showEmpty && (
                  <div className="mll-empty notif-state">
                    <Inbox size={20} className="cyan" />
                    <b>You're all caught up</b>
                    <p>Training, promotion, drift, and data-quality events will land here.</p>
                  </div>
                )}

                {items.length > 0 && (
                  <ul className="notif-list">
                    {items.map((n) => {
                      const Icon = SEVERITY_ICON[n.severity] ?? Info;
                      const isLong = (n.message?.length ?? 0) > LONG_MESSAGE_CHARS;
                      const isExpanded = expanded.has(n.id);
                      return (
                        <li key={`${n.source}-${n.id}`} className={`notif-item${n.read ? " read" : ""}`}>
                          <button
                            className="notif-item-main"
                            onClick={() => !n.read && markRead(n)}
                            title={n.read ? undefined : "Tap to mark read"}
                          >
                            <Icon size={16} className={`notif-item-icon sev-${n.severity}`} aria-hidden="true" />
                            <span className="notif-item-body">
                              <span className="notif-item-title-row">
                                <b>{n.title}</b>
                                {!n.read && <span className="notif-unread-dot" aria-label="Unread" />}
                              </span>
                              {n.message && (
                                <span className={`notif-item-desc${isExpanded ? " expanded" : ""}`}>{n.message}</span>
                              )}
                              <LocalTime value={n.created_at} variant="compact" label="Received" />
                            </span>
                          </button>
                          {isLong && (
                            <button
                              className="notif-show-more"
                              onClick={(e) => {
                                e.stopPropagation();
                                toggleExpanded(n.id);
                              }}
                            >
                              {isExpanded ? (
                                <>
                                  Show less <ChevronUp size={12} />
                                </>
                              ) : (
                                <>
                                  Show more <ChevronDown size={12} />
                                </>
                              )}
                            </button>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}
