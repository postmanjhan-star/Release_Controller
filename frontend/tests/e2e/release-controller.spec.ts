import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";

const scheduledBpmn = readFileSync(
  new URL(
    "../../../app/workflows/bpmn/scheduled_release.bpmn",
    import.meta.url,
  ),
  "utf8",
);

const bundleBpmn = readFileSync(
  new URL("../../../app/workflows/bpmn/release_bundle.bpmn", import.meta.url),
  "utf8",
);

const buildResponse = (component: "frontend" | "backend") => ({
  component,
  repository: {
    slug: `release/${component}`,
    link: `https://example.test/release/${component}`,
    default_branch: "main",
  },
  branches: ["main"],
  items: [
    {
      number: component === "frontend" ? 101 : 202,
      branch: "main",
      commit_sha:
        component === "frontend" ? "frontend12345678" : "backend12345678",
      commit_message: `${component} release candidate`,
      author: "release-team",
      status: "success",
      promotable: true,
    },
  ],
});

const projectComponent = (
  component: "frontend" | "backend",
  position: number,
) => ({
  id: `component-${component}`,
  project_id: "project-release",
  key: component,
  display_name: component === "frontend" ? "Frontend" : "Backend",
  position,
  drone_owner: "release",
  drone_repo: component,
  drone_slug: `release/${component}`,
  promote_target_override: null,
  effective_target: "production",
  drone_connection_id: "drone-default",
  publish_enabled: true,
  gitea_owner: "release",
  gitea_repo: component,
  gitea_slug: `release/${component}`,
  gitea_connection_id: "gitea-default",
  tag_prefix: "v",
  is_active: true,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
});

/**
 * The components, their order and the default target all come from the project
 * now. This one deploys the frontend first — the opposite of the fixture in the
 * controller unit tests — so that the deployment order is demonstrably read
 * from the project rather than hardcoded anywhere.
 */
const projectResponse = {
  items: [
    {
      id: "project-release",
      key: "release",
      name: "Release",
      description: null,
      default_target: "production",
      drone_connection_id: "drone-default",
      gitea_connection_id: "gitea-default",
      is_archived: false,
      created_at: "2026-08-01T00:00:00Z",
      updated_at: "2026-08-01T00:00:00Z",
      components: [
        projectComponent("frontend", 1),
        projectComponent("backend", 2),
      ],
    },
  ],
};

const connectionResponse = {
  items: [
    {
      id: "drone-default",
      kind: "drone",
      name: "Release Drone",
      base_url: "https://drone.example.test",
      token_hint: "••••1234",
      is_default: true,
      verify_status: "ok",
      verify_detail: null,
      verified_at: "2026-08-30T09:00:00Z",
      created_at: "2026-08-01T00:00:00Z",
      updated_at: "2026-08-30T09:00:00Z",
    },
    {
      id: "gitea-default",
      kind: "gitea",
      name: "Release Gitea",
      base_url: "https://gitea.example.test",
      token_hint: "••••5678",
      is_default: true,
      verify_status: null,
      verify_detail: null,
      verified_at: null,
      created_at: "2026-08-01T00:00:00Z",
      updated_at: "2026-08-01T00:00:00Z",
    },
  ],
};

