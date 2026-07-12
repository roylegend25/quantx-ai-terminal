import "@testing-library/jest-dom/vitest";

// jsdom doesn't implement matchMedia - stub it for hooks like useMediaQuery
// (e.g. AutoCardTable's responsive card/table switch) that call it eagerly.
if (typeof window !== "undefined" && !window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList;
}
