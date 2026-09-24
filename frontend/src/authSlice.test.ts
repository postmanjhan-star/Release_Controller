import { configureStore } from "@reduxjs/toolkit";
import { afterEach, describe, expect, it, vi } from "vitest";
import authReducer, { loadSession, logout } from "./authSlice";

function createStore() {
  return configureStore({ reducer: { auth: authReducer } });
}

describe("authSlice", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads the current Gitea-backed session", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            enabled: true,
            configured: true,
            authenticated: true,
            user: {
              login: "jhan",
              full_name: "Jhan",
              email: null,
              avatar_url: null,
              is_admin: false,
            },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );
    const store = createStore();

    await store.dispatch(loadSession());

    expect(store.getState().auth).toMatchObject({
      status: "ready",
      authenticated: true,
      user: { login: "jhan" },
    });
  });

  it("clears identity after logout", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 204 })),
    );
    const store = createStore();

    await store.dispatch(logout());

    expect(store.getState().auth).toMatchObject({
      status: "ready",
      authenticated: false,
      user: null,
    });
  });

  it("keeps API failures in Redux state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Session unavailable" }), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const store = createStore();

    await store.dispatch(loadSession());

    expect(store.getState().auth).toMatchObject({
      status: "error",
      authenticated: false,
      error: "Session unavailable",
    });
  });

  it("keeps logout failures in Redux state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Logout failed" }), {
          status: 500,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );
    const store = createStore();

    await store.dispatch(logout());

    expect(store.getState().auth.error).toBe("Logout failed");
  });
});
