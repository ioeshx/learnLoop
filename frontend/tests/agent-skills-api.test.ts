import { afterEach, describe, expect, it, vi } from "vitest";

import {
  fetchAgentSkills,
  reviewAgentSkill,
  updateAgentSkillStatus,
} from "@/lib/api";

describe("Agent Skill Library API", () => {
  afterEach(() => vi.restoreAllMocks());

  it("preserves Skill version and source Run provenance", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify([
          {
            id: "skill-1",
            version: 2,
            status: "candidate",
            source_run_ids: ["run-1", "run-2"],
          },
        ]),
      ),
    );

    const skills = await fetchAgentSkills();

    expect(skills[0].version).toBe(2);
    expect(skills[0].source_run_ids).toEqual(["run-1", "run-2"]);
  });

  it("sends optimistic version guards for review and disable", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async () =>
        new Response(JSON.stringify({ id: "skill-1" })),
      );

    await reviewAgentSkill("skill-1", "publish", 3, "verified");
    await updateAgentSkillStatus("skill-1", "disabled", 3, "kill switch");

    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toMatchObject({
      decision: "publish",
      expected_version: 3,
    });
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toMatchObject({
      status: "disabled",
      expected_version: 3,
    });
  });
});
