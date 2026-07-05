import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  DEFAULT_THEME_ID,
  ROTATION_INTERVAL_MS,
  THEMES,
  recommendedTheme,
  sunriseSunsetTheme,
  systemAppearanceTheme,
  type RotationMode,
  type ThemeId,
} from "../lib/themes";

const THEME_KEY = "quantx_theme";
const ROTATION_KEY = "quantx_theme_rotation";

function readStored<T extends string>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  const v = window.localStorage.getItem(key);
  return (v as T) || fallback;
}

/** Applies the theme purely via a data-theme attribute + CSS custom
 * properties (see App.css [data-theme] blocks) - no per-component color
 * logic, so every themed surface (sidebar, cards, buttons, chart lines,
 * gauges) recolors instantly with zero re-render of unrelated components. */
export function useTheme() {
  const [themeId, setThemeIdState] = useState<ThemeId>(() => readStored(THEME_KEY, DEFAULT_THEME_ID));
  const [rotation, setRotationState] = useState<RotationMode>(() => readStored(ROTATION_KEY, "off"));
  const rotationIndexRef = useRef(0);

  const setThemeId = useCallback((id: ThemeId) => {
    setThemeIdState(id);
    window.localStorage.setItem(THEME_KEY, id);
  }, []);

  const setRotation = useCallback((mode: RotationMode) => {
    setRotationState(mode);
    window.localStorage.setItem(ROTATION_KEY, mode);
  }, []);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", themeId);
  }, [themeId]);

  // Fixed-interval rotation (15m/30m/1h): cycles through every theme in
  // order so "Live" rotation is a real, visible cycle, not a coin flip.
  useEffect(() => {
    const ms = ROTATION_INTERVAL_MS[rotation];
    if (!ms) return;
    const id = window.setInterval(() => {
      rotationIndexRef.current = (rotationIndexRef.current + 1) % THEMES.length;
      setThemeId(THEMES[rotationIndexRef.current].id);
    }, ms);
    return () => window.clearInterval(id);
  }, [rotation, setThemeId]);

  // Sunrise/sunset: local-hour heuristic, re-checked every minute so the
  // switch happens close to the actual hour boundary without a page reload.
  useEffect(() => {
    if (rotation !== "sunrise-sunset") return;
    const apply = () => setThemeId(sunriseSunsetTheme());
    apply();
    const id = window.setInterval(apply, 60_000);
    return () => window.clearInterval(id);
  }, [rotation, setThemeId]);

  // System appearance: reacts live to prefers-color-scheme changes.
  useEffect(() => {
    if (rotation !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const apply = () => setThemeId(systemAppearanceTheme(mq.matches));
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [rotation, setThemeId]);

  const recommended = useMemo(() => recommendedTheme(), []);

  return { themeId, setThemeId, rotation, setRotation, recommended };
}

export type UseThemeReturn = ReturnType<typeof useTheme>;
