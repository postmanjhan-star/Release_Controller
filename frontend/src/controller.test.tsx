import { configureStore } from "@reduxjs/toolkit";
import { cleanup, render, waitFor } from "@testing-library/react";
import { Provider } from "react-redux";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AuthState } from "./authSlice";

// bpmn-js needs real SVG layout, which jsdom does not provide. The fake records
// what the controller asks of it so the tests can assert viewer reuse instead of
// asserting on pixels.
const bpmn = vi.hoisted(() => ({
  viewers: [] as Array<{
    destroyed: boolean;
    imports: number;
    markers: Set<string>;
  }>,
}));

vi.mock("bpmn-js/lib/NavigatedViewer", () => {
  class FakeViewer {
    private readonly record = {
      destroyed: false,
      imports: 0,
      markers: new Set<string>(),
    };

    constructor() {
      bpmn.viewers.push(this.record);
    }

    async importXML() {
      this.record.imports += 1;
    }

    get(service: string) {
      if (service === "canvas") {
        return {
          zoom: () => 1,
          addMarker: (id: string, marker: string) =>
            this.record.markers.add(`${id}:${marker}`),
          removeMarker: (id: string, marker: string) =>
            this.record.markers.delete(`${id}:${marker}`),
        };
      }
      if (service === "elementRegistry")
        return { get: (id: string) => ({ id }) };
      return { add: () => undefined };
    }

    destroy() {
      this.record.destroyed = true;
    }
  }
  return { default: FakeViewer };
});