test.beforeEach(async ({ page }) => {
  await page.route("**/health", (route) =>
    route.fulfill({ json: { status: "ok" } }),
  );
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/auth/session")) {
      await route.fulfill({
        json: {
          enabled: true,
          configured: true,
          authenticated: true,
          user: {
            login: "release-operator",
            full_name: "Release Operator",
            email: "release-operator@example.com",
            avatar_url: null,
            is_admin: false,
          },
        },
      });
    } else if (url.pathname.endsWith("/api/v1/projects")) {
      await route.fulfill({ json: projectResponse });
    } else if (url.pathname.endsWith("/api/v1/connections")) {
      await route.fulfill({ json: connectionResponse });
    } else if (url.pathname.endsWith("/drone/status")) {
      await route.fulfill({ json: { status: "ok" } });
    } else if (url.pathname.endsWith("/gitea/status")) {
      await route.fulfill({
        json: {
          status: "ok",
          server: "https://gitea.example.test",
          endpoint: "https://gitea.example.test/api/healthz",
          detail: null,
        },
      });
    } else if (url.pathname.endsWith("/notifications/recipients")) {
      await route.fulfill({
        json: {
          items: [
            {
              id: "first",
              email: "first@example.com",
              created_at: "2026-08-26T07:11:00Z",
            },
            {
              id: "second",
              email: "second@example.com",
              created_at: "2026-08-26T07:12:00Z",
            },
          ],
          total: 2,
        },
      });
    } else if (url.pathname.includes("/components/frontend/builds")) {
      await route.fulfill({ json: buildResponse("frontend") });
    } else if (url.pathname.includes("/components/backend/builds")) {
      await route.fulfill({ json: buildResponse("backend") });
    } else if (
      url.pathname.includes("/workflows/") &&
      url.pathname.endsWith("/definition")
    ) {
      await route.fulfill({
        contentType: "application/xml",
        body: "<definitions />",
      });
    } else {
      await route.fulfill({ json: { items: [] } });
    }
  });
});

test("loads builds and enables release actions", async ({ page }) => {
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "Choose independent source builds" }),
  ).toBeVisible();
  await expect(page.getByText("frontend release candidate")).toBeVisible();
  await expect(page.getByText("backend release candidate")).toBeVisible();
  await expect(page.locator("#release-target")).toHaveValue("production");
  await expect(
    page.getByRole("button", { name: "Release Frontend + Backend" }),
  ).toBeEnabled();
  await expect(page.locator("#service-health")).toContainText(
    "Service healthy",
  );
  await expect(page.locator("#gitea-health")).toContainText("Gitea healthy");
});

test("selects saved email recipients and remembers the choice", async ({
  page,
}) => {
  let submittedRecipients: string[] = [];
  let createdSchedule: Record<string, unknown> | null = null;
  await page.route("**/api/v1/schedules", async (route) => {
    const request = route.request();
    const body = request.postData();
    if (body === null) {
      throw new Error("Schedule request is missing its multipart body");
    }
    const formData = await new Response(body, {
      headers: { "content-type": request.headers()["content-type"] },
    }).formData();
    const rawPayload = formData.get("payload");
    if (typeof rawPayload !== "string") {
      throw new Error("Schedule request is missing its JSON payload");
    }
    const payload = JSON.parse(rawPayload);
    submittedRecipients = payload.notification_recipients;
    createdSchedule = {
      id: "new-schedule",
      ...payload,
      // The server answers with the stored child rows, not with the request's
      // component list.
      component_builds: (payload.components ?? []).map(
        (item: { key: string; build_number: number }) => ({
          component_id: `component-${item.key}`,
          component_key: item.key,
          build_number: item.build_number,
        }),
      ),
      scheduled_for_utc: payload.scheduled_for,
      status: "PENDING",
      deployment_id: null,
      release_bundle_id: null,
      error_message: null,
      created_at: "2026-08-26T08:00:00Z",
      updated_at: "2026-08-26T08:00:00Z",
      started_at: null,
      finished_at: null,
    };
    await route.fulfill({ json: createdSchedule });
  });
  await page.route("**/api/v1/schedules?**", (route) =>
    route.fulfill({
      json: {
        items: createdSchedule ? [createdSchedule] : [],
        total: createdSchedule ? 1 : 0,
      },
    }),
  );
  await page.route("**/api/v1/schedules/new-schedule", (route) =>
    route.fulfill({ json: createdSchedule }),
  );
  await page.goto("/");

  await page
    .getByRole("button", { name: "Schedule", exact: true })
    .first()
    .click();
  const first = page.getByRole("checkbox", { name: "first@example.com" });
  const second = page.getByRole("checkbox", { name: "second@example.com" });
  await expect(first).toBeChecked();
  await expect(second).toBeChecked();

  await second.uncheck();
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page
    .getByRole("button", { name: "Schedule", exact: true })
    .first()
    .click();

  await expect(first).toBeChecked();
  await expect(second).not.toBeChecked();

  await page.getByLabel("Release version").fill("v1.5.0");
  await page.getByLabel("Update details").fill("Fix schedule notifications");
  await page.getByRole("button", { name: "Create schedule" }).click();
  await expect.poll(() => submittedRecipients).toEqual(["first@example.com"]);
  await expect(page).toHaveURL(/view=workflows/);
  await expect(page).toHaveURL(/workflow_kind=schedule/);
  await expect(page).toHaveURL(/selected=schedule%3Anew-schedule/);
});

