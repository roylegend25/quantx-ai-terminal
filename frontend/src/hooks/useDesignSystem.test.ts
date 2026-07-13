import { describe, expect, it, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useDesignSystem, DEFAULT_DESIGN_SYSTEM } from "./useDesignSystem";

describe("useDesignSystem", () => {
  beforeEach(() => {
    window.localStorage.clear();
    document.documentElement.removeAttribute("data-design");
  });

  afterEach(() => {
    document.documentElement.removeAttribute("data-design");
  });

  it("defaults to classic when nothing is stored", () => {
    const { result } = renderHook(() => useDesignSystem());
    expect(result.current.mode).toBe(DEFAULT_DESIGN_SYSTEM);
    expect(result.current.mode).toBe("classic");
  });

  it("applies the data-design attribute to the document element", () => {
    renderHook(() => useDesignSystem());
    expect(document.documentElement.getAttribute("data-design")).toBe("classic");
  });

  it("persists a mode change to localStorage and updates the attribute", () => {
    const { result } = renderHook(() => useDesignSystem());

    act(() => {
      result.current.setMode("premium");
    });

    expect(result.current.mode).toBe("premium");
    expect(window.localStorage.getItem("quantx_design_system")).toBe("premium");
    expect(document.documentElement.getAttribute("data-design")).toBe("premium");
  });

  it("reads a previously stored mode on mount", () => {
    window.localStorage.setItem("quantx_design_system", "premium");
    const { result } = renderHook(() => useDesignSystem());
    expect(result.current.mode).toBe("premium");
  });

  it("ignores a corrupted stored value and falls back to the default", () => {
    window.localStorage.setItem("quantx_design_system", "garbage");
    const { result } = renderHook(() => useDesignSystem());
    expect(result.current.mode).toBe(DEFAULT_DESIGN_SYSTEM);
  });
});
