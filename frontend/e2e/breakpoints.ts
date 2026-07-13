export type Breakpoint = { name: string; width: number; height: number };

/** The 9 viewports called out by the Premium Dark redesign spec: iPhone
 * 17 Pro Max size class, iPad Pro M4 portrait/landscape, and common
 * desktop/laptop widths. */
export const BREAKPOINTS: Breakpoint[] = [
  { name: "iphone-390x844", width: 390, height: 844 },
  { name: "iphone-430x932", width: 430, height: 932 },
  { name: "iphone-440x956", width: 440, height: 956 },
  { name: "ipad-portrait-834x1194", width: 834, height: 1194 },
  { name: "ipad-portrait-1024x1366", width: 1024, height: 1366 },
  { name: "ipad-landscape-1194x834", width: 1194, height: 834 },
  { name: "ipad-landscape-1366x1024", width: 1366, height: 1024 },
  { name: "desktop-1440x900", width: 1440, height: 900 },
  { name: "desktop-1920x1080", width: 1920, height: 1080 },
];

export function isPhoneBreakpoint(bp: Breakpoint): boolean {
  return bp.width <= 767;
}
