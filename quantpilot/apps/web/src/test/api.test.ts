import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiFetch, ApiError } from "@/lib/api";
import { clearActivity, getActivityEntries } from "@/lib/activity-log";

describe("apiFetch", () => {
  beforeEach(() => {
    clearActivity();
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns parsed JSON and records the activity entry on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ status: "ok" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const result = await apiFetch<{ status: string }>("/api/health");
    expect(result.status).toBe("ok");

    const entries = getActivityEntries();
    expect(entries).toHaveLength(1);
    expect(entries[0].path).toBe("/api/health");
    expect(entries[0].status).toBe(200);
    expect(entries[0].ok).toBe(true);
  });

  it("throws ApiError with the backend detail on HTTP error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: { error: "missing item: policy" } }), {
          status: 404,
        }),
      ),
    );

    await expect(apiFetch("/api/policies/confirm", { method: "POST", body: { policy_id: "x" } }))
      .rejects.toMatchObject({ status: 404, message: "missing item: policy" });

    const entries = getActivityEntries();
    expect(entries[0].ok).toBe(false);
    expect(entries[0].errorMessage).toContain("missing item");
  });

  it("throws an offline ApiError when the network request fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    try {
      await apiFetch("/api/health");
      expect.unreachable("should have thrown");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).isOffline).toBe(true);
      expect((error as ApiError).status).toBeNull();
    }

    expect(getActivityEntries()[0].status).toBeNull();
  });
});
