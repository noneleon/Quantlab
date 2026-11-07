import { describe, expect, it } from "vitest";

import { MODULES } from "./modules";

const ROUTED = new Set(["/", "/data", "/backtests", "/strategies", "/editor", "/optimizations", "/portfolio", "/orders", "/risk"]);

describe("workstation modules (UI-008)", () => {
  it("lists all 18 modules from the master prompt exactly once", () => {
    expect(MODULES).toHaveLength(18);
    expect(new Set(MODULES.map((m) => m.id)).size).toBe(18);
  });

  it("marks a module available only if it has a real page", () => {
    for (const m of MODULES) {
      if (m.status === "available") expect(ROUTED.has(m.route), m.id).toBe(true);
      else expect(m.route, m.id).toBe(`/modules/${m.id}`);
    }
  });

  it("every non-available module names its phase and features", () => {
    for (const m of MODULES.filter((x) => x.status !== "available")) {
      expect(m.phase, m.id).toMatch(/^\d+$/);
      expect(m.features.length, m.id).toBeGreaterThan(0);
    }
  });

  it("live trading is never presented as available", () => {
    expect(MODULES.find((m) => m.id === "live")?.status).toBe("planned");
  });
});
