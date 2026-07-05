/**
 * All 10 themes are dark-background variants with a different accent
 * palette - QuantX never goes pure-white or pure-black (see App.css :root),
 * so "theme" here means "which accent gradient recolors the whole app",
 * not a light/dark swap. Each theme overrides a small set of CSS custom
 * properties on :root (see the [data-theme] blocks in App.css); everything
 * downstream (sidebar, cards, buttons, glows, chart lines, gauges) reads
 * those properties, so switching themes recolors instantly with no reload
 * and no per-component logic.
 */
import type { ComponentType } from "react";
import {
  Sparkles,
  Moon,
  Zap,
  Waves,
  Leaf,
  Gem,
  Flame,
  Snowflake,
  Mountain,
  Sun,
} from "lucide-react";

export type ThemeId =
  | "quantx-default"
  | "apple-aurora"
  | "midnight-purple"
  | "cyber-neon"
  | "ocean-blue"
  | "emerald"
  | "ruby"
  | "sunset"
  | "ice"
  | "graphite";

export type ThemeDef = {
  id: ThemeId;
  name: string;
  tagline: string;
  icon: ComponentType<{ size?: number }>;
  /** [accent, accent-2, accent-3] - drives --c-purple/--c-blue/--c-cyan. */
  colors: [string, string, string];
};

export const THEMES: ThemeDef[] = [
  { id: "quantx-default", name: "Default QuantX", tagline: "The original terminal look", icon: Sparkles, colors: ["#7c5cff", "#00a8ff", "#00f5d4"] },
  { id: "apple-aurora", name: "Apple Aurora", tagline: "Soft, vibrant, presentation-ready", icon: Sun, colors: ["#a78bff", "#5ec8ff", "#7cf0d1"] },
  { id: "midnight-purple", name: "Midnight Purple", tagline: "Easy on the eyes for long sessions", icon: Moon, colors: ["#6d4aff", "#4338ca", "#a78bfa"] },
  { id: "cyber-neon", name: "Cyber Neon", tagline: "Maximum contrast, maximum energy", icon: Zap, colors: ["#ff2ec4", "#7c3aed", "#00e5ff"] },
  { id: "ocean-blue", name: "Ocean Blue", tagline: "Calm, focused, analytical", icon: Waves, colors: ["#2196f3", "#0ea5e9", "#4dd0e1"] },
  { id: "emerald", name: "Emerald", tagline: "Fresh, profitable, grounded", icon: Leaf, colors: ["#10b981", "#059669", "#34d399"] },
  { id: "ruby", name: "Ruby", tagline: "Bold, warm, high-energy", icon: Gem, colors: ["#f43f5e", "#dc2626", "#fb7185"] },
  { id: "sunset", name: "Sunset", tagline: "Warm gradient, golden hour", icon: Flame, colors: ["#f97316", "#fb923c", "#fbbf24"] },
  { id: "ice", name: "Ice", tagline: "Cool, pale, crisp clarity", icon: Snowflake, colors: ["#67e8f9", "#38bdf8", "#a5f3fc"] },
  { id: "graphite", name: "Graphite", tagline: "Desaturated and distraction-free", icon: Mountain, colors: ["#94a3b8", "#64748b", "#cbd5e1"] },
];

export const THEME_MAP: Record<ThemeId, ThemeDef> = Object.fromEntries(
  THEMES.map((t) => [t.id, t])
) as Record<ThemeId, ThemeDef>;

export const DEFAULT_THEME_ID: ThemeId = "quantx-default";

export type RotationMode = "off" | "15min" | "30min" | "1hour" | "sunrise-sunset" | "system";

export const ROTATION_OPTIONS: { id: RotationMode; label: string }[] = [
  { id: "off", label: "Off" },
  { id: "15min", label: "15 min" },
  { id: "30min", label: "30 min" },
  { id: "1hour", label: "1 hour" },
  { id: "sunrise-sunset", label: "Sunrise / Sunset" },
  { id: "system", label: "System" },
];

export const ROTATION_INTERVAL_MS: Partial<Record<RotationMode, number>> = {
  "15min": 15 * 60_000,
  "30min": 30 * 60_000,
  "1hour": 60 * 60_000,
};

/** Local-hour heuristic, not real geolocation-based sunrise/sunset - keeps
 * the feature genuinely useful without prompting for location access. */
export function sunriseSunsetTheme(now: Date = new Date()): ThemeId {
  const hour = now.getHours();
  return hour >= 20 || hour < 6 ? "midnight-purple" : "apple-aurora";
}

export function systemAppearanceTheme(prefersLight: boolean): ThemeId {
  return prefersLight ? "ice" : "graphite";
}

/** The single "Recommended For You" pick (Design 8) - a light, honest
 * heuristic (time of day), not a trained model; the curated, user-chosen
 * presets below (Design 10) cover the other named use-cases explicitly. */
export function recommendedTheme(now: Date = new Date()): { theme: ThemeId; reason: string } {
  const hour = now.getHours();
  if (hour >= 21 || hour < 6) {
    return { theme: "midnight-purple", reason: "Trading at night - easier on the eyes for long sessions." };
  }
  if (hour >= 6 && hour < 9) {
    return { theme: "sunset", reason: "Early session - a warm palette to ease into the day." };
  }
  return { theme: "quantx-default", reason: "Standard trading hours - the balanced, high-contrast default." };
}

export type ContextPreset = {
  id: string;
  name: string;
  description: string;
  icon: ComponentType<{ size?: number }>;
  theme: ThemeId;
};

/** "Themes For You" (Design 10) - user-picked contextual presets. */
export const CONTEXT_PRESETS: ContextPreset[] = [
  { id: "trading-focus", name: "Trading Focus", description: "Maximum contrast for fast-moving charts", icon: Zap, theme: "cyber-neon" },
  { id: "night-owl", name: "Night Owl", description: "Soft dark tones for late-night sessions", icon: Moon, theme: "midnight-purple" },
  { id: "minimal", name: "Minimal", description: "Desaturated and distraction-free", icon: Mountain, theme: "graphite" },
  { id: "research", name: "Research", description: "Calm, focused tones for deep analysis", icon: Waves, theme: "ocean-blue" },
  { id: "presentation", name: "Presentation", description: "Vibrant and polished for demos", icon: Sun, theme: "apple-aurora" },
];
