import { afterEach, describe, expect, it, vi } from "vitest";

import { searchResources, uploadResource } from "@/lib/api";

describe("resource API client", () => {
  afterEach(() => vi.restoreAllMocks());

  it("uploads multipart data without overriding the browser boundary", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          id: "resource-1",
          goal_id: "goal-1",
          knowledge_node_id: "node-1",
          title: "notes",
          source_type: "file",
          source_uri: null,
          original_filename: "notes.md",
          media_type: "text/markdown",
          sha256: "abc",
          size_bytes: 12,
          status: "ready",
          error: null,
          created_at: "2026-09-07T00:00:00Z",
          updated_at: "2026-09-07T00:00:00Z",
        }),
        { status: 201 },
      ),
    );

    const resource = await uploadResource(
      new File(["# BFS"], "notes.md", { type: "text/markdown" }),
      "goal-1",
      "node-1",
    );

    const request = fetchMock.mock.calls[0][1];
    expect(resource.id).toBe("resource-1");
    expect(request?.body).toBeInstanceOf(FormData);
    expect(request?.headers).toBeUndefined();
  });

  it("encodes hybrid-search filters", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("[]", { status: 200 }));

    await searchResources("BFS 队列", "goal/1", "node 1");

    expect(fetchMock.mock.calls[0][0]).toContain(
      "query=BFS+%E9%98%9F%E5%88%97&goal_id=goal%2F1&knowledge_node_id=node+1",
    );
  });
});
