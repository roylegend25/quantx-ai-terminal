import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../services/api";

/** All AI-Model-Lab data + actions, self-contained so the lab can poll at
 *  its own cadence without touching the global 10s dashboard poll.
 *  Ambient refresh every 15s; job progress polls at 2s only while a job is
 *  actually queued/running. */

export type Algorithm = {
  id: string;
  label: string;
  family: string;
  available: boolean;
  unavailable_reason: string | null;
  supports_shap: boolean;
  default_hyperparameters: Record<string, number>;
  description?: string | null;
};

export type MLJob = {
  job_id: string;
  kind: string;
  algorithm: string;
  model_name: string;
  status: string;
  progress: Record<string, any> | null;
  result_model_id?: string | null;
  result?: Record<string, any> | null;
  error?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
};

export type RegistryModel = Record<string, any>;

export function useMLLab(active: boolean) {
  const [algorithms, setAlgorithms] = useState<Algorithm[]>([]);
  const [hpoSupported, setHpoSupported] = useState<string[]>([]);
  const [overview, setOverview] = useState<any>(null);
  const [jobs, setJobs] = useState<MLJob[]>([]);
  const [registry, setRegistry] = useState<RegistryModel[]>([]);
  const [compare, setCompare] = useState<any[]>([]);
  const [drift, setDrift] = useState<any>(null);
  const [driftHistory, setDriftHistory] = useState<any>(null);
  const [live, setLive] = useState<any>(null);
  const [inference, setInference] = useState<any>(null);
  const [history, setHistory] = useState<any>(null);
  const [settings, setSettings] = useState<any>(null);
  const [hpoResults, setHpoResults] = useState<any[]>([]);
  const [explanation, setExplanation] = useState<any>(null);
  const [modelDetail, setModelDetail] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const activeRef = useRef(active);
  activeRef.current = active;

  const refreshJobs = useCallback(async () => {
    try {
      const res = await api.mlJobs(undefined, 30);
      setJobs(res?.jobs || []);
      return res?.jobs || [];
    } catch {
      return [];
    }
  }, []);

  const refreshAll = useCallback(async () => {
    const [ov, cmp, reg, dr, drh, lv, inf, hist, st, hpo, expl] = await Promise.all([
      api.mlOverview().catch(() => null),
      api.mlCompare().catch(() => null),
      api.mlRegistry().catch(() => null),
      api.mlDrift().catch(() => null),
      api.mlDriftHistory(30).catch(() => null),
      api.mlLive().catch(() => null),
      api.mlInference(40).catch(() => null),
      api.mlPerformanceHistory(30).catch(() => null),
      api.mlSettings().catch(() => null),
      api.mlHpoResults().catch(() => null),
      api.mlExplain().catch(() => null),
    ]);
    if (!activeRef.current) return;
    if (ov) setOverview(ov);
    if (cmp) setCompare(cmp.rows || []);
    if (reg) setRegistry(reg.models || []);
    if (dr) setDrift(dr.drift || null);
    if (drh) setDriftHistory(drh);
    if (lv) setLive(lv);
    if (inf) setInference(inf);
    if (hist) setHistory(hist);
    if (st) setSettings(st.settings || null);
    if (hpo) setHpoResults(hpo.results || []);
    if (expl) setExplanation(expl);
    setError(ov ? null : "AI Lab API unreachable — check that the backend is running");
    setLoading(false);
  }, []);

  // initial load: catalog once + full bundle, then ambient 15s refresh
  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await api.mlAlgorithms();
        if (!cancelled) {
          setAlgorithms(res?.algorithms || []);
          setHpoSupported(res?.hpo_supported || []);
        }
      } catch {
        /* surfaced via bundle error state */
      }
      await refreshAll();
      await refreshJobs();
    })();
    const id = window.setInterval(() => {
      refreshAll();
    }, 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [active, refreshAll, refreshJobs]);

  // fast job polling only while something is queued/running
  const hasActiveJob = jobs.some((j) => j.status === "running" || j.status === "queued");
  useEffect(() => {
    if (!active || !hasActiveJob) return;
    const id = window.setInterval(async () => {
      const latest = await refreshJobs();
      const stillActive = latest.some((j: MLJob) => j.status === "running" || j.status === "queued");
      // a job just finished: pull fresh registry/compare/overview immediately
      if (!stillActive) refreshAll();
    }, 2_000);
    return () => window.clearInterval(id);
  }, [active, hasActiveJob, refreshJobs, refreshAll]);

  const train = useCallback(async (body: Record<string, unknown>) => {
    const res = await api.mlTrain(body);
    await refreshJobs();
    return res;
  }, [refreshJobs]);

  const cancelJob = useCallback(async (jobId: string) => {
    const res = await api.mlCancelJob(jobId);
    await refreshJobs();
    return res;
  }, [refreshJobs]);

  const promote = useCallback(async (modelId: string, force = false) => {
    const res = await api.mlPromote(modelId, force);
    await refreshAll();
    return res;
  }, [refreshAll]);

  const rollback = useCallback(async (modelName: string) => {
    const res = await api.mlRollback(modelName);
    await refreshAll();
    return res;
  }, [refreshAll]);

  const deleteModel = useCallback(async (modelId: string) => {
    const res = await api.mlRegistryDelete(modelId);
    await refreshAll();
    return res;
  }, [refreshAll]);

  const startHpo = useCallback(async (body: Record<string, unknown>) => {
    const res = await api.mlHpoStart(body);
    await refreshJobs();
    return res;
  }, [refreshJobs]);

  const scanDrift = useCallback(async () => {
    const res = await api.mlDriftScan();
    await refreshAll();
    return res;
  }, [refreshAll]);

  const saveSettings = useCallback(async (patch: Record<string, unknown>) => {
    const res = await api.mlSettingsUpdate(patch);
    setSettings(res?.settings || null);
    return res;
  }, []);

  const retrainNow = useCallback(async () => {
    const res = await api.mlRetrainNow();
    await refreshJobs();
    return res;
  }, [refreshJobs]);

  const loadModelDetail = useCallback(async (modelId: string) => {
    setModelDetail({ loading: true, model_id: modelId });
    try {
      const res = await api.mlRegistryDetail(modelId);
      setModelDetail({ loading: false, ...res });
      return res;
    } catch (e: any) {
      setModelDetail({ loading: false, error: e?.message || "Failed to load model detail" });
      return null;
    }
  }, []);

  const explainPrediction = useCallback(async (predictionId?: number) => {
    const res = await api.mlExplain(predictionId).catch(() => null);
    if (res) setExplanation(res);
    return res;
  }, []);

  return {
    algorithms, hpoSupported, overview, jobs, registry, compare, drift, driftHistory,
    live, inference, history, settings, hpoResults, explanation, modelDetail,
    loading, error, hasActiveJob,
    train, cancelJob, promote, rollback, deleteModel, startHpo, scanDrift,
    saveSettings, retrainNow, loadModelDetail, explainPrediction, refreshAll,
  };
}

export type MLLabData = ReturnType<typeof useMLLab>;