test("redirects the former deployments view into the combined workflow page", async ({
  page,
}) => {
  await page.goto("/?view=deployments");

  await expect(page).toHaveURL(/view=workflows/);
  await expect(page).toHaveURL(/workflow_kind=deployment/);
  await expect(
    page.getByRole("heading", { name: "Workflow runs" }),
  ).toBeVisible();
  await expect(page.getByLabel("Type")).toHaveValue("deployment");
  await expect(
    page.getByRole("link", { name: "Deployments", exact: true }),
  ).not.toBeVisible();
});

test("shows creation and execution state for every workflow", async ({
  page,
}, testInfo) => {
  const bundle = {
    id: "bundle-workflow",
    mode: "BUNDLE",
    status: "DEPLOYING",
    target: "production",
    workflow_instance_id: "workflow-instance",
    current_stage: "WAIT_BACKEND_DEPLOYMENT",
    failed_stage: null,
    error_code: null,
    error_message: null,
    requested_by: "release-operator",
    created_at: "2026-08-26T07:20:00Z",
    updated_at: "2026-08-26T07:22:00Z",
    started_at: "2026-08-26T07:21:00Z",
    failed_at: null,
    finished_at: null,
    deployments: [],
  };
  const standalone = {
    id: "standalone-workflow",
    release_bundle_id: null,
    component: "frontend",
    status: "SUCCESS",
    target: "production",
    source_build_number: 101,
    promotion_build_number: 102,
    commit_sha: "frontend12345678",
    current_stage: null,
    failed_stage: null,
    error_code: null,
    error_message: null,
    cancel_reason: null,
    created_at: "2026-08-26T07:10:00Z",
    updated_at: "2026-08-26T07:14:00Z",
    started_at: "2026-08-26T07:11:00Z",
    finished_at: "2026-08-26T07:14:00Z",
  };
  const bundledDeployment = {
    ...standalone,
    id: "bundle-child",
    release_bundle_id: bundle.id,
    component: "backend",
    source_build_number: 202,
  };

  await page.route("**/api/v1/releases?**", (route) =>
    route.fulfill({
      json: { items: [bundle], total: 1, limit: 100, offset: 0 },
    }),
  );
  await page.route("**/api/v1/deployments?**", (route) =>
    route.fulfill({
      json: {
        items: [standalone, bundledDeployment],
        total: 2,
        limit: 100,
        offset: 0,
      },
    }),
  );
  await page.route("**/api/v1/deployments/standalone-workflow", (route) =>
    route.fulfill({ json: standalone }),
  );
  await page.route(
    "**/api/v1/deployments/standalone-workflow/events",
    (route) => route.fulfill({ json: { items: [] } }),
  );
  await page.route("**/api/v1/releases/bundle-workflow", (route) =>
    route.fulfill({ json: bundle }),
  );
  await page.route("**/api/v1/releases/bundle-workflow/events", (route) =>
    route.fulfill({ json: { items: [] } }),
  );
  await page.route("**/api/v1/workflows/BUNDLE/definition", (route) =>
    route.fulfill({ contentType: "application/xml", body: bundleBpmn }),
  );

  await page.goto("/?view=workflows");

  await expect(
    page.getByRole("heading", { name: "Workflow runs" }),
  ).toBeVisible();
  await expect(page.locator(".history-row")).toHaveCount(2);
  await expect(page.locator(".history-list")).toContainText(
    "Wait Backend Deployment",
  );
  await expect(page.locator(".history-list")).toContainText(
    "Completed successfully",
  );
  await expect(
    page.getByRole("region", { name: "Workflow lifecycle" }),
  ).toContainText("Execution started");

  const canvas = page.locator("#bpmn-canvas");
  const task = (id: string) =>
    canvas.locator(`[data-element-id="${id}"] .djs-visual`);
  await expect(task("COMPLETE_PUBLISH")).toBeVisible();
  await page.getByRole("button", { name: "Fit", exact: true }).click();
  const firstRow = (await task("VALIDATE_FRONTEND").boundingBox())!;
  const secondRow = (await task("PROMOTE_FRONTEND").boundingBox())!;
  const thirdRow = (await task("PREPARE_RELEASE_VERSION").boundingBox())!;
  expect(firstRow.width).toBeGreaterThan(70);
  expect(secondRow.y).toBeGreaterThan(firstRow.y + firstRow.height);
  expect(thirdRow.y).toBeGreaterThan(secondRow.y + secondRow.height);
  expect(Math.abs(firstRow.x - secondRow.x)).toBeLessThan(1);
  expect(Math.abs(secondRow.x - thirdRow.x)).toBeLessThan(1);
  const frame = (await canvas.boundingBox())!;
  for (const visual of await canvas.locator(".djs-visual").all()) {
    const bounds = (await visual.boundingBox())!;
    expect(bounds.x).toBeGreaterThanOrEqual(frame.x - 2);
    expect(bounds.y).toBeGreaterThanOrEqual(frame.y - 2);
    expect(bounds.x + bounds.width).toBeLessThanOrEqual(
      frame.x + frame.width + 2,
    );
    expect(bounds.y + bounds.height).toBeLessThanOrEqual(
      frame.y + frame.height + 2,
    );
  }
  await expect(
    canvas.locator('[data-element-id="WAIT_BACKEND_DEPLOYMENT"]'),
  ).toHaveClass(/workflow-current/);
  await canvas.screenshot({ path: testInfo.outputPath("bundle-layout.png") });
  await page.getByLabel("Type").selectOption("deployment");
  await expect(page.locator(".history-row")).toHaveCount(1);
  await page.getByLabel("Status").selectOption("completed");
  await expect(page.locator(".history-list")).toContainText(
    "Completed successfully",
  );
  await expect(
    page.getByRole("button", { name: "Create legacy release" }),
  ).toBeHidden();
  await expect(
    page.getByRole("button", { name: "Add email address" }),
  ).toBeHidden();
});

