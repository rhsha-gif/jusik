import { beforeEach, describe, expect, it } from "vitest";
import {
  clearWorkingPolicy,
  getWorkingPolicy,
  markWorkingPolicyConfirmed,
  setWorkingPolicy,
} from "@/lib/working-policy";
import type { UserPolicy } from "@/lib/types";

const FIXTURE_POLICY = {
  policy_id: "pol_test",
  user_id: "fixture-user",
  market: "KR_STOCK",
  risk_profile: "moderate",
  execution_mode: "approval_required",
  broker: "mock",
  preferred_symbols: ["AAA"],
  preferred_sectors: ["technology"],
  preferred_themes: ["ai"],
} as UserPolicy;

describe("working policy store", () => {
  beforeEach(() => {
    localStorage.clear();
    clearWorkingPolicy();
  });

  it("pins a parsed policy and marks confirmation", () => {
    setWorkingPolicy(FIXTURE_POLICY, false);
    expect(getWorkingPolicy()?.policyId).toBe("pol_test");
    expect(getWorkingPolicy()?.confirmed).toBe(false);

    markWorkingPolicyConfirmed("pol_test");
    expect(getWorkingPolicy()?.confirmed).toBe(true);
  });

  it("ignores confirmation for a different policy id", () => {
    setWorkingPolicy(FIXTURE_POLICY, false);
    markWorkingPolicyConfirmed("pol_other");
    expect(getWorkingPolicy()?.confirmed).toBe(false);
  });

  it("clears the pinned policy", () => {
    setWorkingPolicy(FIXTURE_POLICY, true);
    clearWorkingPolicy();
    expect(getWorkingPolicy()).toBeNull();
  });

  it("preserves focus metadata from the parsed policy", () => {
    setWorkingPolicy(FIXTURE_POLICY, false, { direction: "AAA technology" });

    expect(getWorkingPolicy()?.symbols).toEqual(["AAA"]);
    expect(getWorkingPolicy()?.sectors).toEqual(["technology"]);
    expect(getWorkingPolicy()?.themes).toEqual(["ai"]);
  });
});
