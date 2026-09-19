import { afterEach, describe, expect, it, vi } from "vitest";

import { fetchReliabilityReports, runReliabilitySuite } from "@/lib/api";

describe("Agent Reliability API", () => {
  afterEach(() => vi.restoreAllMocks());

  it("preserves pass-k, safety, and replay manifest fields", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([{
        id: "report-1",
        metrics: { pass_at_k: 1, pass_power_k: 0.5, safety_rate: 1 },
        trials: [{ manifest: { manifest_hash: "abc", seed: 21 } }],
      }])),
    );

    const reports = await fetchReliabilityReports();
    expect(reports[0].metrics.pass_power_k).toBe(0.5);
    expect(reports[0].trials[0].manifest.manifest_hash).toBe("abc");
  });

  it("posts the seeded suite without mutating its contract", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ id: "report-1" })),
    );
    const payload = {
      scenarios: [{ id: "scenario" }],
      trials_per_scenario: 3,
      base_seed: 17,
      environment: { dataset_version: "1.0.0" },
    };

    await runReliabilitySuite(payload);

    expect(fetchMock.mock.calls[0][0]).toContain("/agent/reliability/run");
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual(payload);
  });
});
