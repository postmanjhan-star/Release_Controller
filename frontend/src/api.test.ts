import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, apiText } from "./api";

describe("api", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns typed JSON and adds the JSON content type", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [1, 2] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await api<{ items: number[] }>("/items", { method: "POST" });

    expect(result.items).toEqual([1, 2]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/items",
      expect.objectContaining({
        headers: expect.objectContaining({
          "Content-Type": "application/json",
        }),
      }),
    );
  });

  it("lets the browser set the multipart boundary for FormData", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        new Response(JSON.stringify({ id: "scheduled" }), { status: 201 }),
      );
    vi.stubGlobal("fetch", fetchMock);
    const body = new FormData();
    body.set("payload", "{}");

    await api("/schedules", { method: "POST", body });

    expect(fetchMock).toHaveBeenCalledWith(
      "/schedules",
      expect.objectContaining({ body, headers: undefined }),
    );
  });

  it("returns null for a 204 response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 204 })),
    );

    await expect(
      api<null>("/items/1", { method: "DELETE" }),
    ).resolves.toBeNull();
  });

  it("uses API detail when a request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Release conflict" }), {
          status: 409,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(api("/releases")).rejects.toEqual(
      new ApiError("Release conflict", 409),
    );
  });

  it("falls back to the HTTP status for a non-JSON error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("Proxy error", { status: 502 })),
    );

    await expect(api("/releases")).rejects.toMatchObject({
      message: "Request failed (502)",
      status: 502,
    });
  });

  it("explains when an outdated backend returns the frontend HTML", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("<!doctype html><html></html>", {
          status: 200,
          headers: { "Content-Type": "text/html" },
        }),
      ),
    );

    await expect(api("/api/v1/notifications/recipients")).rejects.toMatchObject(
      {
        message:
          "Backend API returned HTML for /api/v1/notifications/recipients. Deploy or restart the backend, then verify the API proxy.",
        status: 200,
      },
    );
  });

  it("reports a successful non-JSON API response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("not json", { status: 200 })),
    );

    await expect(api("/items")).rejects.toMatchObject({
      message: "Backend API returned invalid JSON for /items.",
      status: 200,
    });
  });
});

describe("apiText", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns BPMN XML text", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(new Response("<definitions />", { status: 200 })),
    );

    await expect(apiText("/workflow")).resolves.toBe("<definitions />");
  });

  it("throws an ApiError when the BPMN definition is unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 404 })),
    );

    await expect(apiText("/workflow")).rejects.toMatchObject({ status: 404 });
  });
});