test("renders the persisted BPMN for a legacy workflow", async ({ page }) => {
  const legacyRelease = {
    id: "legacy-workflow",
    repository: "release/legacy-api",
    branch: "main",
    commit_sha: "legacy12345678",
    environment: "pre-production",
    status: "DEPLOYING",
    created_at: "2026-08-26T08:00:00Z",
    updated_at: "2026-08-26T08:05:00Z",
    approved_by: "release-operator",
    approved_at: "2026-08-26T08:01:00Z",
    rejected_by: null,
    rejected_at: null,
    deploy_started_at: "2026-08-26T08:05:00Z",
    deploy_finished_at: null,
    message: null,
  };
  const createdLegacyRelease = {
    ...legacyRelease,
    id: "created-legacy-workflow",
    repository: "release/created-api",
    commit_sha: "created12345678",
    status: "PENDING",
    approved_by: null,
    approved_at: null,
    deploy_started_at: null,
    updated_at: "2026-08-26T09:00:00Z",
    created_at: "2026-08-26T09:00:00Z",
  };
  const releases: Array<typeof legacyRelease | typeof createdLegacyRelease> = [
    legacyRelease,
  ];
  const workflowState = {
    release_id: legacyRelease.id,
    definition_id: "release_approval",
    is_complete: false,
    current_element_ids: ["Task_execute_deployment"],
    completed_element_ids: [
      "Task_approval_gate",
      "Task_start_deployment",
      "Flow_start_approval",
      "Flow_approval_decision",
      "Flow_decision_approved",
      "Flow_deployment_started",
    ],
    steps: [],
    updated_at: "2026-08-26T08:05:00Z",
  };

  await page.route("**/api/v1/releases?**", (route) =>
    route.fulfill({
      json: { items: releases, total: releases.length, limit: 100, offset: 0 },
    }),
  );
  await page.route("**/api/v1/releases", async (route) => {
    releases.unshift(createdLegacyRelease);
    await route.fulfill({ json: createdLegacyRelease });
  });
  await page.route("**/api/v1/deployments?**", (route) =>
    route.fulfill({ json: { items: [], total: 0, limit: 100, offset: 0 } }),
  );
  await page.route("**/api/v1/releases/legacy-workflow", (route) =>
    route.fulfill({ json: legacyRelease }),
  );
  await page.route("**/api/v1/releases/legacy-workflow/workflow", (route) =>
    route.fulfill({ json: workflowState }),
  );
  await page.route("**/api/v1/releases/created-legacy-workflow", (route) =>
    route.fulfill({ json: createdLegacyRelease }),
  );
  await page.route(
    "**/api/v1/releases/created-legacy-workflow/workflow",
    (route) =>
      route.fulfill({
        json: {
          ...workflowState,
          release_id: createdLegacyRelease.id,
          current_element_ids: ["Task_approval_gate"],
          completed_element_ids: [],
        },
      }),
  );

  await page.goto("/?view=legacy&selected=legacy-workflow");

  await expect(page).toHaveURL(/view=workflows/);
  await expect(page).toHaveURL(/workflow_kind=legacy/);
  await expect(page).toHaveURL(/selected=legacy%3Alegacy-workflow/);
  await expect(page.getByLabel("Type")).toHaveValue("legacy");
  await expect(
    page.getByRole("button", { name: "Create legacy release" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "release/legacy-api" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "BPMN execution progress" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "Open XML" })).toHaveAttribute(
    "href",
    "/api/v1/workflows/release-definition",
  );

  await page.getByRole("button", { name: "Create legacy release" }).click();
  // Scoped to the legacy form: the component editor has repository fields too.
  const legacyForm = page.locator("#create-form");
  await legacyForm.getByLabel("Repository").fill("release/created-api");
  await legacyForm.getByLabel("Commit SHA").fill("created12345678");
  await page.getByRole("button", { name: "Create release" }).click();

  await expect(page).toHaveURL(/selected=legacy%3Acreated-legacy-workflow/);
  await expect(
    page.getByRole("heading", { name: "release/created-api" }),
  ).toBeVisible();
});