const AUTH: AuthState = {
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

function build(number: number, promotable = true) {
  return {
    number,
    branch: "main",
    commit_sha: `${number}`.repeat(8) + "abcdef",
    commit_message: `Commit for build ${number}`,
    author: "jhan",
    status: "success",
    promotable,
  };
}

function buildList(component: string) {
  return {
    component,
    repository: {
      slug: `acme/${component}`,
      link: `https://gitea.example/acme/${component}`,
      default_branch: "main",
    },
    branches: ["main", "release"],
    items: [build(30), build(29), build(28, false)],
  };
}

function componentRow(key: string, position: number) {
  return {
    id: `component-${key}`,
    project_id: "project-1",
    key,
    display_name: key.charAt(0).toUpperCase() + key.slice(1),
    position,
    drone_owner: "acme",
    drone_repo: key,
    drone_slug: `acme/${key}`,
    promote_target_override: null,
    effective_target: "pre-production",
    drone_connection_id: "conn-drone",
    publish_enabled: true,
    gitea_owner: "acme",
    gitea_repo: key,
    gitea_slug: `acme/${key}`,
    gitea_connection_id: "conn-gitea",
    tag_prefix: "v",
    is_active: true,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
  };
}

/**
 * The one project the stub server knows about. Its components are ordered
 * backend first, so a combined release deploys the backend before the
 * frontend — the order is now a property of the project, not of the code.
 */
function project(overrides: Record<string, unknown> = {}) {
  return {
    id: "project-1",
    key: "default",
    name: "Default",
    description: null,
    default_target: "pre-production",
    drone_connection_id: "conn-drone",
    gitea_connection_id: "conn-gitea",
    is_archived: false,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    components: [componentRow("backend", 1), componentRow("frontend", 2)],
    ...overrides,
  };
}

function connection(overrides: Record<string, unknown> = {}) {
  return {
    id: "conn-drone",
    kind: "drone",
    name: "Primary Drone",
    base_url: "https://drone.example",
    // The API answers with a hint, never with the token.
    token_hint: "••••abcd",
    is_default: true,
    verify_status: null,
    verify_detail: null,
    verified_at: null,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...overrides,
  };
}

function deployment(overrides: Record<string, unknown> = {}) {
  return {
    id: "deployment-standalone",
    release_bundle_id: null,
    project_id: "project-1",
    component_id: "component-backend",
    component: "backend",
    drone_owner: "acme",
    drone_repository: "backend",
    source_build_number: 30,
    promotion_build_number: 41,
    commit_sha: "3030303030abcdef",
    branch: "main",
    target: "pre-production",
    status: "SUCCESS",
    publish_status: "NOT_PUBLISHED",
    version: null,
    gitea_release_url: null,
    current_stage: null,
    failed_stage: null,
    error_code: null,
    error_message: null,
    cancel_reason: null,
    attachment_filename: null,
    created_at: "2026-09-01T09:00:00Z",
    updated_at: "2026-09-01T09:10:00Z",
    started_at: "2026-09-01T09:00:10Z",
    finished_at: "2026-09-01T09:10:00Z",
    ...overrides,
  };
}

const BUNDLE_ID = "bundle-aaaabbbb-cccc";

function bundle(overrides: Record<string, unknown> = {}) {
  return {
    id: BUNDLE_ID,
    mode: "BUNDLE",
    target: "pre-production",
    status: "SUCCESS",
    deployment_status: "SUCCESS",
    publish_status: "NOT_PUBLISHED",
    version: null,
    release_name: null,
    release_notes: null,
    publish_records: [],
    workflow_instance_id: "wf-0001",
    current_stage: null,
    failed_stage: null,
    error_code: null,
    error_message: null,
    attachment_filename: null,
    created_at: "2026-09-01T08:00:00Z",
    updated_at: "2026-09-01T08:20:00Z",
    started_at: "2026-09-01T08:00:10Z",
    finished_at: "2026-09-01T08:20:00Z",
    deployments: [
      deployment({
        id: "dep-backend",
        release_bundle_id: BUNDLE_ID,
        component: "backend",
      }),
      deployment({
        id: "dep-frontend",
        release_bundle_id: BUNDLE_ID,
        component_id: "component-frontend",
        component: "frontend",
        source_build_number: 30,
      }),
    ],
    ...overrides,
  };
}

const LEGACY_ID = "legacy-release-1";
const SCHEDULE_ID = "schedule-1";

function legacyRelease() {
  return {
    id: LEGACY_ID,
    repository: "acme/backend",
    environment: "pre-production",
    branch: "main",
    commit_sha: "abcdef1234567890",
    status: "PENDING",
    message: "Legacy approval record",
    approved_by: null,
    rejected_by: null,
    created_at: "2026-08-30T08:00:00Z",
    updated_at: "2026-08-30T08:00:00Z",
  };
}

function schedule(overrides: Record<string, unknown> = {}) {
  return {
    id: SCHEDULE_ID,
    project_id: "project-1",
    mode: "BUNDLE",
    status: "PENDING",
    target: "pre-production",
    selected_component_keys: "backend,frontend",
    component_builds: [
      {
        component_id: "component-backend",
        component_key: "backend",
        build_number: 30,
      },
      {
        component_id: "component-frontend",
        component_key: "frontend",
        build_number: 30,
      },
    ],
    // Still served for rows written before v3.0; the controller prefers
    // component_builds when it is present.
    frontend_build_number: 30,
    backend_build_number: 30,
    scheduled_for_utc: "2026-09-05T02:00:00Z",
    timezone: "Asia/Taipei",
    notification_recipients: ["ops@example.com"],
    notification_status: "QUEUED",
    release_version: "v2.4.0",
    release_notes: "Scheduled release",
    attachment_filename: null,
    requested_by: "jhan",
    release_bundle_id: null,
    deployment_id: null,
    error_message: null,
    created_at: "2026-09-01T07:00:00Z",
    updated_at: "2026-09-01T07:00:00Z",
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

const BPMN_XML =
  '<?xml version="1.0" encoding="UTF-8"?><definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" id="d"><process id="p" /></definitions>';

interface Call {
  method: string;
  path: string;
  body?: unknown;
}

/** Mutable fixture table so a test can change what the server "returns". */
let data: {
  projects: ReturnType<typeof project>[];
  connections: ReturnType<typeof connection>[];
  bundles: ReturnType<typeof bundle>[];
  legacy: ReturnType<typeof legacyRelease>[];
  deployments: ReturnType<typeof deployment>[];
  schedules: ReturnType<typeof schedule>[];
  recipients: Array<{ id: string; email: string; created_at: string }>;
  failReleaseList: boolean;
  hangHealth: boolean;
};

const HEALTH_PATHS = new Set([
  "/health",
  "/api/v1/drone/status",
  "/api/v1/gitea/status",
]);
let calls: Call[];

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function respond(method: string, path: string, body?: unknown): Response {
  if (path === "/health") return json({ status: "ok" });
  if (path === "/api/v1/drone/status") return json({ status: "ok" });
  if (path === "/api/v1/gitea/status") return json({ status: "ok" });
  // The target and the repositories are properties of the project now, so
  // there is no server-wide config endpoint left to serve.
  if (path.startsWith("/api/v1/projects?") || path === "/api/v1/projects") {
    if (method === "POST") {
      const created = project({
        id: "project-new",
        key: "new-project",
        name: "New project",
        components: [],
      });
      data.projects = [...data.projects, created];
      return json(created, 201);
    }
    return json({ items: data.projects });
  }
  if (path === "/api/v1/connections") {
    if (method === "POST") {
      const created = connection({
        id: "connection-new",
        name: "Second Drone",
        is_default: false,
      });
      data.connections = [...data.connections, created];
      return json(created, 201);
    }
    return json({ items: data.connections });
  }

  const connectionTest = path.match(/^\/api\/v1\/connections\/([^/?]+)\/test$/);
  if (connectionTest && method === "POST") {
    return json({
      status: "ok",
      detail: null,
      checked_at: "2026-09-02T12:00:00Z",
    });
  }

  const connectionDetail = path.match(/^\/api\/v1\/connections\/([^/?]+)$/);
  if (connectionDetail && (method === "PATCH" || method === "DELETE")) {
    const found = data.connections.find(
      (item) => item.id === connectionDetail[1],
    );
    if (!found) return json({ detail: "Connection not found" }, 404);
    if (method === "DELETE") {
      data.connections = data.connections.filter((item) => item !== found);
      return new Response(null, { status: 204 });
    }
    const updated = { ...found, name: "Renamed Drone" };
    data.connections = data.connections.map((item) =>
      item === found ? updated : item,
    );
    return json(updated);
  }

  const componentCollection = path.match(
    /^\/api\/v1\/projects\/([^/?]+)\/components$/,
  );
  if (componentCollection && method === "POST") {
    const target = data.projects.find(
      (item) => item.key === componentCollection[1],
    );
    if (!target) return json({ detail: "Project not found" }, 404);
    const added = componentRow("worker", target.components.length + 1);
    target.components = [...target.components, added];
    return json(added, 201);
  }

  const componentReorder = path.match(
    /^\/api\/v1\/projects\/([^/?]+)\/components\/reorder$/,
  );
  if (componentReorder && method === "POST") {
    const target = data.projects.find(
      (item) => item.key === componentReorder[1],
    );
    if (!target) return json({ detail: "Project not found" }, 404);
    const requested =
      (body as { component_ids?: string[] })?.component_ids ?? [];
    const order = requested.map((id, index) => {
      const found = target.components.find((item) => item.id === id)!;
      return { ...found, position: index + 1 };
    });
    target.components = order;
    return json({ items: order });
  }

  const projectDetail = path.match(/^\/api\/v1\/projects\/([^/?]+)$/);
  if (projectDetail && method === "PATCH") {
    const target = data.projects.find((item) => item.key === projectDetail[1]);
    if (!target) return json({ detail: "Project not found" }, 404);
    const updated = { ...target, name: "Renamed project" };
    data.projects = data.projects.map((item) =>
      item === target ? updated : item,
    );
    return json(updated);
  }

  const builds = path.match(
    /^\/api\/v1\/projects\/[^/]+\/components\/([^/]+)\/builds(\?|$)/,
  );
  if (builds) return json(buildList(builds[1]));

  if (path.startsWith("/api/v1/workflows/")) {
    return new Response(BPMN_XML, {
      status: 200,
      headers: { "Content-Type": "application/xml" },
    });
  }

  if (path.startsWith("/api/v1/releases?")) {
    if (data.failReleaseList)
      return json({ detail: "Database is locked" }, 503);
    const items = [...data.legacy, ...data.bundles];
    return json({ items, total: items.length, limit: 100, offset: 0 });
  }
  if (path.startsWith("/api/v1/deployments?")) {
    return json({
      items: data.deployments,
      total: data.deployments.length,
      limit: 100,
      offset: 0,
    });
  }
  if (path.startsWith("/api/v1/schedules?")) {
    return json({
      items: data.schedules,
      total: data.schedules.length,
      limit: 100,
      offset: 0,
    });
  }
  if (path === "/api/v1/notifications/recipients") {
    if (method === "POST") {
      const created = {
        id: "recipient-new",
        email: "release@example.com",
        created_at: "2026-09-02T00:00:00Z",
      };
      data.recipients = [...data.recipients, created];
      return json(created, 201);
    }
    return json({ items: data.recipients, total: data.recipients.length });
  }

  if (path === "/api/v1/schedules" && method === "POST") {
    const created = schedule({ id: "schedule-new", release_version: "v2.5.0" });
    data.schedules = [created, ...data.schedules];
    return json(created, 201);
  }

  if (path === "/api/v1/releases/promote" && method === "POST") {
    const created = bundle({
      id: "bundle-new",
      status: "RUNNING",
      deployment_status: "RUNNING",
    });
    data.bundles = [created, ...data.bundles];
    return json(created, 201);
  }

  const standalonePromote = path.match(
    /^\/api\/v1\/projects\/[^/]+\/components\/([^/]+)\/builds\/(\d+)\/promote$/,
  );
  if (standalonePromote && method === "POST") {
    const created = deployment({
      id: "deployment-new",
      component_id: `component-${standalonePromote[1]}`,
      component: standalonePromote[1],
      source_build_number: Number(standalonePromote[2]),
      status: "RUNNING",
    });
    data.deployments = [created, ...data.deployments];
    return json(created, 201);
  }

  const publishBundle = path.match(/^\/api\/v1\/releases\/([^/?]+)\/publish$/);
  if (publishBundle && method === "POST") {
    const published = bundle({
      publish_status: "PUBLISHED",
      version: "v2.5.0",
    });
    data.bundles = data.bundles.map((item) =>
      item.id === publishBundle[1] ? published : item,
    );
    return json(published);
  }

  const scheduleDetail = path.match(/^\/api\/v1\/schedules\/([^/?]+)$/);
  if (scheduleDetail) {
    const found = data.schedules.find((item) => item.id === scheduleDetail[1]);
    if (!found) return json({ detail: "Schedule not found" }, 404);
    if (method === "DELETE") {
      const cancelled = { ...found, status: "CANCELLED" };
      data.schedules = data.schedules.map((item) =>
        item.id === found.id ? cancelled : item,
      );
      return json(cancelled);
    }
    return json(found);
  }

  const releaseEvents = path.match(/^\/api\/v1\/releases\/([^/?]+)\/events$/);
  if (releaseEvents) {
    return json({
      items: [
        {
          id: "event-1",
          release_bundle_id: releaseEvents[1],
          deployment_id: "dep-backend",
          stage: "PROMOTE_BACKEND",
          event_type: "STAGE_SUCCEEDED",
          status: "SUCCESS",
          error_code: null,
          message: "Backend promoted",
          created_at: "2026-09-01T08:05:00Z",
        },
      ],
    });
  }

  const releaseDetail = path.match(/^\/api\/v1\/releases\/([^/?]+)$/);
  if (releaseDetail) {
    const found =
      data.bundles.find((item) => item.id === releaseDetail[1]) ??
      data.legacy.find((item) => item.id === releaseDetail[1]);
    return found ? json(found) : json({ detail: "Release not found" }, 404);
  }

  if (path.match(/^\/api\/v1\/releases\/[^/?]+\/refresh$/)) {
    const id = path.split("/")[4];
    const found = data.bundles.find((item) => item.id === id);
    return found ? json(found) : json({ detail: "Release not found" }, 404);
  }

  const deploymentEvents = path.match(
    /^\/api\/v1\/deployments\/([^/?]+)\/events$/,
  );
  if (deploymentEvents) return json({ items: [] });

  const deploymentDetail = path.match(/^\/api\/v1\/deployments\/([^/?]+)$/);
  if (deploymentDetail) {
    const found = data.deployments.find(
      (item) => item.id === deploymentDetail[1],
    );
    return found ? json(found) : json({ detail: "Deployment not found" }, 404);
  }

  return json({ detail: `Unhandled ${method} ${path}` }, 500);
}

/** Multipart requests carry their JSON under a `payload` part; unwrap both shapes. */
function readBody(body: BodyInit | null | undefined): unknown {
  if (body instanceof FormData) {
    const payload = body.get("payload");
    return {
      payload: typeof payload === "string" ? JSON.parse(payload) : null,
      attachment: body.get("attachment"),
    };
  }
  if (typeof body === "string") {
    try {
      return JSON.parse(body);
    } catch {
      return body;
    }
  }
  return undefined;
}

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const raw = typeof input === "string" ? input : input.toString();
      const path = raw.startsWith("http")
        ? new URL(raw).pathname + new URL(raw).search
        : raw;
      const method = (init?.method ?? "GET").toUpperCase();
      const body = readBody(init?.body);
      calls.push({ method, path, body });
      // Used to prove the data load does not queue behind the health probes.
      if (data.hangHealth && HEALTH_PATHS.has(path)) {
        return new Promise<Response>(() => {});
      }
      return respond(method, path, body);
    }),
  );
}

