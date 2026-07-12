import { useCallback, useEffect, useState } from "react";
import { api } from "../services/api";

const POLL_MS = 15000;

/** Admin-only server trading config (Phase 24's GET /api/admin/server-config),
 *  extracted from ServerTradingControlCard.tsx so any Binance Live tab that
 *  needs to show/edit server-protected settings (Bot Settings, Risk
 *  Management) can reuse the exact same fetch/poll/forbidden handling
 *  instead of re-implementing it. Renders forbidden=true (never throws) for
 *  non-admin tokens - the endpoint answers 403 and callers should render
 *  nothing/disabled rather than surface the error. */
export function useAdminServerConfig(pollMs = POLL_MS) {
  const [config, setConfig] = useState<any>(null);
  const [forbidden, setForbidden] = useState(false);

  const reload = useCallback(async () => {
    try {
      setConfig(await api.adminServerConfig());
      setForbidden(false);
    } catch (e: any) {
      if (String(e?.message || "").includes("Admin")) setForbidden(true);
    }
  }, []);

  useEffect(() => {
    reload();
    const id = window.setInterval(reload, pollMs);
    return () => window.clearInterval(id);
  }, [reload, pollMs]);

  return { config, forbidden, reload };
}
