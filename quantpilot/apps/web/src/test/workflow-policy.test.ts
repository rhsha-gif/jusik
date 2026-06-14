import { describe, expect, it } from "vitest";
import { buildSafeWorkflowPolicy, RISK_PRESETS, THEME_CHIPS } from "@/lib/workflow-policy";

describe("workflow policy builder", () => {
  it("builds a mock, limit-only policy from user direction and risk preset", () => {
    const draft = buildSafeWorkflowPolicy({
      direction: "US AI semiconductor supply chain",
      riskPreset: "conservative",
    });

    expect(draft.text).toContain("US stock conservative risk");
    expect(draft.text).toContain("mock broker");
    expect(draft.text).toContain("limit orders only");
    expect(draft.text).toContain("Live trading disabled");
    expect(draft.text).toContain("Cash 30%");
    expect(draft.text).toContain("position 10%");
    expect(draft.themes).toEqual(["ai", "semiconductor"]);
  });

  it("detects explicit symbols and sectors for the intent-first policy text", () => {
    const draft = buildSafeWorkflowPolicy({
      direction: "AAA and MSFT technology sector",
      riskPreset: "moderate",
    });

    expect(draft.symbols).toEqual(["AAA", "MSFT"]);
    expect(draft.sectors).toEqual(["technology"]);
    expect(draft.text).toContain("Focus symbols: AAA, MSFT.");
    expect(draft.text).toContain("Focus sectors: technology.");
    expect(draft.text).toContain("mock broker");
    expect(draft.text).toContain("limit orders only");
    expect(draft.text).toContain("Live trading disabled");
  });

  it("exposes risk presets and theme chips for the intent-first UI", () => {
    expect(RISK_PRESETS.map((preset) => preset.value)).toEqual([
      "moderate",
      "conservative",
      "aggressive",
    ]);
    expect(THEME_CHIPS.map((chip) => chip.theme)).toContain("dividend");
  });
});
