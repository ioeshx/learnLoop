import { afterEach, describe, expect, it, vi } from "vitest";

import {
  approveMemory,
  correctMemory,
  deleteMemory,
  fetchMemories,
} from "@/lib/api";

describe("Agent Memory API client", () => {
  afterEach(() => vi.restoreAllMocks());

  it("uses governance endpoints for browse, approval, correction, and delete", async () => {
    const memory = {
      id: "memory-1",
      status: "candidate",
      kind: "semantic",
      evidence: [],
      revisions: [],
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify([memory])))
      .mockResolvedValueOnce(new Response(JSON.stringify(memory)))
      .mockResolvedValueOnce(new Response(JSON.stringify(memory)))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    await fetchMemories("candidate");
    await approveMemory("memory-1");
    await correctMemory("memory-1", "新偏好", "用户更正");
    await deleteMemory("memory-1");

    expect(fetchMock.mock.calls[0][0]).toContain(
      "/memories?memory_status=candidate",
    );
    expect(fetchMock.mock.calls[1][0]).toContain("/memories/memory-1/approve");
    expect(fetchMock.mock.calls[1][1]?.method).toBe("POST");
    expect(fetchMock.mock.calls[2][1]?.body).toBe(
      JSON.stringify({ content: "新偏好", reason: "用户更正" }),
    );
    expect(fetchMock.mock.calls[3][1]?.method).toBe("DELETE");
  });
});
