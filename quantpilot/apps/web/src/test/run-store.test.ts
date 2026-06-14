import { beforeEach, describe, expect, it } from "vitest";
import {
  clearRunSnapshot,
  getRunSnapshot,
  patchRunSnapshot,
  setRunSnapshot,
} from "@/lib/run-store";

describe("run snapshot store", () => {
  beforeEach(() => {
    localStorage.clear();
    clearRunSnapshot();
  });

  it("persists and returns a run snapshot", () => {
    setRunSnapshot({
      policyId: "pol_test",
      policyVersion: 3,
      direction: "AI infrastructure",
      riskPreset: "moderate",
      symbols: ["AAA"],
      sectors: ["technology"],
      themes: ["ai"],
      generatedPolicyText: "KR stock moderate risk, mock broker, limit orders only.",
      source: "overview",
    });

    expect(getRunSnapshot()?.policyId).toBe("pol_test");
    expect(getRunSnapshot()?.riskPreset).toBe("moderate");
    expect(getRunSnapshot()?.symbols).toEqual(["AAA"]);
    expect(getRunSnapshot()?.sectors).toEqual(["technology"]);
    expect(getRunSnapshot()?.themes).toEqual(["ai"]);
    expect(localStorage.getItem("qp.runSnapshot")).toContain("pol_test");
  });

  it("patches the active policy snapshot without dropping existing fields", () => {
    setRunSnapshot({
      policyId: "pol_test",
      direction: "dividend quality",
      riskPreset: "conservative",
    });

    patchRunSnapshot({ policyId: "pol_test", source: "run" });

    expect(getRunSnapshot()?.direction).toBe("dividend quality");
    expect(getRunSnapshot()?.source).toBe("run");
  });

  it("clears the snapshot", () => {
    setRunSnapshot({ policyId: "pol_test" });
    clearRunSnapshot();

    expect(getRunSnapshot()).toBeNull();
    expect(localStorage.getItem("qp.runSnapshot")).toBeNull();
  });
});