/**
 * Renders the real React shell and lets it dynamically import the real
 * controller, exactly as the browser does. The module registry is reset first so
 * every test gets a controller bound to its own freshly rendered DOM.
 */
async function mountApp() {
  const { App } = await import("./App");
  const authReducer = (await import("./authSlice")).default;
  const store = configureStore({
    reducer: { auth: authReducer },
    preloadedState: { auth: AUTH },
  });
  render(
    <Provider store={store}>
      <App />
    </Provider>,
  );
  await waitFor(() => {
    expect(
      document.querySelectorAll("#history-list .history-row").length,
    ).toBeGreaterThan(0);
    expect(
      document.querySelectorAll("#frontend-builds .build-row").length,
    ).toBeGreaterThan(0);
  });
  return store;
}

function historyRows() {
  return Array.from(
    document.querySelectorAll<HTMLAnchorElement>("#history-list .history-row"),
  );
}

function buildRows(component: string) {
  return Array.from(
    document.querySelectorAll<HTMLButtonElement>(
      `#${component}-builds .build-row`,
    ),
  );
}

function detail() {
  return document.querySelector("#release-detail") as HTMLElement;
}

function pathsFor(fragment: string) {
  return calls.filter((call) => call.path.includes(fragment));
}

function called(method: string, path: string) {
  return calls.filter((call) => call.method === method && call.path === path);
}