test("keeps deployment pending after scheduling and email notification", async ({
  page,
}) => {
  const schedule = {
    id: "pending-scheduled-workflow",
    mode: "FRONTEND_ONLY",
    frontend_build_number: 101,
    backend_build_number: null,
    target: "production",
    scheduled_for_utc: "2030-09-29T05:50:00Z",
    timezone: "Asia/Taipei",
    status: "PENDING",
    requested_by: "release-operator",
    notification_recipients: ["release-owner@example.com"],
    notification_status: "SENT",
    notification_sent_at: "2026-08-27T01:32:05Z",
    release_version: "v2.2",
    release_notes: "Scheduled release",
    deployment_id: null,
    release_bundle_id: null,
    error_message: null,
    created_at: "2026-08-27T01:32:03Z",
    updated_at: "2026-08-27T01:32:05Z",
    started_at: null,
    finished_at: null,
  };
  await page.route("**/api/v1/releases?**", (route) =>
    route.fulfill({ json: { items: [], total: 0, limit: 100, offset: 0 } }),
  );
  await page.route("**/api/v1/deployments?**", (route) =>
    route.fulfill({ json: { items: [], total: 0, limit: 100, offset: 0 } }),
  );
  await page.route("**/api/v1/schedules?**", (route) =>
    route.fulfill({
      json: { items: [schedule], total: 1, limit: 100, offset: 0 },
    }),
  );
  await page.route("**/api/v1/schedules/pending-scheduled-workflow", (route) =>
    route.fulfill({ json: schedule }),
  );
  await page.route("**/api/v1/workflows/SCHEDULED/definition", (route) =>
    route.fulfill({ contentType: "application/xml", body: scheduledBpmn }),
  );

  await page.goto("/?view=workflows");

  await expect(
    page.getByRole("heading", { name: "Workflow runs" }),
  ).toBeVisible();
  await expect(page.locator(".history-list")).toContainText("Waiting until");
  await expect(
    page.locator('[data-element-id="SCHEDULE_DEPLOYMENT"]'),
  ).toHaveClass(/workflow-completed/);
  await expect(
    page.locator('[data-element-id="SEND_EMAIL_NOTIFICATION"]'),
  ).toHaveClass(/workflow-completed/);
  await expect(
    page.locator('[data-element-id="WAIT_SCHEDULED_TIME"]'),
  ).toHaveClass(/workflow-current/);
  await expect(
    page.locator('[data-element-id="START_DEPLOYMENT"]'),
  ).toHaveClass(/workflow-pending/);
  await page.getByRole("button", { name: "Pending schedules" }).click();
  await expect(
    page.getByRole("button", { name: "Pending schedules" }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator('[data-element-id="CANCEL_SCHEDULE"]')).toHaveClass(
    /workflow-pending/,
  );
  const waitIcon = page.getByRole("img", { name: "Waiting task" });
  const emailIcon = page.getByRole("img", { name: "Email task" });
  await expect(waitIcon).toBeVisible();
  await expect(emailIcon).toBeVisible();
  await expect(waitIcon.locator("..")).toHaveCSS("left", "6px");
  await expect(emailIcon.locator("..")).toHaveCSS("left", "6px");
});

test("shows the cancelled branch for a cancelled schedule", async ({
  page,
}) => {
  const schedule = {
    id: "cancelled-scheduled-workflow",
    mode: "FRONTEND_ONLY",
    frontend_build_number: 101,
    backend_build_number: null,
    target: "production",
    scheduled_for_utc: "2030-09-29T05:50:00Z",
    timezone: "Asia/Taipei",
    status: "CANCELLED",
    requested_by: "release-operator",
    notification_recipients: ["release-owner@example.com"],
    notification_status: "SENT",
    notification_sent_at: "2026-08-27T01:32:05Z",
    release_version: "v2.2",
    release_notes: "Cancelled scheduled release",
    deployment_id: null,
    release_bundle_id: null,
    error_message: null,
    created_at: "2026-08-27T01:32:03Z",
    updated_at: "2026-08-27T01:35:05Z",
    started_at: null,
    finished_at: "2026-08-27T01:35:05Z",
  };
  await page.route("**/api/v1/releases?**", (route) =>
    route.fulfill({ json: { items: [], total: 0, limit: 100, offset: 0 } }),
  );
  await page.route("**/api/v1/deployments?**", (route) =>
    route.fulfill({ json: { items: [], total: 0, limit: 100, offset: 0 } }),
  );
  await page.route("**/api/v1/schedules?**", (route) =>
    route.fulfill({
      json: { items: [schedule], total: 1, limit: 100, offset: 0 },
    }),
  );
  await page.route(
    "**/api/v1/schedules/cancelled-scheduled-workflow",
    (route) => route.fulfill({ json: schedule }),
  );
  await page.route("**/api/v1/workflows/SCHEDULED/definition", (route) =>
    route.fulfill({ contentType: "application/xml", body: scheduledBpmn }),
  );

  await page.goto("/?view=workflows");

  await expect(page.locator(".detail-heading")).toContainText("CANCELLED");
  await expect(page.locator('[data-element-id="CANCEL_SCHEDULE"]')).toHaveClass(
    /workflow-cancelled/,
  );
  await expect(page.locator('[data-element-id="S_CANCELLED"]')).toHaveClass(
    /workflow-cancelled/,
  );
  await expect(page.locator('[data-element-id="END_CANCELLED"]')).toHaveClass(
    /workflow-cancelled/,
  );
  await expect(
    page.locator('[data-element-id="START_DEPLOYMENT"]'),
  ).toHaveClass(/workflow-cancelled/);
});

test("redirects the legacy schedules view and selects a pending schedule", async ({
  page,
}) => {
  let documentRequests = 0;
  page.on("request", (request) => {
    if (request.resourceType() === "document") documentRequests += 1;
  });
  const baseSchedule = {
    mode: "FRONTEND_ONLY",
    frontend_build_number: 101,
    backend_build_number: null,
    target: "pre-production",
    scheduled_for_utc: "2030-09-03T09:43:00Z",
    timezone: "Asia/Taipei",
    notification_recipients: [],
    deployment_id: null,
    release_bundle_id: null,
    error_message: null,
    created_at: "2026-08-26T07:27:00Z",
    updated_at: "2026-08-26T07:27:00Z",
    started_at: null,
    finished_at: null,
  };
  const schedules = [
    {
      ...baseSchedule,
      id: "cancelled-schedule",
      status: "CANCELLED",
      requested_by: "first-operator",
    },
    {
      ...baseSchedule,
      id: "pending-schedule",
      status: "PENDING",
      requested_by: "pending-operator",
    },
  ];
  await page.route("**/api/v1/schedules?**", (route) =>
    route.fulfill({
      json: { items: schedules, total: 2, limit: 100, offset: 0 },
    }),
  );
  await page.route("**/api/v1/schedules/*", (route) => {
    const schedule = schedules.find((item) =>
      route.request().url().endsWith(item.id),
    );
    return route.fulfill({ json: schedule });
  });

  await page.goto("/?view=schedules");
  await expect(page).toHaveURL(/view=workflows/);
  await expect(page).toHaveURL(/workflow_kind=schedule/);
  await expect(page.getByLabel("Type")).toHaveValue("schedule");
  await page
    .getByRole("link", { name: /Scheduled Frontend #101 PENDING/ })
    .click();

  await expect(page).toHaveURL(/selected=schedule%3Apending-schedule/);
  await expect(page.locator("#release-detail")).toContainText(
    "pending-operator",
  );
  await expect(
    page.getByRole("button", { name: "Cancel schedule" }),
  ).toBeVisible();
  expect(documentRequests).toBe(1);

  const selectedUrl = page.url();
  await page.locator("#release-detail").click({ position: { x: 8, y: 8 } });
  await expect(page).toHaveURL(selectedUrl);
  expect(documentRequests).toBe(1);
});

test("edits a project's components and its upstream connections", async ({
  page,
}) => {
  await page.goto("/?view=projects");

  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Release" })).toBeVisible();
  // The table is the deployment order, and this project deploys frontend first.
  await expect(page.locator(".component-row .component-position")).toHaveText([
    "1",
    "2",
  ]);
  await expect(page.locator(".component-row").first()).toContainText(
    "Frontend",
  );
  await expect(page.locator("#release-detail")).toContainText("Release Drone");

  await page.getByRole("button", { name: "Add component" }).click();
  await expect(
    page.getByRole("heading", { name: "Add component" }),
  ).toBeVisible();
  // Both connection dropdowns offer the real connections plus "inherit".
  await expect(page.locator("#component-drone-connection option")).toHaveText([
    "Inherit from the project",
    "Release Drone (default)",
  ]);
  await page.getByRole("button", { name: "Cancel" }).click();

  await page.getByRole("button", { name: "Connections", exact: true }).click();
  await expect(page).toHaveURL(/view=connections/);
  await expect(page.locator(".history-row").first()).toContainText(
    "Release Drone",
  );
  await expect(page.locator("#release-detail")).toContainText("••••1234");
  // The stored secret is never rendered, only its hint.
  await expect(page.locator("#release-detail")).not.toContainText("token=");
});
