import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchBanditDecisions,
  fetchFailureClusters,
  fetchOptimizationRewards,
  fetchPolicyExperiments,
  fetchPolicyVersions,
} from "@/lib/api";

describe("Agent Policy Lab API", () => {
  afterEach(() => vi.restoreAllMocks());

  it("preserves version, propensity, delayed reward, and holdout gates", async () => {
    const responses = [
      [{ id: "policy-1", version: 2, status: "active", algorithm: "linucb" }],
      [
        {
          id: "reward-1",
          status: "mature",
          hard_gate_passed: true,
          optimization_score: 0.82,
        },
      ],
      [
        {
          id: "decision-1",
          policy_version: 2,
          arm_id: "socratic_prompt",
          propensity: 0.9625,
        },
      ],
      [
        {
          manifest: { id: "experiment-1", dataset_version: "replay-1.0.0" },
          split: "holdout",
          effective_sample_size: 21,
          promotable: true,
        },
      ],
      [
        {
          signature: "a".repeat(64),
          problem_category: "verification_failure",
          count: 3,
        },
      ],
    ];
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify(responses.shift())),
    );

    const policies = await fetchPolicyVersions();
    const rewards = await fetchOptimizationRewards();
    const decisions = await fetchBanditDecisions();
    const experiments = await fetchPolicyExperiments();
    const clusters = await fetchFailureClusters();

    expect(policies[0].version).toBe(2);
    expect(rewards[0].hard_gate_passed).toBe(true);
    expect(decisions[0].propensity).toBe(0.9625);
    expect(experiments[0].split).toBe("holdout");
    expect(experiments[0].promotable).toBe(true);
    expect(clusters[0].count).toBe(3);
  });
});