function submit(selector: string) {
  const form = document.querySelector(selector) as HTMLFormElement;
  form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  return form;
}

function field(form: HTMLFormElement, name: string) {
  return form.elements.namedItem(name) as
    HTMLInputElement | HTMLTextAreaElement;
}

let intervals: number[];
// waitFor polls with setInterval too, so the 5s controller poll is picked out by
// its period rather than by being the only one.
let pollIntervals: number[];
let clearedIntervals: number[];

beforeEach(() => {
  vi.resetModules();
  bpmn.viewers.length = 0;
  calls = [];
  intervals = [];
  pollIntervals = [];
  clearedIntervals = [];
  data = {
    projects: [project()],
    connections: [
      connection(),
      connection({
        id: "conn-gitea",
        kind: "gitea",
        name: "Primary Gitea",
        base_url: "https://gitea.example",
      }),
    ],
    bundles: [bundle()],
    legacy: [legacyRelease()],
    deployments: [
      deployment(),
      deployment({ id: "dep-backend", release_bundle_id: BUNDLE_ID }),
    ],
    schedules: [schedule()],
    recipients: [
      {
        id: "recipient-1",
        email: "ops@example.com",
        created_at: "2026-08-01T00:00:00Z",
      },
    ],
    failReleaseList: false,
    hangHealth: false,
  };
  stubFetch();
  // The controller starts a 5s poll it never clears; collect the ids so one test
  // cannot leak a timer into the next.
  const realSetInterval = globalThis.setInterval;
  vi.stubGlobal("setInterval", ((
    handler: TimerHandler,
    timeout?: number,
    ...rest: unknown[]
  ) => {
    const id = realSetInterval(handler, timeout, ...rest);
    intervals.push(id);
    if (timeout === 5000) pollIntervals.push(id as unknown as number);
    return id;
  }) as typeof setInterval);
  const realClearInterval = globalThis.clearInterval;
  vi.stubGlobal("clearInterval", ((id?: number) => {
    if (id !== undefined) clearedIntervals.push(id);
    realClearInterval(id);
  }) as typeof clearInterval);
  window.history.replaceState({}, "", "/");
});

