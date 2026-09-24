import { configureStore } from "@reduxjs/toolkit";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Provider } from "react-redux";
import { App } from "./App";
import authReducer, { type AuthState } from "./authSlice";

const initialize = vi.fn().mockResolvedValue(undefined);
const teardown = vi.fn();

vi.mock("./controller", () => ({ initialize, teardown }));

const authenticatedState: AuthState = {
  enabled: true,
  configured: true,
  authenticated: true,
  user: {
    login: "jhan",
    full_name: "Jhan",
    email: "jhan@example.com",
    avatar_url: null,
    is_admin: false,
  },
  status: "ready",
  error: null,
};

function renderApp(auth: AuthState = authenticatedState) {
  const store = configureStore({
    reducer: { auth: authReducer },
    preloadedState: { auth },
  });
  return render(
    <Provider store={store}>
      <App />
    </Provider>,
  );
}

describe("App", () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    vi.unstubAllGlobals();
    window.history.replaceState({}, "", "/");
  });

  it("renders the release workflow, leaving the components to the controller", async () => {
    const { container } = renderApp();

    expect(
      screen.getByRole("heading", { name: "Choose independent source builds" }),
    ).toBeVisible();
    // Which components exist is a property of the selected project, so the shell
    // renders the project picker and an empty grid rather than a fixed pair.
    expect(container.querySelector("#project-selector")).toBeInstanceOf(
      HTMLSelectElement,
    );
    expect(container.querySelector("#component-grid")).toBeInstanceOf(
      HTMLDivElement,
    );
    expect(
      screen.getByRole("button", { name: "Release selected components" }),
    ).toBeDisabled();
    expect(
      await screen.findByRole("heading", { name: "Release bundles" }),
    ).toBeVisible();
    expect(initialize).toHaveBeenCalledOnce();
  });

  it("renders every history navigation destination", () => {
    const { container } = renderApp();

    for (const name of [
      "Releases",
      "Workflows",
      "Email recipients",
      "Projects",
    ]) {
      expect(screen.getByRole("link", { name })).toBeVisible();
    }
    expect(
      screen.queryByRole("link", { name: "Connections" }),
    ).not.toBeInTheDocument();
    expect(
      container.querySelector('[data-settings-view="connections"]'),
    ).toHaveTextContent("Connections");
    expect(
      screen.queryByRole("link", { name: "Deployments" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Schedules" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: "Legacy approvals" }),
    ).not.toBeInTheDocument();
    expect(container.querySelector("#workflow-kind-filter")).toBeInstanceOf(
      HTMLSelectElement,
    );
    expect(container.querySelector("#workflow-status-filter")).toBeInstanceOf(
      HTMLSelectElement,
    );
    expect(
      container.querySelector("#pending-schedules-filter"),
    ).toHaveTextContent("Pending schedules");
  });

  it("renders separate publish controls and service indicators", () => {
    const { container } = renderApp();

    expect(
      screen.getByText(`Release Worker v${__APP_VERSION__}`),
    ).toBeVisible();
    expect(screen.getByText("Checking Drone")).toBeVisible();
    expect(screen.getByText("Checking Gitea")).toBeVisible();
    expect(screen.getByText("@jhan")).toBeVisible();
    expect(container.querySelector("#publish-dialog")).toBeInstanceOf(
      HTMLDialogElement,
    );
    expect(
      container.querySelector('#publish-form [name="version"]'),
    ).toHaveAttribute("pattern");
    expect(
      container.querySelector('#publish-form [name="release_notes"]'),
    ).toBeInstanceOf(HTMLTextAreaElement);
  });

  it("renders the settings editors without ever offering to show a token", () => {
    const { container } = renderApp();

    for (const id of [
      "#project-dialog",
      "#component-dialog",
      "#connection-dialog",
    ]) {
      expect(container.querySelector(id)).toBeInstanceOf(HTMLDialogElement);
    }
    // A stored token is never sent back to the page, so the field is only ever
    // an empty password box — not a text input pre-filled with the secret.
    const token = container.querySelector('#connection-form [name="token"]');
    expect(token).toHaveAttribute("type", "password");
    expect(token).toHaveValue("");
    expect(
      container.querySelector(
        '#component-form [name="promote_target_override"]',
      ),
    ).toBeInstanceOf(HTMLInputElement);
  });

  it("renders the Gitea login action for an anonymous user", () => {
    renderApp({
      ...authenticatedState,
      authenticated: false,
      user: null,
    });

    expect(
      screen.getByRole("heading", { name: "Sign in to continue" }),
    ).toBeVisible();
    expect(
      screen.getByRole("link", { name: "Sign in with Gitea" }),
    ).toHaveAttribute("href", "/api/v1/auth/login");
    expect(initialize).not.toHaveBeenCalled();
  });

  it("explains missing OAuth configuration and callback errors", () => {
    window.history.replaceState({}, "", "/?auth_error=Access%20denied");
    renderApp({
      ...authenticatedState,
      configured: false,
      authenticated: false,
      user: null,
      error: null,
    });

    expect(screen.getByRole("alert")).toHaveTextContent("Access denied");
    expect(screen.getByRole("status")).toHaveTextContent(
      "Gitea OAuth is not configured",
    );
    expect(
      screen.queryByRole("link", { name: "Sign in with Gitea" }),
    ).not.toBeInTheDocument();
  });

  it("shows session loading and Redux errors", () => {
    const { rerender } = renderApp({
      ...authenticatedState,
      status: "loading",
      authenticated: false,
      user: null,
    });
    expect(
      screen.getByRole("link", { name: "Checking session…" }),
    ).toBeVisible();

    const errorStore = configureStore({
      reducer: { auth: authReducer },
      preloadedState: {
        auth: {
          ...authenticatedState,
          status: "error" as const,
          authenticated: false,
          user: null,
          error: "Session lookup failed",
        },
      },
    });
    rerender(
      <Provider store={errorStore}>
        <App />
      </Provider>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Session lookup failed",
    );
  });

  it("uses the login when full name is absent and signs out", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 204 })),
    );
    renderApp({
      ...authenticatedState,
      user: { ...authenticatedState.user!, full_name: null },
    });
    expect(screen.getAllByText("jhan")[0]).toBeVisible();

    screen.getByRole("button", { name: "Sign out" }).click();

    expect(
      await screen.findByRole("heading", { name: "Sign in to continue" }),
    ).toBeVisible();
  });

  /**
   * The controller writes into the DOM this shell renders. Signing out unmounts
   * all of it, so the controller has to be told -- otherwise its 5s poll keeps
   * requesting releases with a dead session and writing the answers into
   * detached nodes.
   */
  it("signing out stops the controller", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(null, { status: 204 })),
    );
    renderApp();
    await screen.findByRole("heading", { name: "Release bundles" });
    expect(initialize).toHaveBeenCalledOnce();
    expect(teardown).not.toHaveBeenCalled();

    screen.getByRole("button", { name: "Sign out" }).click();

    expect(
      await screen.findByRole("heading", { name: "Sign in to continue" }),
    ).toBeVisible();
    expect(teardown).toHaveBeenCalledOnce();
  });
});