afterEach(() => {
  for (const id of intervals) clearInterval(id);
  cleanup();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("首次載入", () => {
  it("依專案畫出元件的 builds，並套用該專案的預設 target", async () => {
    await mountApp();

    expect(
      (document.querySelector("#project-selector") as HTMLSelectElement).value,
    ).toBe("default");
    // The cards exist because the project says so, in the project's order.
    expect(
      Array.from(
        document.querySelectorAll<HTMLElement>(
          "#component-grid .component-card",
        ),
      ).map((card) => card.dataset.component),
    ).toEqual(["backend", "frontend"]);
    expect(buildRows("frontend")).toHaveLength(3);
    expect(buildRows("backend")).toHaveLength(3);
    expect(
      (document.querySelector("#release-target") as HTMLInputElement).value,
    ).toBe("pre-production");
    expect(document.querySelector("#frontend-repo")).toHaveTextContent(
      "acme/frontend",
    );
    expect(document.querySelector("#service-health")).toHaveClass("healthy");
    expect(document.querySelector("#drone-health")).toHaveClass("healthy");
    expect(document.querySelector("#gitea-health")).toHaveClass("healthy");
  });

  /**
   * The Drone and Gitea probes are round-trips the backend makes to upstream
   * services. While they ran in sequence ahead of the data load, their latency
   * sat in front of the first useful paint; if one hung, nothing rendered.
   */
  it("健康檢查沒回應也不擋住資料載入", async () => {
    data.hangHealth = true;
    await mountApp();

    expect(historyRows().length).toBeGreaterThan(0);
    expect(buildRows("frontend")).toHaveLength(3);
    // All three probes were issued, none of them answered.
    expect(called("GET", "/health")).toHaveLength(1);
    expect(called("GET", "/api/v1/drone/status")).toHaveLength(1);
    expect(called("GET", "/api/v1/gitea/status")).toHaveLength(1);
    expect(document.querySelector("#service-health")).not.toHaveClass(
      "healthy",
    );
  });

  it("自動選取第一個可 promote 的 build 並開放合併發布", async () => {
    await mountApp();

    expect(document.querySelector("#frontend-selection")).toHaveTextContent(
      "Selected #30",
    );
    expect(document.querySelector("#release-both")).not.toBeDisabled();

    const target = document.querySelector(
      "#release-target",
    ) as HTMLInputElement;
    target.value = "";
    target.dispatchEvent(new Event("input", { bubbles: true }));

    expect(document.querySelector("#release-both")).toBeDisabled();
    expect(
      document.querySelector('[data-deploy-component="frontend"]'),
    ).toBeDisabled();
  });

  it("不可 promote 的 build 無法被選取", async () => {
    await mountApp();

    const rows = buildRows("frontend");
    expect(rows[2]).toBeDisabled();
    rows[2].click();

    expect(document.querySelector("#frontend-selection")).toHaveTextContent(
      "Selected #30",
    );
  });
});

describe("選取不會重建 DOM", () => {
  it("改選 build 只移動 selected 樣式，沿用原本的節點", async () => {
    await mountApp();

    const before = buildRows("frontend");
    expect(before[0]).toHaveClass("selected");

    before[1].click();

    const after = buildRows("frontend");
    expect(after[0]).toBe(before[0]);
    expect(after[1]).toBe(before[1]);
    expect(after[1]).toHaveClass("selected");
    expect(after[0]).not.toHaveClass("selected");
    expect(document.querySelector("#frontend-selection")).toHaveTextContent(
      "Selected #29",
    );
  });

  it("重新整理相同的歷史資料時沿用原本的列節點", async () => {
    await mountApp();

    const before = historyRows();
    (document.querySelector("#refresh-all") as HTMLButtonElement).click();
    await waitFor(() => {
      expect(pathsFor("/api/v1/releases?").length).toBeGreaterThan(1);
    });

    const after = historyRows();
    expect(after).toHaveLength(before.length);
    expect(after[0]).toBe(before[0]);
  });
});

describe("檢視切換", () => {
  it("切到 Workflows 時更新網址、顯示篩選器，並列出四種 workflow", async () => {
    await mountApp();

    (
      document.querySelector(
        '.topnav [data-view="workflows"]',
      ) as HTMLAnchorElement
    ).click();

    await waitFor(() => {
      expect(historyRows().length).toBeGreaterThan(3);
    });
    expect(window.location.search).toContain("view=workflows");
    expect(document.querySelector("#workflow-filters")).not.toHaveAttribute(
      "hidden",
    );
    expect(document.querySelector("#launch-panel")).toHaveAttribute("hidden");

    const ids = historyRows().map((row) => row.dataset.historyId);
    expect(ids).toEqual(
      expect.arrayContaining([
        `schedule:${SCHEDULE_ID}`,
        `bundle:${BUNDLE_ID}`,
        "deployment:deployment-standalone",
        `legacy:${LEGACY_ID}`,
      ]),
    );
    // Deployments that belong to a bundle must not appear as their own workflow.
    expect(ids).not.toContain("deployment:dep-backend");
  });

  it("上一頁回到前一個檢視，而且不重新載入文件", async () => {
    await mountApp();

    (
      document.querySelector(
        '.topnav [data-view="workflows"]',
      ) as HTMLAnchorElement
    ).click();
    await waitFor(() =>
      expect(window.location.search).toContain("view=workflows"),
    );

    window.history.back();

    await waitFor(() => {
      expect(window.location.search).not.toContain("view=workflows");
      expect(document.querySelector("#history-title")).toHaveTextContent(
        "Release bundles",
      );
    });
  });

  it("篩選 Pending schedules 只留下待執行的排程", async () => {
    await mountApp();

    (
      document.querySelector(
        '.topnav [data-view="workflows"]',
      ) as HTMLAnchorElement
    ).click();
    await waitFor(() => expect(historyRows().length).toBeGreaterThan(3));

    (
      document.querySelector("#pending-schedules-filter") as HTMLButtonElement
    ).click();

    await waitFor(() => {
      expect(historyRows().map((row) => row.dataset.historyId)).toEqual([
        `schedule:${SCHEDULE_ID}`,
      ]);
    });
    expect(window.location.search).toContain("workflow_kind=schedule");
    expect(window.location.search).toContain("workflow_status=pending");
  });
});

describe("選取歷史記錄", () => {
  it("抓取該筆與它的事件，並把 detail 與 BPMN 畫出來", async () => {
    await mountApp();

    await waitFor(() => {
      expect(detail().querySelector(".detail-heading")).toBeTruthy();
    });

    expect(
      pathsFor(`/api/v1/releases/${BUNDLE_ID}`).map((call) => call.path),
    ).toEqual(
      expect.arrayContaining([
        `/api/v1/releases/${BUNDLE_ID}`,
        `/api/v1/releases/${BUNDLE_ID}/events`,
      ]),
    );
    expect(detail()).toHaveTextContent(`Release ${BUNDLE_ID.slice(0, 8)}`);
    expect(detail()).toHaveTextContent("Backend promoted");
    expect(detail().querySelectorAll(".deployment-card")).toHaveLength(2);

    // The live viewer is the one mounted for this bundle; the workflow preview
    // drawn during startup was disposed when the detail markup was replaced.
    await waitFor(() => expect(bpmn.viewers.at(-1)?.imports).toBe(1));
    const live = bpmn.viewers.at(-1)!;
    expect(live.destroyed).toBe(false);
    expect(live.markers.size).toBeGreaterThan(0);
  });

  it("點選會把選取寫進網址，重複點選不會堆積瀏覽紀錄", async () => {
    await mountApp();
    await waitFor(() =>
      expect(detail().querySelector(".detail-heading")).toBeTruthy(),
    );

    historyRows()[0].click();
    await waitFor(() => {
      expect(window.location.search).toContain(`selected=${BUNDLE_ID}`);
    });

    const entries = window.history.length;
    historyRows()[0].click();
    historyRows()[0].click();

    expect(window.history.length).toBe(entries);
  });
});

describe("輪詢與重新整理", () => {
  it("資料沒變時不重建 detail，也不重建 BPMN viewer", async () => {
    data.bundles = [
      bundle({ status: "RUNNING", deployment_status: "RUNNING" }),
    ];
    await mountApp();

    await waitFor(() =>
      expect(document.querySelector("#refresh-release")).toBeTruthy(),
    );
    await waitFor(() => expect(bpmn.viewers.at(-1)?.imports).toBe(1));

    const heading = detail().querySelector(".detail-heading");
    const viewerCount = bpmn.viewers.length;
    const live = bpmn.viewers.at(-1)!;

    (document.querySelector("#refresh-release") as HTMLButtonElement).click();
    await waitFor(() => {
      expect(pathsFor(`/api/v1/releases/${BUNDLE_ID}/refresh`)).toHaveLength(1);
    });
    await waitFor(() =>
      expect(document.querySelector("#toast")).toHaveTextContent("refreshed"),
    );

    expect(detail().querySelector(".detail-heading")).toBe(heading);
    expect(bpmn.viewers).toHaveLength(viewerCount);
    expect(live.destroyed).toBe(false);
    expect(live.imports).toBe(1);
  });

  it("資料變了才重畫 detail", async () => {
    data.bundles = [
      bundle({ status: "RUNNING", deployment_status: "RUNNING" }),
    ];
    await mountApp();
    await waitFor(() =>
      expect(document.querySelector("#refresh-release")).toBeTruthy(),
    );

    expect(detail()).toHaveTextContent("RUNNING");
    data.bundles = [
      bundle({
        status: "FAILED",
        deployment_status: "FAILED",
        failed_stage: "PROMOTE_FRONTEND",
        error_code: "DRONE_TIMEOUT",
        error_message: "Drone did not answer",
      }),
    ];

    (document.querySelector("#refresh-release") as HTMLButtonElement).click();

    await waitFor(() => {
      expect(detail()).toHaveTextContent("DRONE_TIMEOUT");
    });
    expect(detail()).toHaveTextContent("Drone did not answer");
  });
});

describe("排程", () => {
  it("取消排程會對正確的端點送出 DELETE 並更新狀態", async () => {
    await mountApp();

    (
      document.querySelector(
        '.topnav [data-view="workflows"]',
      ) as HTMLAnchorElement
    ).click();
    await waitFor(() => expect(historyRows().length).toBeGreaterThan(3));

    const row = historyRows().find(
      (item) => item.dataset.historyId === `schedule:${SCHEDULE_ID}`,
    )!;
    row.click();

    await waitFor(() =>
      expect(document.querySelector("#cancel-schedule")).toBeTruthy(),
    );
    (document.querySelector("#cancel-schedule") as HTMLButtonElement).click();

    await waitFor(() => {
      expect(called("DELETE", `/api/v1/schedules/${SCHEDULE_ID}`)).toHaveLength(
        1,
      );
    });
    await waitFor(() => expect(detail()).toHaveTextContent("CANCELLED"));
    expect(document.querySelector("#cancel-schedule")).toBeNull();
  });
});

describe("收件人", () => {
  it("新增 email 收件人後出現在清單裡", async () => {
    await mountApp();

    (
      document.querySelector(
        '.topnav [data-view="recipients"]',
      ) as HTMLAnchorElement
    ).click();
    await waitFor(() => expect(historyRows()).toHaveLength(1));
    expect(document.querySelector("#new-recipient")).not.toHaveAttribute(
      "hidden",
    );

    const form = document.querySelector("#recipient-form") as HTMLFormElement;
    field(form, "email").value = "release@example.com";
    form.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );

    await waitFor(() => {
      expect(called("POST", "/api/v1/notifications/recipients")).toHaveLength(
        1,
      );
    });
    expect(called("POST", "/api/v1/notifications/recipients")[0].body).toEqual({
      email: "release@example.com",
    });
    await waitFor(() => {
      expect(historyRows().map((row) => row.textContent)).toEqual(
        expect.arrayContaining([
          expect.stringContaining("release@example.com"),
        ]),
      );
    });
  });
});

describe("發布動作", () => {
  it("合併發布會把兩邊的 build 編號與 target 送進 multipart payload", async () => {
    await mountApp();

    (document.querySelector("#release-both") as HTMLButtonElement).click();
    expect(document.querySelector("#confirmation-summary")).toHaveTextContent(
      "Build #30",
    );

    submit("#confirmation-form");

    await waitFor(() => {
      expect(called("POST", "/api/v1/releases/promote")).toHaveLength(1);
    });
    expect(called("POST", "/api/v1/releases/promote")[0].body).toEqual({
      payload: {
        project_id: "default",
        components: [
          { key: "backend", build_number: 30 },
          { key: "frontend", build_number: 30 },
        ],
        target: "pre-production",
      },
      attachment: null,
    });
    await waitFor(() => {
      expect(detail()).toHaveTextContent("Release bundle-n");
    });
    expect(historyRows()[0].dataset.historyId).toBe("bundle-new");
    expect(historyRows()[0]).toHaveClass("selected");
    expect(document.querySelector("#toast")).toHaveTextContent("Backend first");
    // 已知的不一致：建立 bundle 後網址不會記下 selected（建立排程時會），
    // 所以這時候重新整理頁面會失去選取。行為先鎖在這裡，等重構時一起處理。
    expect(window.location.search).not.toContain("selected=");
  });

  it("單一元件 promote 會打該元件的 build 端點並切到 Workflows 的 deployment 篩選", async () => {
    await mountApp();

    (
      document.querySelector(
        '[data-deploy-component="backend"]',
      ) as HTMLButtonElement
    ).click();
    expect(document.querySelector("#promote-summary")).toHaveTextContent(
      "Backend #30",
    );

    submit("#promote-form");

    await waitFor(() => {
      expect(
        called(
          "POST",
          "/api/v1/projects/default/components/backend/builds/30/promote",
        ),
      ).toHaveLength(1);
    });
    await waitFor(() => {
      expect(window.location.search).toContain("view=workflows");
      expect(window.location.search).toContain("workflow_kind=deployment");
      expect(window.location.search).toContain(
        "selected=deployment%3Adeployment-new",
      );
      expect(document.querySelector("#history-title")).toHaveTextContent(
        "Workflow runs",
      );
    });
  });

  it("Publish 會帶著版本與 release notes 呼叫 publish 端點", async () => {
    await mountApp();
    await waitFor(() =>
      expect(document.querySelector("#publish-version")).toBeTruthy(),
    );

    (document.querySelector("#publish-version") as HTMLButtonElement).click();

    const form = document.querySelector("#publish-form") as HTMLFormElement;
    // The dialog pre-fills release notes from the deployment it is publishing.
    expect(field(form, "release_notes").value).toContain(
      "Target: pre-production",
    );
    field(form, "version").value = "v2.5.0";
    field(form, "name").value = "v2.5.0";
    submit("#publish-form");

    await waitFor(() => {
      expect(
        called("POST", `/api/v1/releases/${BUNDLE_ID}/publish`),
      ).toHaveLength(1);
    });
    expect(
      called("POST", `/api/v1/releases/${BUNDLE_ID}/publish`)[0].body,
    ).toEqual({
      version: "v2.5.0",
      name: "v2.5.0",
      release_notes: expect.stringContaining("Target: pre-production"),
      draft: false,
      prerelease: false,
    });
    await waitFor(() => expect(detail()).toHaveTextContent("PUBLISHED"));
  });
});

describe("建立排程", () => {
  it("送出排程時帶上勾選的收件人，並記住這次的選擇", async () => {
    window.localStorage.clear();
    await mountApp();

    (document.querySelector("#schedule-both") as HTMLButtonElement).click();

    const options = document.querySelectorAll<HTMLInputElement>(
      '#schedule-recipient-options input[name="notification_recipients"]',
    );
    expect(options).toHaveLength(1);
    expect(options[0].checked).toBe(true);
    options[0].dispatchEvent(new Event("change", { bubbles: true }));
    expect(
      JSON.parse(
        window.localStorage.getItem("release-controller.schedule-recipients")!,
      ),
    ).toEqual(["ops@example.com"]);

    const form = document.querySelector("#schedule-form") as HTMLFormElement;
    field(form, "release_version").value = "v2.5.0";
    field(form, "release_notes").value = "Nightly window";
    submit("#schedule-form");

    await waitFor(() => {
      expect(called("POST", "/api/v1/schedules")).toHaveLength(1);
    });
    const sent = called("POST", "/api/v1/schedules")[0].body as {
      payload: Record<string, unknown>;
    };
    expect(sent.payload).toMatchObject({
      project_id: "default",
      components: [
        { key: "backend", build_number: 30 },
        { key: "frontend", build_number: 30 },
      ],
      target: "pre-production",
      timezone: expect.any(String),
      notification_recipients: ["ops@example.com"],
      release_version: "v2.5.0",
      release_notes: "Nightly window",
    });
    await waitFor(() => {
      expect(window.location.search).toContain("view=workflows");
      expect(window.location.search).toContain(
        "selected=schedule%3Aschedule-new",
      );
    });
  });
});

describe("錯誤處理", () => {
  it("歷史載入失敗時顯示錯誤，Retry 之後可以復原", async () => {
    await mountApp();

    data.failReleaseList = true;
    (
      document.querySelector(
        '.topnav [data-view="workflows"]',
      ) as HTMLAnchorElement
    ).click();
    await waitFor(() =>
      expect(window.location.search).toContain("view=workflows"),
    );

    (
      document.querySelector(
        '.topnav [data-view="releases"]',
      ) as HTMLAnchorElement
    ).click();
    await waitFor(() => {
      expect(document.querySelector("#history-list")).toHaveTextContent(
        "Unable to load this view",
      );
    });
    expect(document.querySelector("#retry-history")).toBeTruthy();

    data.failReleaseList = false;
    (document.querySelector("#retry-history") as HTMLButtonElement).click();

    await waitFor(() => {
      expect(historyRows()).toHaveLength(1);
    });
    expect(document.querySelector("#history-list")).not.toHaveTextContent(
      "Unable to load this view",
    );
  });
});

describe("專案設定", () => {
  async function openProjects() {
    (
      document.querySelector(
        '.topnav [data-view="projects"]',
      ) as HTMLAnchorElement
    ).click();
    await waitFor(() =>
      expect(detail().querySelector(".component-table")).toBeTruthy(),
    );
  }

  it("列出專案並依部署順序畫出元件", async () => {
    await mountApp();
    await openProjects();

    expect(historyRows().map((row) => row.dataset.historyId)).toEqual([
      "project-1",
    ]);
    expect(document.querySelector("#history-title")).toHaveTextContent(
      "Projects",
    );
    expect(document.querySelector("#new-project")).not.toHaveAttribute(
      "hidden",
    );
    // The order in the table is the deployment order, read from position.
    expect(
      Array.from(
        detail().querySelectorAll<HTMLElement>(".component-row code"),
      ).map((node) => node.textContent),
    ).toEqual(expect.arrayContaining(["backend", "acme/backend", "frontend"]));
    expect(detail()).toHaveTextContent("Primary Drone");
    expect(detail()).toHaveTextContent("Primary Gitea");
  });

  it("上移元件會送出完整的新順序", async () => {
    await mountApp();
    await openProjects();

    const moveUp = detail().querySelectorAll<HTMLButtonElement>(
      '[data-move-component][data-direction="-1"]',
    );
    // The first row cannot move up; the second one can.
    expect(moveUp[0]).toBeDisabled();
    moveUp[1].click();

    await waitFor(() => {
      expect(
        called("POST", "/api/v1/projects/default/components/reorder"),
      ).toHaveLength(1);
    });
    expect(
      called("POST", "/api/v1/projects/default/components/reorder")[0].body,
    ).toEqual({ component_ids: ["component-frontend", "component-backend"] });
    await waitFor(() => {
      expect(
        Array.from(
          detail().querySelectorAll<HTMLElement>(".component-row"),
        ).map((row) => row.dataset.componentId),
      ).toEqual(["component-frontend", "component-backend"]);
    });
  });

  it("新增元件後回頭更新發布畫面的元件卡片", async () => {
    await mountApp();
    expect(
      document.querySelectorAll("#component-grid .component-card"),
    ).toHaveLength(2);
    await openProjects();

    (document.querySelector("#add-component") as HTMLButtonElement).click();
    const form = document.querySelector("#component-form") as HTMLFormElement;
    field(form, "drone_owner").value = "acme";
    field(form, "drone_repo").value = "worker";
    submit("#component-form");

    await waitFor(() => {
      expect(
        called("POST", "/api/v1/projects/default/components"),
      ).toHaveLength(1);
    });
    expect(
      called("POST", "/api/v1/projects/default/components")[0].body,
    ).toMatchObject({
      drone_owner: "acme",
      drone_repo: "worker",
      publish_enabled: true,
      is_active: true,
    });
    // The launcher shares the project list, so its cards follow along.
    await waitFor(() => {
      expect(
        document.querySelectorAll("#component-grid .component-card"),
      ).toHaveLength(3);
    });
  });

  it("編輯專案時不能改 key，送出的是 PATCH", async () => {
    await mountApp();
    await openProjects();

    (document.querySelector("#edit-project") as HTMLButtonElement).click();
    expect(document.querySelector("#project-key-field")).toHaveAttribute(
      "hidden",
    );
    const form = document.querySelector("#project-form") as HTMLFormElement;
    expect(field(form, "name").value).toBe("Default");
    field(form, "name").value = "Renamed project";
    submit("#project-form");

    await waitFor(() => {
      expect(called("PATCH", "/api/v1/projects/default")).toHaveLength(1);
    });
    expect(called("PATCH", "/api/v1/projects/default")[0].body).toEqual({
      name: "Renamed project",
      default_target: "pre-production",
      description: null,
      drone_connection_id: "conn-drone",
      gitea_connection_id: "conn-gitea",
    });
    await waitFor(() =>
      expect(historyRows()[0]).toHaveTextContent("Renamed project"),
    );
  });

  /**
   * The component-row buttons are delegated from #release-detail, which survives
   * every patch. Binding them per render stacked one listener per project the
   * operator had opened, so a single click sent one reorder request per visit.
   */
  it("看過多個專案後，元件按鈕仍只送出一次請求", async () => {
    data.projects = [
      project(),
      project({ id: "project-2", key: "second", name: "Second project" }),
    ];
    await mountApp();
    await openProjects();

    // Each switch re-renders the detail with different markup, which is what
    // used to add another listener to the surviving container.
    for (const historyId of [
      "project-2",
      "project-1",
      "project-2",
      "project-1",
    ]) {
      historyRows()
        .find((row) => row.dataset.historyId === historyId)!
        .click();
      await waitFor(() =>
        expect(detail().querySelector(".component-table")).toBeTruthy(),
      );
      await waitFor(() =>
        expect(detail()).toHaveTextContent(
          historyId === "project-2" ? "Second project" : "Default",
        ),
      );
    }

    const moveUp = detail().querySelectorAll<HTMLButtonElement>(
      '[data-move-component][data-direction="-1"]',
    );
    moveUp[1].click();

    await waitFor(() => {
      expect(
        called("POST", "/api/v1/projects/default/components/reorder"),
      ).toHaveLength(1);
    });
    // Give any surplus listener a chance to land before asserting it did not.
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(
      called("POST", "/api/v1/projects/default/components/reorder"),
    ).toHaveLength(1);
  });
});

/**
 * Signing out unmounts everything the controller reaches into. The module used
 * to survive that with `initialized` still true and `elements` still pointing at
 * the unmounted nodes, so signing back in produced a shell with nothing bound to
 * it -- dead until the operator reloaded the page.
 */
describe("登出再登入", () => {
  const SESSION = {
    enabled: true,
    configured: true,
    authenticated: true,
    user: AUTH.user,
  };

  it("重新登入後 controller 重新綁定到新的 DOM", async () => {
    const store = await mountApp();
    const before = document.querySelector("#component-grid");

    store.dispatch({ type: "auth/logout/fulfilled" });
    await waitFor(() =>
      expect(document.querySelector("#component-grid")).toBeNull(),
    );

    store.dispatch({ type: "auth/loadSession/fulfilled", payload: SESSION });

    // A fresh shell: React rendered new nodes, so the controller has to have
    // looked them up again rather than holding the unmounted ones.
    await waitFor(() => {
      expect(buildRows("frontend")).toHaveLength(3);
      expect(historyRows().length).toBeGreaterThan(0);
    });
    expect(document.querySelector("#component-grid")).not.toBe(before);

    // And bindEvents ran against them: selecting a build is a delegated click on
    // the new grid, and the target input still drives the release button.
    buildRows("frontend")[1].click();
    expect(document.querySelector("#frontend-selection")).toHaveTextContent(
      "Selected #29",
    );
    expect(document.querySelector("#release-both")).not.toBeDisabled();
  });

  /**
   * Asserted on the timer rather than by waiting out a 5s tick: the poll used to
   * keep requesting releases with a dead session for as long as the tab stayed
   * open, and nothing ever stopped it.
   */
  it("登出後停止輪詢", async () => {
    const store = await mountApp();
    expect(pollIntervals).toHaveLength(1);
    expect(clearedIntervals).not.toContain(pollIntervals[0]);

    store.dispatch({ type: "auth/logout/fulfilled" });
    await waitFor(() =>
      expect(document.querySelector("#component-grid")).toBeNull(),
    );

    expect(clearedIntervals).toContain(pollIntervals[0]);
  });
});

describe("連線設定", () => {
  async function openConnections() {
    (
      document.querySelector(
        '.topnav [data-view="projects"]',
      ) as HTMLAnchorElement
    ).click();
    await waitFor(() => expect(historyRows()).toHaveLength(1));
    (
      document.querySelector(
        '[data-settings-view="connections"]',
      ) as HTMLButtonElement
    ).click();
    await waitFor(() => expect(historyRows()).toHaveLength(2));
  }

  it("只顯示 token 提示，不顯示 token 本身", async () => {
    await mountApp();
    await openConnections();

    expect(document.querySelector("#history-title")).toHaveTextContent(
      "Upstream connections",
    );
    expect(
      document.querySelector('.topnav [data-view="projects"]'),
    ).toHaveAttribute("aria-current", "page");
    expect(detail()).toHaveTextContent("••••abcd");
    const form = document.querySelector("#connection-form") as HTMLFormElement;
    (document.querySelector("#edit-connection") as HTMLButtonElement).click();
    // Editing an existing connection must never pre-fill the secret.
    expect(field(form, "token").value).toBe("");
    expect(field(form, "token")).toHaveAttribute("type", "password");
    expect(document.querySelector("#connection-token-hint")).toHaveTextContent(
      "Leave empty to keep it",
    );
  });

  it("編輯時留白的 token 不會送出，Test 會寫回驗證結果", async () => {
    await mountApp();
    await openConnections();

    (document.querySelector("#edit-connection") as HTMLButtonElement).click();
    const form = document.querySelector("#connection-form") as HTMLFormElement;
    field(form, "name").value = "Renamed Drone";
    submit("#connection-form");

    await waitFor(() => {
      expect(called("PATCH", "/api/v1/connections/conn-drone")).toHaveLength(1);
    });
    const sent = called("PATCH", "/api/v1/connections/conn-drone")[0]
      .body as Record<string, unknown>;
    expect(sent).not.toHaveProperty("token");
    expect(sent).not.toHaveProperty("kind");

    (document.querySelector("#test-connection") as HTMLButtonElement).click();
    await waitFor(() => {
      expect(
        called("POST", "/api/v1/connections/conn-drone/test"),
      ).toHaveLength(1);
    });
    await waitFor(() => expect(detail()).toHaveTextContent("ok"));
    expect(historyRows()[0]).toHaveTextContent("OK");
  });
});
