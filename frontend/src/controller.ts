// Transitional imperative adapter. React owns the shell; this module preserves the
// proven release/BPMN behavior while each workflow is migrated into typed hooks.
import { api as typedApi, apiText } from './api';
import type {
  BpmnMode,
  ComponentName,
  Deployment,
  DeploymentSchedule,
  DroneBuild,
  DroneBuildList,
  HistoryView,
  LegacyWorkflowState,
  Project,
  ProjectComponent,
  ProjectValidation,
  ReleaseBundle,
  RepositorySummary,
  UpstreamConnection,
  WorkflowEvent,
} from './domain';

// This adapter receives several generations of API payloads. New React code uses
// typedApi directly; the legacy boundary narrows incrementally as hooks replace it.
const api: any = typedApi;
const document: any = window.document;

const API = '/api/v1';
const SCHEDULE_RECIPIENTS_STORAGE_KEY = 'release-controller.schedule-recipients';
const PROJECT_STORAGE_KEY = 'release-controller.project';
const TERMINAL = new Set(['SUCCESS', 'FAILED', 'PARTIAL_FAILURE', 'CANCELLED', 'REJECTED']);
const HISTORY_VIEWS = new Set<HistoryView>(['releases', 'workflows', 'recipients', 'projects', 'connections']);
// Configuration rather than history: these two describe what a release can be,
// not what a release did.
const SETTINGS_VIEWS = new Set<HistoryView>(['projects', 'connections']);
const STAGE_LABELS: Record<string, string> = {
  VALIDATE_FRONTEND: 'Validate Frontend',
  VALIDATE_BACKEND: 'Validate Backend',
  CREATE_RELEASE_BUNDLE: 'Create Release Bundle',
  PROMOTE_BACKEND: 'Promote Backend',
  WAIT_BACKEND_DEPLOYMENT: 'Wait Backend Deployment',
  PROMOTE_FRONTEND: 'Promote Frontend',
  WAIT_FRONTEND_DEPLOYMENT: 'Wait Frontend Deployment',
  COMPLETE_RELEASE: 'Complete Release (v2.2)',
  COMPLETE_DEPLOYMENT: 'Complete Deployment',
  WAIT_PUBLISH_REQUEST: 'Wait Publish Request',
  PREPARE_RELEASE_VERSION: 'Prepare Release Version',
  PUBLISH_BACKEND_RELEASE: 'Publish Backend Release',
  PUBLISH_FRONTEND_RELEASE: 'Publish Frontend Release',
  COMPLETE_PUBLISH: 'Complete Publish',
};

interface LegacyState {
  projects: Project[];
  project: Project | null;
  // Keyed by component key. Which keys exist is a property of the selected
  // project, not of this module.
  builds: Record<ComponentName, DroneBuild[]>;
  branches: Record<ComponentName, string[]>;
  selectedBranch: Record<ComponentName, string | null>;
  selected: Record<ComponentName, number | null>;
  included: Record<ComponentName, boolean>;
  bundles: ReleaseBundle[];
  deployments: Deployment[];
  schedules: DeploymentSchedule[];
  recipients: any[];
  connections: UpstreamConnection[];
  // Keyed by project id: what the last "Check now" said about each component.
  validations: Record<string, ProjectValidation>;
  legacy: any[];
  events: WorkflowEvent[];
  giteaRepositories: Record<ComponentName, string | null>;
  workflowKindFilter: WorkflowKindFilter;
  workflowStatusFilter: WorkflowStatusFilter;
  view: HistoryView;
  selectedId: string | null;
}

type WorkflowHistoryKind = 'schedule' | 'bundle' | 'deployment' | 'legacy';
type WorkflowKindFilter = 'all' | WorkflowHistoryKind;
type WorkflowStatusFilter = 'all' | 'pending' | 'running' | 'completed' | 'failed';
const WORKFLOW_KIND_FILTERS = new Set<WorkflowKindFilter>(['all', 'schedule', 'bundle', 'deployment', 'legacy']);
const WORKFLOW_STATUS_FILTERS = new Set<WorkflowStatusFilter>(['all', 'pending', 'running', 'completed', 'failed']);

interface WorkflowHistoryItem {
  id: string;
  recordId: string;
  workflowKind: WorkflowHistoryKind;
  status: string;
  created_at?: string;
  updated_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  record: any;
}

// A factory rather than a literal: signing out and back in has to start from
// these values again, and reassigning `state` would strip every closure that
// captured it.
function initialLegacyState(): LegacyState {
  return {
    projects: [],
    project: null,
    builds: {},
    branches: {},
    selectedBranch: {},
    selected: {},
    included: {},
    bundles: [],
    deployments: [],
    schedules: [],
    recipients: [],
    connections: [],
    validations: {},
    legacy: [],
    events: [],
    giteaRepositories: {},
    workflowKindFilter: 'all',
    workflowStatusFilter: 'all',
    view: 'releases',
    selectedId: null,
  };
}

const state: LegacyState = initialLegacyState();

// Filled by queryElements() when initialize() runs, not when this module is
// evaluated. Signing out unmounts the React shell, so a second sign-in renders a
// fresh set of nodes and every one of these has to be looked up again.
const elements: any = {};

function queryElements() {
  Object.assign(elements, {
    target: document.querySelector('#release-target'),
    history: document.querySelector('#history-list'),
    historyTitle: document.querySelector('#history-title'),
    historyEyebrow: document.querySelector('#history-eyebrow'),
    settingsSwitcher: document.querySelector('#settings-switcher'),
    workflowFilters: document.querySelector('#workflow-filters'),
    workflowKindFilter: document.querySelector('#workflow-kind-filter'),
    workflowStatusFilter: document.querySelector('#workflow-status-filter'),
    detail: document.querySelector('#release-detail'),
    pageShell: document.querySelector('#page-shell'),
    launcher: document.querySelector('#launch-panel'),
    health: document.querySelector('#service-health'),
    droneHealth: document.querySelector('#drone-health'),
    giteaHealth: document.querySelector('#gitea-health'),
    toast: document.querySelector('#toast'),
    confirmation: document.querySelector('#confirmation-dialog'),
    confirmationSummary: document.querySelector('#confirmation-summary'),
    promoteDialog: document.querySelector('#promote-dialog'),
    promoteSummary: document.querySelector('#promote-summary'),
    createDialog: document.querySelector('#create-dialog'),
    decisionDialog: document.querySelector('#decision-dialog'),
    scheduleDialog: document.querySelector('#schedule-dialog'),
    scheduleSummary: document.querySelector('#schedule-summary'),
    scheduleRecipientOptions: document.querySelector('#schedule-recipient-options'),
    recipientDialog: document.querySelector('#recipient-dialog'),
    publishDialog: document.querySelector('#publish-dialog'),
    publishSummary: document.querySelector('#publish-summary'),
    projectDialog: document.querySelector('#project-dialog'),
    componentDialog: document.querySelector('#component-dialog'),
    connectionDialog: document.querySelector('#connection-dialog'),
  });
}

let bpmnViewer: any = null;
let bpmnRenderToken = 0;
let bpmnMode: BpmnMode | null = null;
let bpmnContainer: any = null;
let bpmnMarkers: Array<[string, string]> = [];
const bpmnXmlCache = new Map<string, string>();
// The viewer and its stylesheets are the heaviest thing here and only a record
// with a diagram needs them, so they load on first use rather than with this
// module. The promise is the cache: concurrent callers share one download.
let bpmnViewerModule: Promise<typeof import('./bpmnViewer')> | null = null;

function loadBpmnViewer() {
  bpmnViewerModule ??= import('./bpmnViewer');
  return bpmnViewerModule;
}

let currentDetailRecord: any = null;
let initialized = false;
let toastTimer: ReturnType<typeof setTimeout> | undefined;
let pollTimer: ReturnType<typeof setInterval> | undefined;
// Bumped by teardown(). Work already in flight when a session ends compares its
// own value against this and drops out instead of writing into the next one.
let generation = 0;

// Rendering is idempotent on purpose. Every render path below rebuilds the full
// markup for a region, so without these guards the 5s poll (and every click that
// triggers a re-render) replaces live DOM with identical DOM: the panel blanks for
// a frame, scroll position and hover state are lost, and the BPMN canvas is torn
// down and re-imported. Comparing the generated markup first makes a no-op render
// cost nothing and leaves the DOM — including the bpmn-js viewer — untouched.
const renderedHtml = new WeakMap<object, string>();

// The BPMN viewer lives inside the detail markup, so replacing that markup must
// dispose of it first — and must not happen at all when nothing changed.
function patchDetail(html: string): boolean {
  if (renderedHtml.get(elements.detail) === html) return false;
  destroyBpmnViewer();
  renderedHtml.set(elements.detail, html);
  elements.detail.innerHTML = html;
  return true;
}

function rowKey(node: any): string | null {
  return node?.dataset?.historyId ?? node?.dataset?.buildNumber ?? null;
}

// Keyed reconcile: rows that are unchanged keep their exact DOM node, rows whose
// content changed are patched in place, and only genuinely new rows are created.
// A selection change therefore becomes a className toggle instead of a full rebuild.
function patchRows(container: any, html: string): boolean {
  if (!container) return false;
  if (renderedHtml.get(container) === html) return false;
  renderedHtml.set(container, html);

  const staging = document.createElement('div');
  staging.innerHTML = html;
  const existing = new Map<string, any>();
  for (const node of Array.from(container.children) as any[]) {
    const key = rowKey(node);
    if (key !== null) existing.set(key, node);
  }
  let cursor = container.firstElementChild;
  for (const next of Array.from(staging.children) as any[]) {
    const key = rowKey(next);
    const current = key !== null ? existing.get(key) : undefined;
    let target = next;
    if (current) {
      if (current.outerHTML !== next.outerHTML) {
        for (const attribute of Array.from(next.attributes) as any[]) {
          if (current.getAttribute(attribute.name) !== attribute.value) {
            current.setAttribute(attribute.name, attribute.value);
          }
        }
        for (const attribute of Array.from(current.attributes) as any[]) {
          if (!next.hasAttribute(attribute.name)) current.removeAttribute(attribute.name);
        }
        if (current.innerHTML !== next.innerHTML) current.innerHTML = next.innerHTML;
      }
      target = current;
      existing.delete(key as string);
    }
    if (cursor === target) cursor = target.nextElementSibling;
    else container.insertBefore(target, cursor);
  }
  while (cursor) {
    const doomed = cursor;
    cursor = cursor.nextElementSibling;
    doomed.remove();
  }
  return true;
}

// One node, reused. escapeHtml is synchronous and never re-entrant, so there is
// no state to leak between calls -- and it is called ~70 times per rendered row.
const ESCAPE_NODE = document.createElement('span');

function escapeHtml(value: unknown) {
  ESCAPE_NODE.textContent = value ?? '';
  return ESCAPE_NODE.innerHTML;
}

function shortSha(value?: string | null) {
  return value ? value.slice(0, 8) : 'unknown';
}

// Constructing an Intl formatter is far more expensive than using one, and this
// runs once per history row and once per workflow event -- every 5s poll.
const TIME_FORMAT = new Intl.DateTimeFormat(undefined, {
  month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
});

function formatTime(value?: string | Date | null) {
  if (!value) return '—';
  return TIME_FORMAT.format(new Date(value));
}

function showToast(message: string, error = false) {
  elements.toast.textContent = message;
  elements.toast.className = `toast visible${error ? ' error' : ''}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { elements.toast.className = 'toast'; }, 3500);
}

function activeComponents(): ProjectComponent[] {
  return (state.project?.components || [])
    .filter((component) => component.is_active)
    .slice()
    .sort((left, right) => left.position - right.position);
}

// Hoisted: a literal in the function body is recompiled on every call. Both are
// only ever used with String#replace, which resets lastIndex itself, so the /g
// flag carries no state between calls.
const COMPONENT_KEY_SEPARATORS = /[-_]+/g;
const COMPONENT_KEY_INITIALS = /\b\w/g;

// Rebuilt whenever state.project changes rather than scanned per call: the
// history rows call this once per record, per render.
let componentLabels = new Map<string, string>();

function rebuildComponentLabels() {
  componentLabels = new Map(
    (state.project?.components || []).map((component) => [component.key, component.display_name]),
  );
}

/** What to call a component in the interface. Falls back to a readable form of
 * the key, so a record whose component has since been renamed still shows
 * something meaningful rather than blank. */
function componentLabel(component: ComponentName): string {
  const registered = componentLabels.get(component);
  if (registered) return registered;
  return String(component || '')
    .replace(COMPONENT_KEY_SEPARATORS, ' ')
    .replace(COMPONENT_KEY_INITIALS, (character) => character.toUpperCase()) || 'Component';
}

/** The components a combined release would cover: active, ticked, and with a
 * build chosen. */
function includedComponents(): ProjectComponent[] {
  return activeComponents().filter(
    (component) => state.included[component.key] !== false && selectedBuild(component.key),
  );
}

function selectedBuild(component: ComponentName): DroneBuild | undefined {
  return visibleBuilds(component).find((build) => build.number === state.selected[component]);
}

function visibleBuilds(component: ComponentName): DroneBuild[] {
  const branch = state.selectedBranch[component];
  return (state.builds[component] || []).filter((build) => !branch || build.branch === branch);
}

function selectFirstPromotable(component: ComponentName) {
  state.selected[component] = visibleBuilds(component).find((build) => build.promotable)?.number ?? null;
}

function renderSource(component: ComponentName, repository: RepositorySummary) {
  const repositoryLabel = document.querySelector(`#${CSS_ID(component)}-repo`);
  if (!repositoryLabel) return;
  repositoryLabel.textContent = repository.slug;
  repositoryLabel.title = repository.link || repository.slug;

  const branchSelect = document.querySelector(`#${CSS_ID(component)}-branch`);
  branchSelect.replaceChildren(...state.branches[component].map((branch) => new Option(branch, branch)));
  branchSelect.disabled = state.branches[component].length === 0;
  branchSelect.value = state.selectedBranch[component] || '';
}

function renderBuilds(component: ComponentName, error: string | null = null) {
  const list = document.querySelector(`#${CSS_ID(component)}-builds`);
  if (!list) return;
  if (error) {
    patchRows(list, `<div class="build-error"><strong>Builds unavailable</strong><span>${escapeHtml(error)}</span><button data-retry-builds="${escapeHtml(component)}">Retry</button></div>`);
    return;
  }
  const builds = visibleBuilds(component);
  if (!builds.length) {
    patchRows(list, '<div class="loading-row">No Drone builds found for this branch.</div>');
    return;
  }
  patchRows(list, builds.map((build) => `
    <button class="build-row ${state.selected[component] === build.number ? 'selected' : ''}" data-build-number="${build.number}" ${build.promotable ? '' : 'disabled'}>
      <span class="build-number">#${build.number}</span>
      <span class="build-copy"><strong>${escapeHtml(build.commit_message || 'No commit message')}</strong><span>${escapeHtml(build.branch)} · ${shortSha(build.commit_sha)} · ${escapeHtml(build.author || 'unknown')}</span></span>
      <span class="drone-status ${escapeHtml(build.status)}">${escapeHtml(build.status)}</span>
      <span class="promotable-text">${build.promotable ? 'Promotable' : 'Not promotable'}</span>
    </button>`).join(''));
}

/** The component grid is rebuilt when the project changes, not when a build is
 * selected: renderBuilds patches rows in place so the DOM nodes survive. */
function renderComponentCards() {
  const grid = document.querySelector('#component-grid');
  if (!grid) return;
  const components = activeComponents();
  if (!components.length) {
    grid.innerHTML = '<div class="loading-row">This project has no active components. Add one under Projects.</div>';
    return;
  }
  grid.innerHTML = components.map((component) => `
    <article class="component-card" data-component="${escapeHtml(component.key)}">
      <header>
        <div>
          <span class="component-kicker">Component ${component.position}</span>
          <h2>${escapeHtml(component.display_name)}</h2>
        </div>
        <div class="component-source">
          <label class="component-include"><input type="checkbox" data-include="${escapeHtml(component.key)}" checked /> Include</label>
          <span class="repo-label" id="${escapeHtml(component.key)}-repo" title="${escapeHtml(component.drone_slug)}">Loading repository…</span>
          <label>Branch
            <select id="${escapeHtml(component.key)}-branch" disabled><option>Loading…</option></select>
          </label>
        </div>
      </header>
      <div class="build-list" id="${escapeHtml(component.key)}-builds">
        <div class="loading-row">Loading ${escapeHtml(component.display_name)} builds…</div>
      </div>
      <footer>
        <span id="${escapeHtml(component.key)}-selection">No build selected</span>
        <div class="action-pair">
          <button class="button secondary" data-schedule-component="${escapeHtml(component.key)}" disabled>Schedule</button>
          <button class="button secondary" data-deploy-component="${escapeHtml(component.key)}" disabled>Deploy ${escapeHtml(component.display_name)} Only</button>
        </div>
      </footer>
    </article>`).join('');
}

function renderSelections() {
  const hasTarget = Boolean(elements.target.value.trim());
  for (const component of activeComponents()) {
    const build = selectedBuild(component.key);
    const selection = document.querySelector(`#${CSS_ID(component.key)}-selection`);
    if (selection) {
      selection.textContent = build
        ? `Selected #${build.number} · ${shortSha(build.commit_sha)}`
        : 'No build selected';
    }
    for (const attribute of ['data-deploy-component', 'data-schedule-component']) {
      const button = document.querySelector(`[${attribute}="${component.key}"]`);
      if (button) button.disabled = !build || !hasTarget;
    }
  }
  const order = includedComponents();
  const releaseOrder = document.querySelector('#release-order');
  if (releaseOrder) {
    releaseOrder.textContent = order.length
      ? order.map((component) => component.display_name).join(' → ')
      : 'Nothing selected';
  }
  const releaseButton = document.querySelector('#release-both');
  const scheduleButton = document.querySelector('#schedule-both');
  // A combined release needs at least two components; one on its own is what
  // the per-component buttons are for.
  const disabled = !hasTarget || order.length < 2;
  if (releaseButton) {
    releaseButton.disabled = disabled;
    releaseButton.textContent = order.length > 1
      ? `Release ${order.map((component) => component.display_name).join(' + ')}`
      : 'Release selected components';
  }
  if (scheduleButton) scheduleButton.disabled = disabled;
}

/** Component keys are slugs, so they are already valid in a selector; this only
 * exists to make that assumption visible at the call sites. */
function CSS_ID(key: string): string {
  return key;
}

async function loadBuilds(component: ComponentName) {
  const project = state.project;
  if (!project) return;
  try {
    const body: DroneBuildList = await api(
      `${API}/projects/${encodeURIComponent(project.key)}/components/${encodeURIComponent(component)}/builds?limit=100`,
    );
    state.builds[component] = body.items;
    state.branches[component] = body.branches;
    const preferredBranch = body.items.find((build) => build.promotable)?.branch
      || body.repository.default_branch
      || body.branches[0]
      || null;
    if (!state.selectedBranch[component] || !body.branches.includes(state.selectedBranch[component])) {
      state.selectedBranch[component] = preferredBranch;
    }
    if (!selectedBuild(component)) selectFirstPromotable(component);
    renderSource(component, body.repository);
    renderBuilds(component);
    renderSelections();
  } catch (error) {
    renderBuilds(component, error.message);
  }
}

/** Projects, their components, and which one to show. The target and the Gitea
 * repositories come from the selected project rather than from server-wide
 * configuration, because they differ per project now. */
/** Archived projects stay in `state.projects` so the settings view can find them
 * again; only these are offered as somewhere to release to. */
function releasableProjects(): Project[] {
  return state.projects.filter((project) => !project.is_archived);
}

function giteaRepositoriesFor(project: Project): Record<ComponentName, string | null> {
  return Object.fromEntries(
    project.components.map((component) => [component.key, component.gitea_slug || null]),
  );
}

/** Everything about the selected project's components that the launcher draws.
 * Compared as a string so a refresh that changed nothing redraws nothing. */
function componentSignature(): string {
  return activeComponents()
    .map((component) => [
      component.key,
      component.display_name,
      component.position,
      component.drone_slug,
    ].join('|'))
    .join('\n');
}

function renderProjectSelector() {
  const selector = document.querySelector('#project-selector');
  if (!selector) return;
  const options = releasableProjects();
  selector.replaceChildren(...options.map((project) => new Option(project.name, project.key)));
  selector.disabled = options.length < 2;
  if (state.project) selector.value = state.project.key;
}

let projectsLoad: Promise<void> | null = null;

/**
 * The launcher and a settings view can both want the project list at once --
 * opening `?view=projects` does exactly that on the first load. Loading it twice
 * would select a project twice and re-read every build list, so concurrent
 * callers share one request; the first caller's options are the ones that apply.
 */
function loadProjects(options: { keepSelection?: boolean } = {}): Promise<void> {
  if (!projectsLoad) {
    projectsLoad = readProjects(options).finally(() => { projectsLoad = null; });
  }
  return projectsLoad;
}

/** `keepSelection` is for a reload that must not disturb the launcher: the
 * settings view refreshes this list on every edit, and re-selecting would throw
 * away the operator's chosen build. */
async function readProjects({ keepSelection = false } = {}) {
  const body = await api(`${API}/projects?include_archived=true`);
  state.projects = body.items || [];
  const options = releasableProjects();
  const current = state.project
    ? options.find((project) => project.key === state.project!.key)
    : undefined;
  if (keepSelection && current) {
    const before = componentSignature();
    state.project = current;
    rebuildComponentLabels();
    state.giteaRepositories = giteaRepositoriesFor(current);
    renderProjectSelector();
    if (componentSignature() === before) {
      renderSelections();
      return;
    }
    // Adding, removing, reordering or repointing a component changes what the
    // launcher offers, so the cards are rebuilt and their builds re-read: the
    // repository behind a card may now be a different one.
    renderComponentCards();
    renderSelections();
    await Promise.allSettled(activeComponents().map((component) => loadBuilds(component.key)));
    return;
  }
  renderProjectSelector();
  if (!options.length) {
    state.project = null;
    rebuildComponentLabels();
    const grid = document.querySelector('#component-grid');
    if (grid) {
      grid.innerHTML = '<div class="loading-row">No project is configured yet. Add one under Projects.</div>';
    }
    return;
  }
  const remembered = window.localStorage?.getItem(PROJECT_STORAGE_KEY);
  const chosen = options.find((project) => project.key === remembered) || options[0];
  await selectProject(chosen.key);
}

async function selectProject(projectKey: string) {
  const project = state.projects.find((item) => item.key === projectKey);
  if (!project) return;
  state.project = project;
  rebuildComponentLabels();
  try {
    window.localStorage?.setItem(PROJECT_STORAGE_KEY, project.key);
  } catch {
    // A browser with storage disabled just does not remember the choice.
  }
  const selector = document.querySelector('#project-selector');
  if (selector) selector.value = project.key;
  state.builds = {};
  state.branches = {};
  state.selectedBranch = {};
  state.selected = {};
  state.included = {};
  state.giteaRepositories = giteaRepositoriesFor(project);
  elements.target.value = project.default_target;
  elements.target.placeholder = '';
  renderComponentCards();
  renderSelections();
  await Promise.allSettled(activeComponents().map((component) => loadBuilds(component.key)));
}

function statusBadge(status: string) {
  return `<span class="status-badge ${String(status).toLowerCase()}">${escapeHtml(status)}</span>`;
}

function workflowHistoryItems(): WorkflowHistoryItem[] {
  const schedules = state.schedules.map((record) => ({
    id: `schedule:${record.id}`,
    recordId: record.id,
    workflowKind: 'schedule' as const,
    status: record.status,
    created_at: record.created_at,
    updated_at: record.updated_at,
    started_at: record.started_at,
    finished_at: record.finished_at,
    record,
  }));
  const bundles = state.bundles.map((record) => ({
    id: `bundle:${record.id}`,
    recordId: record.id,
    workflowKind: 'bundle' as const,
    status: record.status,
    created_at: record.created_at,
    updated_at: record.updated_at,
    started_at: record.started_at,
    finished_at: record.finished_at,
    record,
  }));
  const deployments = state.deployments
    .filter((record: any) => !record.release_bundle_id)
    .map((record) => ({
      id: `deployment:${record.id}`,
      recordId: record.id,
      workflowKind: 'deployment' as const,
      status: record.status,
      created_at: record.created_at,
      updated_at: record.updated_at,
      started_at: record.started_at,
      finished_at: record.finished_at,
      record,
    }));
  const legacy = state.legacy.map((record) => ({
    id: `legacy:${record.id}`,
    recordId: record.id,
    workflowKind: 'legacy' as const,
    status: record.status,
    created_at: record.created_at,
    updated_at: record.updated_at,
    started_at: record.deploy_started_at,
    finished_at: record.deploy_finished_at || record.rejected_at,
    record,
  }));
  // created_at is ISO-8601, so lexicographic order is chronological order and a
  // plain comparison avoids Intl collation on every pair.
  return [...schedules, ...bundles, ...deployments, ...legacy].sort((left, right) => {
    const leftCreated = String(left.created_at || '');
    const rightCreated = String(right.created_at || '');
    if (leftCreated === rightCreated) return 0;
    return leftCreated < rightCreated ? 1 : -1;
  });
}

// Table rather than an if-chain of array literals: this runs once per record
// every time the workflow list is filtered.
const WORKFLOW_STATUS_GROUPS = new Map<string, WorkflowStatusFilter>([
  ['PENDING', 'pending'], ['APPROVED', 'pending'],
  ['RUNNING', 'running'], ['DEPLOYING', 'running'], ['PROMOTING', 'running'],
  ['WAITING', 'running'], ['PUBLISHING', 'running'],
  ['SUCCESS', 'completed'], ['SUCCEEDED', 'completed'], ['PUBLISHED', 'completed'],
  ['FAILED', 'failed'], ['PARTIAL_FAILURE', 'failed'],
  ['CANCELLED', 'failed'], ['REJECTED', 'failed'],
]);

function workflowStatusGroup(status: string): WorkflowStatusFilter {
  return WORKFLOW_STATUS_GROUPS.get(String(status || 'PENDING').toUpperCase()) ?? 'running';
}

function filteredWorkflowHistoryItems(): WorkflowHistoryItem[] {
  return workflowHistoryItems().filter((item) => {
    const kindMatches = state.workflowKindFilter === 'all'
      || item.workflowKind === state.workflowKindFilter;
    const statusMatches = state.workflowStatusFilter === 'all'
      || workflowStatusGroup(item.status) === state.workflowStatusFilter;
    return kindMatches && statusMatches;
  });
}

function currentHistory() {
  if (state.view === 'workflows') return filteredWorkflowHistoryItems();
  if (state.view === 'recipients') return state.recipients;
  if (state.view === 'projects') return state.projects;
  if (state.view === 'connections') return state.connections;
  return state.bundles;
}

function historyLabel(item: any) {
  if (state.view === 'workflows') {
    if (item.workflowKind === 'schedule') {
      const schedule = item.record as DeploymentSchedule;
      return `Scheduled ${scheduleBuildSummary(schedule)}`;
    }
    if (item.workflowKind === 'bundle') return `Bundle ${item.recordId.slice(0, 8)}`;
    if (item.workflowKind === 'deployment') {
      const deployment = item.record as Deployment;
      return `${componentLabel(deployment.component)} #${deployment.source_build_number}`;
    }
    return item.record.repository;
  }
  if (state.view === 'recipients') return item.email;
  if (state.view === 'projects') return item.name;
  if (state.view === 'connections') return item.name;
  return `Release ${item.id.slice(0, 8)}`;
}

function historyMeta(item: any) {
  if (state.view === 'workflows') {
    if (item.workflowKind === 'schedule') return `Runs ${formatTime(item.record.scheduled_for_utc)} · ${item.record.target}`;
    if (item.workflowKind === 'bundle') return `Combined release · ${item.record.target}`;
    if (item.workflowKind === 'deployment') return `Standalone ${item.record.component} · ${item.record.target}`;
    return `Legacy approval · ${item.record.environment}`;
  }
  if (state.view === 'recipients') return 'Saved schedule recipient';
  if (state.view === 'projects') {
    const active = (item.components || []).filter((component: ProjectComponent) => component.is_active);
    return `${item.key} · ${item.default_target} · ${active.length} of ${(item.components || []).length} components active`;
  }
  if (state.view === 'connections') return `${item.kind} · ${item.base_url} · token ${item.token_hint}`;
  const builds = (item.deployments || [])
    .map((deployment: Deployment) => `${componentLabel(deployment.component)} #${deployment.source_build_number}`)
    .join(' → ');
  return `${item.target}${builds ? ` · ${builds}` : ''}`;
}

/** What a schedule will release, read from its component list.  Rows written
 * before v3.0 carry no component list, only the two build-number columns. */
function scheduleBuildSummary(schedule: DeploymentSchedule): string {
  const selections = schedule.component_builds?.length
    ? schedule.component_builds.map((item) => `${componentLabel(item.component_key)} #${item.build_number}`)
    : [
      schedule.backend_build_number ? `Backend #${schedule.backend_build_number}` : null,
      schedule.frontend_build_number ? `Frontend #${schedule.frontend_build_number}` : null,
    ].filter(Boolean) as string[];
  return selections.join(' → ') || 'No components selected';
}

function workflowExecutionSummary(record: any): string {
  const status = String(record.status || 'PENDING');
  if (record.failed_stage) return `Failed at ${STAGE_LABELS[record.failed_stage] || record.failed_stage}`;
  if (record.current_stage) return STAGE_LABELS[record.current_stage] || record.current_stage;
  if (status === 'PENDING') {
    if (record.scheduled_for_utc) return `Waiting until ${formatTime(record.scheduled_for_utc)}`;
    return record.repository ? 'Waiting for approval' : 'Waiting to start';
  }
  if (status === 'RUNNING') return 'Starting deployment';
  if (status === 'APPROVED') return 'Waiting to deploy';
  if (status === 'DEPLOYING') return 'Deployment running';
  if (status === 'PROMOTING') return 'Promotion running';
  if (status === 'WAITING') return 'Waiting for prerequisite';
  if (status === 'SUCCESS' || status === 'SUCCEEDED') return 'Completed successfully';
  if (status === 'PARTIAL_FAILURE') return 'Completed with partial failure';
  if (status === 'REJECTED') return 'Rejected before deployment';
  if (status === 'CANCELLED') return 'Cancelled';
  if (status === 'FAILED') return 'Execution failed';
  return status;
}

function workflowRowLifecycle(item: WorkflowHistoryItem): string {
  const executionClass = String(item.status || 'pending').toLowerCase();
  return `<span class="workflow-lifecycle">
    <span class="workflow-lifecycle-step created"><i aria-hidden="true"></i><span><strong>Created</strong><small>${formatTime(item.created_at)}</small></span></span>
    <span class="workflow-lifecycle-step execution ${escapeHtml(executionClass)}"><i aria-hidden="true"></i><span><strong>Execution</strong><small>${escapeHtml(workflowExecutionSummary(item.record))}</small></span></span>
  </span>`;
}

function workflowLifecycleDetail(record: any): string {
  const startedAt = record.started_at || record.deploy_started_at;
  const finishedAt = record.finished_at || record.deploy_finished_at || record.rejected_at;
  return `<section class="workflow-lifecycle-detail" aria-label="Workflow lifecycle">
    <div><span>Workflow created</span><strong>${formatTime(record.created_at)}</strong></div>
    <div><span>Execution started</span><strong>${startedAt ? formatTime(startedAt) : 'Not started'}</strong></div>
    <div><span>Current execution</span><strong>${escapeHtml(workflowExecutionSummary(record))}</strong></div>
    <div><span>Execution finished</span><strong>${finishedAt ? formatTime(finishedAt) : 'Not finished'}</strong></div>
  </section>`;
}

function historySelectionHref(id: string) {
  const url = new URL(window.location.href);
  if (state.view === 'releases') url.searchParams.delete('view');
  else url.searchParams.set('view', state.view);
  url.searchParams.set('selected', id);
  return `${url.pathname}${url.search}${url.hash}`;
}

/** Projects and connections carry configuration state rather than run status,
 * but the row still wants one word for it. */
function historyStatus(item: any): string | null {
  if (state.view === 'projects') return item.is_archived ? 'ARCHIVED' : null;
  if (state.view === 'connections') return item.verify_status ? String(item.verify_status).toUpperCase() : 'UNVERIFIED';
  return item.status || null;
}

function renderHistory() {
  const labels = {
    releases: 'Release bundles',
    workflows: 'Workflow runs',
    recipients: 'Email recipients',
    projects: 'Projects',
    connections: 'Upstream connections',
  };
  elements.historyTitle.textContent = labels[state.view];
  elements.historyEyebrow.textContent = SETTINGS_VIEWS.has(state.view)
    ? 'Project settings'
    : 'Audit history';
  document.querySelector('#new-release').hidden = state.view !== 'workflows'
    || state.workflowKindFilter !== 'legacy';
  document.querySelector('#new-recipient').hidden = state.view !== 'recipients';
  document.querySelector('#new-project').hidden = state.view !== 'projects';
  document.querySelector('#new-connection').hidden = state.view !== 'connections';
  const items = currentHistory();
  if (!items.length) {
    patchRows(elements.history, `<div class="history-empty">No ${labels[state.view].toLowerCase()} yet.</div>`);
    return;
  }
  patchRows(elements.history, items.map((item) => {
    const status = historyStatus(item);
    return `
    <a class="history-row ${state.selectedId === item.id ? 'selected' : ''}" data-history-id="${item.id}" href="${escapeHtml(historySelectionHref(item.id))}">
      <span class="row-title"><strong>${escapeHtml(historyLabel(item))}</strong>${status ? statusBadge(status) : ''}</span>
      <span class="row-meta">${escapeHtml(historyMeta(item))}</span>
      ${state.view === 'workflows' ? workflowRowLifecycle(item) : `<time>${formatTime(item.updated_at || item.created_at)}</time>`}
    </a>`;
  }).join(''));
}

function deploymentStages(deployment: Deployment) {
  const suffix = deployment.component.toUpperCase();
  return [`VALIDATE_${suffix}`, `PROMOTE_${suffix}`, `WAIT_${suffix}_DEPLOYMENT`];
}

const STAGE_FAILURE_EVENTS = new Set(['STAGE_FAILED', 'PUBLISH_STAGE_FAILED']);
const STAGE_SUCCESS_EVENTS = new Set(['STAGE_SUCCEEDED', 'PUBLISH_STAGE_SUCCEEDED', 'PUBLISH_COMPLETED']);
// The BPMN overlay additionally treats a whole-release completion as success for
// every stage it covers; the per-deployment timeline above does not.
const ELEMENT_SUCCESS_EVENTS = new Set([
  'STAGE_SUCCEEDED', 'RELEASE_COMPLETED', 'PUBLISH_STAGE_SUCCEEDED', 'PUBLISH_COMPLETED',
]);

function groupEventsByStage(events: WorkflowEvent[]): Map<string, WorkflowEvent[]> {
  const grouped = new Map<string, WorkflowEvent[]>();
  for (const event of events) {
    const bucket = grouped.get(event.stage);
    if (bucket) bucket.push(event);
    else grouped.set(event.stage, [event]);
  }
  return grouped;
}

// state.events is always replaced wholesale, never mutated in place, so its
// identity is a sound cache key: one grouping pass serves every deployment card
// in a render instead of one full scan per stage.
let eventStageIndex: { source: WorkflowEvent[]; byStage: Map<string, WorkflowEvent[]> } | null = null;

function eventsByStage(): Map<string, WorkflowEvent[]> {
  if (eventStageIndex && eventStageIndex.source === state.events) return eventStageIndex.byStage;
  const byStage = groupEventsByStage(state.events);
  eventStageIndex = { source: state.events, byStage };
  return byStage;
}

function stageState(
  stageEvents: WorkflowEvent[] | undefined,
  stage: string,
  release: any,
  deployment?: Deployment | null,
) {
  let cancelled = false;
  let succeeded = false;
  for (const event of stageEvents || []) {
    if (deployment && event.deployment_id !== deployment.id) continue;
    if (STAGE_FAILURE_EVENTS.has(event.event_type)) return 'FAILED';
    if (event.event_type === 'DEPLOYMENT_CANCELLED') cancelled = true;
    else if (STAGE_SUCCESS_EVENTS.has(event.event_type)) succeeded = true;
  }
  if (cancelled) return 'CANCELLED';
  if (release?.current_stage === stage || deployment?.current_stage === stage) return 'CURRENT';
  if (succeeded) return 'COMPLETED';
  return 'PENDING';
}

const STAGE_SYMBOLS: Record<string, string> = {
  COMPLETED: '✓', CURRENT: '●', FAILED: '✕', CANCELLED: '—', PENDING: '○',
};

function renderStageTimeline(release: any, deployment: Deployment) {
  const byStage = eventsByStage();
  return `<ol class="stage-timeline">${deploymentStages(deployment).map((stage) => {
    const stageStatus = stageState(byStage.get(stage), stage, release, deployment);
    return `<li class="${stageStatus.toLowerCase()}"><span class="stage-symbol">${STAGE_SYMBOLS[stageStatus]}</span><div><strong>${STAGE_LABELS[stage]}</strong><small>${stageStatus}</small></div></li>`;
  }).join('')}</ol>`;
}

function renderDeploymentCard(deployment: Deployment, release: any = null) {
  return `<article class="deployment-card ${deployment.status.toLowerCase()}">
    <header><div><span class="eyebrow">${escapeHtml(deployment.component)}</span><h3>${escapeHtml(componentLabel(deployment.component))} deployment</h3></div>${statusBadge(deployment.status)}</header>
    <dl class="deployment-facts"><div><dt>Source build</dt><dd>#${deployment.source_build_number}</dd></div><div><dt>Promotion build</dt><dd>${deployment.promotion_build_number ? `#${deployment.promotion_build_number}` : 'Not created'}</dd></div><div><dt>Commit</dt><dd><code>${shortSha(deployment.commit_sha)}</code></dd></div><div><dt>Target</dt><dd>${escapeHtml(deployment.target)}</dd></div></dl>
    ${renderStageTimeline(release, deployment)}
    ${deployment.failed_stage ? `<div class="failure-box"><strong>Failed stage: ${escapeHtml(deployment.failed_stage)}</strong><span>${escapeHtml(deployment.error_code || 'UNKNOWN_RELEASE_ERROR')}</span><p>${escapeHtml(deployment.error_message || 'No safe error detail available.')}</p></div>` : ''}
    ${deployment.cancel_reason ? `<div class="cancel-box"><strong>CANCELLED</strong><p>${escapeHtml(deployment.cancel_reason)}</p></div>` : ''}
    <section class="publish-component"><div><span class="eyebrow">Publish</span><strong>${escapeHtml(deployment.version || 'Version not published')}</strong></div>${statusBadge(deployment.publish_status || 'NOT_PUBLISHED')}
      ${deployment.gitea_release_url ? `<a href="${escapeHtml(deployment.gitea_release_url)}" target="_blank" rel="noopener">Open Gitea Release</a>` : ''}
      ${deployment.publish_error_code ? `<div class="failure-box"><strong>${escapeHtml(deployment.publish_error_code)}</strong><p>${escapeHtml(deployment.publish_error_message || 'Publish failed')}</p></div>` : ''}
    </section>
  </article>`;
}

function publishAction(record: ReleaseBundle | Deployment, kind: 'bundle' | 'deployment') {
  const deploymentStatus = kind === 'bundle' ? (record as ReleaseBundle).deployment_status : record.status;
  if (deploymentStatus !== 'SUCCESS') return '';
  const publishStatus = record.publish_status || 'NOT_PUBLISHED';
  if (publishStatus === 'PUBLISHED') return '';
  const label = ['FAILED', 'PARTIAL_FAILURE'].includes(publishStatus)
    ? 'Retry Failed Publish'
    : publishStatus === 'PUBLISHING'
      ? 'Publishing…'
      : 'Publish Version';
  return `<button class="button primary" id="publish-version" data-publish-kind="${kind}" data-publish-id="${record.id}" ${publishStatus === 'PUBLISHING' ? 'disabled' : ''}>${label}</button>`;
}

function publishOverview(bundle: ReleaseBundle) {
  const records = bundle.publish_records || [];
  return `<section class="publish-overview"><div class="publish-heading"><div><span class="eyebrow">Publish version</span><h3>${escapeHtml(bundle.version || 'Not published')}</h3></div>${statusBadge(bundle.publish_status || 'NOT_PUBLISHED')}</div>
    <div class="publish-records">${records.map((record) => `<article><div><strong>${escapeHtml(componentLabel(record.component))}</strong><span>${escapeHtml(record.tag_name)} → <code>${shortSha(record.commit_sha)}</code></span></div>${statusBadge(record.status)}${record.gitea_release_url ? `<a href="${escapeHtml(record.gitea_release_url)}" target="_blank" rel="noopener">Open Gitea Release</a>` : ''}${record.error_code ? `<p>${escapeHtml(record.error_code)} · ${escapeHtml(record.error_message || '')}</p>` : ''}</article>`).join('') || '<p class="publish-empty">Deployment succeeded. This version has not been published to Gitea.</p>'}</div>
    ${bundle.publish_error_code ? `<div class="failure-box"><strong>${escapeHtml(bundle.publish_error_code)}</strong><p>${escapeHtml(bundle.publish_error_message || 'Publish failed')}</p></div>` : ''}
  </section>`;
}

function bpmnDefinitionPath(mode: BpmnMode): string {
  return mode === 'LEGACY'
    ? `${API}/workflows/release-definition`
    : `${API}/workflows/${mode}/definition`;
}

function bpmnViewerMarkup(mode: BpmnMode) {
  return `<section class="bpmn-section" aria-labelledby="bpmn-title">
    <div class="bpmn-heading"><div><span class="eyebrow">Live workflow</span><h3 id="bpmn-title">BPMN execution progress</h3></div><div class="bpmn-tools"><a href="${bpmnDefinitionPath(mode)}" target="_blank" rel="noopener">Open XML</a><button type="button" data-bpmn-zoom="out" aria-label="Zoom out">−</button><button type="button" data-bpmn-zoom="fit">Fit</button><button type="button" data-bpmn-zoom="in" aria-label="Zoom in">+</button></div></div>
    <div class="bpmn-legend"><span class="completed">Completed</span><span class="current">Current</span><span class="failed">Failed</span><span class="cancelled">Cancelled</span><span class="pending">Pending</span></div>
    <div id="bpmn-canvas" class="bpmn-canvas" data-bpmn-mode="${mode}"><div class="bpmn-loading">Loading BPMN…</div></div>
  </section>`;
}

function destroyBpmnViewer() {
  bpmnRenderToken += 1;
  if (bpmnViewer) bpmnViewer.destroy();
  bpmnViewer = null;
  bpmnMode = null;
  bpmnContainer = null;
  bpmnMarkers = [];
}

// Markers are the only thing that changes as a workflow advances. Swapping them on
// the live viewer avoids the destroy → fetch → importXML → fit-viewport cycle, which
// is what makes the diagram blink and lose the user's pan/zoom.
function applyBpmnMarkers(viewer: any, states: Record<string, string>) {
  const canvas = viewer.get('canvas');
  const registry = viewer.get('elementRegistry');
  for (const [elementId, marker] of bpmnMarkers) {
    if (registry.get(elementId)) canvas.removeMarker(elementId, marker);
  }
  bpmnMarkers = [];
  for (const [elementId, visualState] of Object.entries(states)) {
    if (!registry.get(elementId)) continue;
    const marker = `workflow-${visualState}`;
    canvas.addMarker(elementId, marker);
    bpmnMarkers.push([elementId, marker]);
  }
}

async function bpmnDefinition(mode: BpmnMode): Promise<string> {
  const cached = bpmnXmlCache.get(mode);
  if (cached) return cached;
  const xml = await apiText(bpmnDefinitionPath(mode));
  bpmnXmlCache.set(mode, xml);
  return xml;
}

function workflowElementStates(
  mode: BpmnMode,
  record: any,
  deployments: Deployment[],
  events: WorkflowEvent[],
) {
  if (mode === 'SCHEDULED') {
    const states: Record<string, string> = {
      SCHEDULE_START: 'completed',
      SCHEDULE_DEPLOYMENT: 'completed',
      SEND_EMAIL_NOTIFICATION: 'pending',
      WAIT_SCHEDULED_TIME: 'pending',
      SCHEDULE_TIMER: 'pending',
      CANCEL_SCHEDULE: 'pending',
      S4: 'pending',
      S_CANCELLED: 'pending',
      START_DEPLOYMENT: 'pending',
      SCHEDULE_END: 'pending',
      END_CANCELLED: 'pending',
    };
    if (record.notification_status === 'SENT') states.SEND_EMAIL_NOTIFICATION = 'completed';
    else if (record.notification_status === 'FAILED') states.SEND_EMAIL_NOTIFICATION = 'failed';
    else if (['PENDING', 'PROCESSING'].includes(record.notification_status)) states.SEND_EMAIL_NOTIFICATION = 'current';
    else if (!record.notification_recipients?.length) states.SEND_EMAIL_NOTIFICATION = 'cancelled';

    if (record.status === 'PENDING') states.WAIT_SCHEDULED_TIME = 'current';
    else if (record.status === 'CANCELLED') {
      states.WAIT_SCHEDULED_TIME = 'cancelled';
      states.SCHEDULE_TIMER = 'cancelled';
      states.CANCEL_SCHEDULE = 'cancelled';
      states.S4 = 'cancelled';
      states.S_CANCELLED = 'cancelled';
      states.START_DEPLOYMENT = 'cancelled';
      states.SCHEDULE_END = 'cancelled';
      states.END_CANCELLED = 'cancelled';
    } else {
      states.WAIT_SCHEDULED_TIME = 'completed';
      states.SCHEDULE_TIMER = 'completed';
      states.CANCEL_SCHEDULE = 'cancelled';
      states.S4 = 'completed';
      if (record.status === 'RUNNING') states.START_DEPLOYMENT = 'current';
      else if (record.status === 'FAILED') {
        states.START_DEPLOYMENT = 'failed';
        states.SCHEDULE_END = 'failed';
      } else if (record.status === 'SUCCEEDED') {
        states.START_DEPLOYMENT = 'completed';
        states.SCHEDULE_END = 'completed';
      }
    }
    return states;
  }
  if (mode === 'LEGACY') {
    const workflow = record as LegacyWorkflowState & { status?: string };
    const states: Record<string, string> = { StartEvent_release_created: 'completed' };
    for (const elementId of workflow.completed_element_ids) states[elementId] = 'completed';
    for (const elementId of workflow.current_element_ids) states[elementId] = 'current';
    if (workflow.status === 'FAILED') {
      states.Flow_result_failed = 'failed';
      states.EndEvent_failed = 'failed';
    } else if (workflow.status === 'REJECTED') {
      states.Flow_decision_rejected = 'cancelled';
      states.EndEvent_rejected = 'cancelled';
    }
    return states;
  }
  const stages = mode === 'BUNDLE'
    ? ['VALIDATE_FRONTEND', 'VALIDATE_BACKEND', 'CREATE_RELEASE_BUNDLE', 'PROMOTE_BACKEND', 'WAIT_BACKEND_DEPLOYMENT', 'PROMOTE_FRONTEND', 'WAIT_FRONTEND_DEPLOYMENT', 'COMPLETE_DEPLOYMENT', 'WAIT_PUBLISH_REQUEST', 'PREPARE_RELEASE_VERSION', 'PUBLISH_BACKEND_RELEASE', 'PUBLISH_FRONTEND_RELEASE', 'COMPLETE_PUBLISH']
    : [...deploymentStages(record), 'COMPLETE_DEPLOYMENT', 'WAIT_PUBLISH_REQUEST', 'PREPARE_RELEASE_VERSION', record.component === 'backend' ? 'PUBLISH_BACKEND_RELEASE' : 'PUBLISH_FRONTEND_RELEASE', 'COMPLETE_PUBLISH'];
  const states: Record<string, string> = record.preview ? {} : { START: 'completed' };
  const byStage = groupEventsByStage(events);
  const currentStages = new Set(deployments.map((deployment) => deployment.current_stage));
  for (const stage of stages) {
    let failed = false;
    let cancelled = false;
    let succeeded = false;
    for (const event of byStage.get(stage) || []) {
      if (STAGE_FAILURE_EVENTS.has(event.event_type)) { failed = true; break; }
      if (event.event_type === 'DEPLOYMENT_CANCELLED') cancelled = true;
      else if (ELEMENT_SUCCESS_EVENTS.has(event.event_type)) succeeded = true;
    }
    // A stage with none of these stays absent from `states`, exactly as before.
    if (failed) states[stage] = 'failed';
    else if (cancelled) states[stage] = 'cancelled';
    else if (record.current_stage === stage || currentStages.has(stage)) states[stage] = 'current';
    else if (succeeded) states[stage] = 'completed';
  }

  const cancelledFrontend = deployments.some((deployment) => deployment.component === 'frontend' && deployment.status === 'CANCELLED');
  if (cancelledFrontend) {
    states.PROMOTE_FRONTEND = 'cancelled';
    states.WAIT_FRONTEND_DEPLOYMENT = 'cancelled';
    states.COMPLETE_DEPLOYMENT = 'cancelled';
  }
  if (mode === 'BUNDLE') {
    const backend = deployments.find((deployment) => deployment.component === 'backend');
    if (backend?.status === 'SUCCESS' || ['SUCCESS', 'PARTIAL_FAILURE'].includes(record.status)) states.BACKEND_RESULT = 'completed';
    if (record.deployment_status === 'SUCCESS' || record.status === 'SUCCESS') {
      states.COMPLETE_DEPLOYMENT = 'completed';
      if ((record.publish_status || 'NOT_PUBLISHED') === 'NOT_PUBLISHED') states.WAIT_PUBLISH_REQUEST = 'current';
    }
    if (record.publish_status === 'PUBLISHED') {
      states.COMPLETE_PUBLISH = 'completed';
      states.END_SUCCESS = 'completed';
    } else if (['FAILED', 'PARTIAL_FAILURE'].includes(record.status)) {
      states.END_FAILED = 'failed';
    }
  } else if (record.status === 'SUCCESS') {
    states.COMPLETE_DEPLOYMENT = 'completed';
    if ((record.publish_status || 'NOT_PUBLISHED') === 'NOT_PUBLISHED') states.WAIT_PUBLISH_REQUEST = 'current';
    if (record.publish_status === 'PUBLISHED') {
      states.COMPLETE_PUBLISH = 'completed';
      states.END = 'completed';
    }
  }
  return states;
}

function bpmnTaskIcon(kind: 'wait' | 'email'): HTMLElement {
  const icon = document.createElement('span');
  icon.className = `bpmn-task-icon ${kind}`;
  icon.setAttribute('role', 'img');
  icon.setAttribute('aria-label', kind === 'wait' ? 'Waiting task' : 'Email task');
  icon.title = kind === 'wait' ? 'Waiting task' : 'Email task';
  icon.innerHTML = kind === 'wait'
    ? '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8"/><path d="M12 7v5l3 2"/><path d="M9 2h6"/></svg>'
    : '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m4 7 8 6 8-6"/></svg>';
  return icon;
}

async function renderBpmnProgress(
  mode: BpmnMode,
  record: any,
  deployments: Deployment[],
  events: WorkflowEvent[],
) {
  const container = document.querySelector('#bpmn-canvas');
  if (!container) {
    destroyBpmnViewer();
    return;
  }
  const states = workflowElementStates(mode, record, deployments, events);
  // Same diagram, same still-mounted canvas: only the highlighting can have changed.
  // bpmnMode is set only once an import has finished, so this never runs mid-import.
  if (bpmnViewer && bpmnMode === mode && bpmnContainer === container && document.body.contains(container)) {
    try {
      applyBpmnMarkers(bpmnViewer, states);
      return;
    } catch {
      // Fall through to a full re-render if the live viewer is unusable.
    }
  }
  destroyBpmnViewer();
  const renderToken = bpmnRenderToken;
  try {
    // Independent: the definition is a fetch, the viewer is a chunk download.
    const [{ default: NavigatedViewer }, xml] = await Promise.all([
      loadBpmnViewer(),
      bpmnDefinition(mode),
    ]);
    // Two awaits have passed since the token was read -- a record selected in
    // the meantime owns the canvas now.
    if (renderToken !== bpmnRenderToken || !document.body.contains(container)) return;
    container.replaceChildren();
    const viewer: any = new NavigatedViewer({ container });
    bpmnViewer = viewer;
    await viewer.importXML(xml);
    if (renderToken !== bpmnRenderToken) return;
    bpmnMode = mode;
    bpmnContainer = container;
    const canvas = viewer.get('canvas');
    applyBpmnMarkers(viewer, states);
    if (mode === 'SCHEDULED') {
      const overlays = viewer.get('overlays');
      overlays.add('WAIT_SCHEDULED_TIME', {
        position: { top: 6, left: 6 },
        html: bpmnTaskIcon('wait'),
      });
      overlays.add('SEND_EMAIL_NOTIFICATION', {
        position: { top: 6, left: 6 },
        html: bpmnTaskIcon('email'),
      });
    }
    canvas.zoom('fit-viewport');
  } catch (error) {
    if (renderToken === bpmnRenderToken) container.innerHTML = `<div class="bpmn-error"><strong>Unable to render BPMN</strong><span>${escapeHtml(error.message)}</span></div>`;
  }
}

function renderWorkflowPreview() {
  currentDetailRecord = null;
  patchDetail(`<div class="detail-heading"><div><span class="eyebrow">Workflow definition</span><h2>Backend-first release</h2><p>Select or create a release to overlay its live execution state.</p></div></div>${bpmnViewerMarkup('BUNDLE')}`);
  void renderBpmnProgress('BUNDLE', { preview: true, current_stage: null, status: 'PENDING' }, [], []);
}

/** Deployments in the order they run: the order their components sit in.  A
 * bundle's deployments are created in that order, so creation order is the
 * honest fallback for a component that has since been removed. */
function deploymentOrder(deployments: Deployment[]): Deployment[] {
  const positions = new Map(
    (state.project?.components || []).map((component) => [component.key, component.position]),
  );
  return [...deployments].sort(
    (left, right) => (positions.get(left.component) ?? Number.MAX_SAFE_INTEGER)
      - (positions.get(right.component) ?? Number.MAX_SAFE_INTEGER),
  );
}

function renderBundleDetail(bundle: ReleaseBundle) {
  currentDetailRecord = bundle;
  const order = deploymentOrder(bundle.deployments);
  const attachment = bundle.attachment_filename ? ` · Attachment ${escapeHtml(bundle.attachment_filename)}` : '';
  const changed = patchDetail(`
    <div class="detail-heading"><div><span class="eyebrow">${escapeHtml(order.map((deployment) => componentLabel(deployment.component)).join(' → ') || 'Release')}</span><h2>Release ${bundle.id.slice(0, 8)}</h2><p>Target ${escapeHtml(bundle.target)} · Workflow ${escapeHtml(bundle.workflow_instance_id || 'not assigned')}${attachment}</p></div><div class="detail-actions"><span class="split-status">Deployment ${statusBadge(bundle.deployment_status || bundle.status)}</span><span class="split-status">Publish ${statusBadge(bundle.publish_status || 'NOT_PUBLISHED')}</span>${TERMINAL.has(bundle.status) ? '' : '<button class="button secondary" id="refresh-release">Refresh now</button>'}${publishAction(bundle, 'bundle')}</div></div>
    ${workflowLifecycleDetail(bundle)}
    ${bundle.failed_stage ? `<div class="release-failure"><strong>${bundle.status}: ${escapeHtml(bundle.failed_stage)}</strong><span>${escapeHtml(bundle.error_code || '')} · ${escapeHtml(bundle.error_message || '')}</span></div>` : ''}
    ${publishOverview(bundle)}
    ${bpmnViewerMarkup('BUNDLE')}
    <div class="deployment-grid">${order.map((deployment) => renderDeploymentCard(deployment, bundle)).join('')}</div>
    <section class="event-section"><div class="subheading"><span class="eyebrow">Append-only audit</span><h3>Workflow events</h3></div><ol class="event-list">${state.events.map((event) => `<li class="${event.event_type.toLowerCase()}"><time>${formatTime(event.created_at)}</time><strong>${escapeHtml(event.event_type)}</strong><span>${escapeHtml(STAGE_LABELS[event.stage] || event.stage)}</span>${event.message ? `<p>${escapeHtml(event.message)}</p>` : ''}</li>`).join('') || '<li class="event-empty">No events recorded.</li>'}</ol></section>`);
  if (changed) {
    document.querySelector('#refresh-release')?.addEventListener('click', () => refreshSelectedBundle(true));
    document.querySelector('#publish-version')?.addEventListener('click', () => openPublishDialog(currentDetailRecord, 'bundle'));
  }
  void renderBpmnProgress('BUNDLE', bundle, bundle.deployments, state.events);
}

/** The bundled BPMN diagrams only depict a frontend and a backend.  Any other
 * component gets the stage timeline and no diagram, which is more honest than
 * showing one labelled with a component this deployment is not. */
function standaloneBpmnMode(component: ComponentName): BpmnMode | null {
  if (component === 'backend') return 'BACKEND_ONLY';
  if (component === 'frontend') return 'FRONTEND_ONLY';
  return null;
}

function renderStandaloneDetail(deployment: Deployment) {
  currentDetailRecord = deployment;
  const mode = standaloneBpmnMode(deployment.component);
  const attachment = deployment.attachment_filename ? ` · Attachment ${escapeHtml(deployment.attachment_filename)}` : '';
  const changed = patchDetail(`<div class="detail-heading"><div><span class="eyebrow">Standalone deployment</span><h2>${escapeHtml(componentLabel(deployment.component))} #${deployment.source_build_number}</h2><p>No non-participating component record is created.${attachment}</p></div><div class="detail-actions"><span class="split-status">Deployment ${statusBadge(deployment.status)}</span><span class="split-status">Publish ${statusBadge(deployment.publish_status || 'NOT_PUBLISHED')}</span>${TERMINAL.has(deployment.status) ? '' : '<button class="button secondary" id="refresh-deployment">Refresh now</button>'}${publishAction(deployment, 'deployment')}</div></div>${workflowLifecycleDetail(deployment)}${mode ? bpmnViewerMarkup(mode) : ''}${renderDeploymentCard(deployment)}`);
  if (changed) {
    document.querySelector('#refresh-deployment')?.addEventListener('click', () => refreshSelectedDeployment(true));
    document.querySelector('#publish-version')?.addEventListener('click', () => openPublishDialog(currentDetailRecord, 'deployment'));
  }
  if (mode) void renderBpmnProgress(mode, deployment, [], state.events);
  else destroyBpmnViewer();
}

function renderScheduleDetail(schedule: DeploymentSchedule) {
  currentDetailRecord = schedule;
  const builds = scheduleBuildSummary(schedule);
  const changed = patchDetail(`<div class="detail-heading"><div><span class="eyebrow">Persisted schedule</span><h2>${escapeHtml(builds)}</h2><p>Runs ${formatTime(schedule.scheduled_for_utc)} · ${escapeHtml(schedule.timezone)}</p></div><div class="detail-actions">${statusBadge(schedule.status)}${schedule.status === 'PENDING' ? '<button class="button secondary" id="cancel-schedule">Cancel schedule</button>' : ''}</div></div>
    ${workflowLifecycleDetail(schedule)}
    ${bpmnViewerMarkup('SCHEDULED')}
    <dl class="schedule-facts"><div><dt>Target</dt><dd>${escapeHtml(schedule.target)}</dd></div><div><dt>Version</dt><dd>${escapeHtml(schedule.release_version || '—')}</dd></div><div><dt>Update details</dt><dd>${escapeHtml(schedule.release_notes || '—')}</dd></div><div><dt>Attachment</dt><dd>${escapeHtml(schedule.attachment_filename || 'None')}</dd></div><div><dt>Requested by</dt><dd>${escapeHtml(schedule.requested_by || '—')}</dd></div><div><dt>Email notifications</dt><dd>${escapeHtml(schedule.notification_recipients.join(', ') || 'None selected')} · ${escapeHtml(schedule.notification_status || 'NOT_QUEUED')}</dd></div><div><dt>Result</dt><dd>${escapeHtml(schedule.release_bundle_id || schedule.deployment_id || 'Not triggered')}</dd></div></dl>
    ${schedule.error_message ? `<div class="release-failure"><strong>Schedule failed</strong><span>${escapeHtml(schedule.error_message)}</span></div>` : ''}`);
  if (changed) document.querySelector('#cancel-schedule')?.addEventListener('click', async () => {
    try {
      const updated = await api(`${API}/schedules/${schedule.id}`, { method: 'DELETE' });
      state.schedules = state.schedules.map((item) => item.id === updated.id ? updated : item);
      renderHistory();
      renderScheduleDetail(updated);
      showToast('Schedule cancelled');
    } catch (error) { showToast(error.message, true); }
  });
  void renderBpmnProgress('SCHEDULED', schedule, [], []);
}

function renderRecipientDetail(recipient: any) {
  currentDetailRecord = recipient;
  const changed = patchDetail(`<div class="detail-heading"><div><span class="eyebrow">Schedule address book</span><h2>Email recipient</h2><p>Available for selection when a deployment schedule is created.</p></div><div class="detail-actions"><button class="button secondary" id="delete-recipient">Delete</button></div></div>
    <div class="recipient-summary"><strong>${escapeHtml(recipient.email)}</strong><p>Added ${formatTime(recipient.created_at)}. Open Schedule release to include or exclude this address and reuse that selection next time.</p></div>`);
  if (changed) document.querySelector('#delete-recipient').addEventListener('click', async () => {
    try {
      await api(`${API}/notifications/recipients/${recipient.id}`, { method: 'DELETE' });
      state.recipients = state.recipients.filter((item) => item.id !== recipient.id);
      state.selectedId = null;
      renderHistory();
      if (state.recipients[0]) await selectHistory(state.recipients[0].id);
      else patchDetail('<div class="empty-state"><span class="empty-icon">@</span><strong>No recipients yet</strong><p>Add an email address to make it selectable in Schedule release.</p></div>');
      showToast('Email recipient deleted');
    } catch (error) { showToast(error.message, true); }
  });
}

function connectionName(connectionId?: string | null, fallback = 'Default for its kind'): string {
  if (!connectionId) return fallback;
  return state.connections.find((item) => item.id === connectionId)?.name || 'Unknown connection';
}

/** One row per component, in deployment order.  The order is data, so it is
 * edited here rather than being implied by anything in the code. */
function componentRows(project: Project): string {
  const components = [...project.components].sort((left, right) => left.position - right.position);
  if (!components.length) {
    return '<p class="component-table-empty">No components yet. A project cannot release until it has at least one.</p>';
  }
  const validation = state.validations[project.id];
  return `<ol class="component-table">${components.map((component, index) => {
    const check = validation?.checks.find((entry) => entry.component_id === component.id);
    return `<li class="component-row${component.is_active ? '' : ' inactive'}" data-component-id="${escapeHtml(component.id)}">
      <span class="component-position">${component.position}</span>
      <span class="component-identity">
        <strong>${escapeHtml(component.display_name)}</strong>
        <code>${escapeHtml(component.key)}</code>
        ${component.is_active ? '' : '<span class="component-flag">Inactive</span>'}
        ${component.publish_enabled ? '' : '<span class="component-flag">No Gitea release</span>'}
      </span>
      <span class="component-repos">
        <span>Drone <code>${escapeHtml(component.drone_slug)}</code></span>
        <span>Gitea <code>${escapeHtml(component.gitea_slug || 'not set')}</code></span>
        <span>Target <code>${escapeHtml(component.effective_target)}</code>${component.promote_target_override ? ' (override)' : ''}</span>
        ${check ? `<span class="component-check ${escapeHtml(check.status)}">${escapeHtml(check.status === 'ok' ? 'Reachable' : check.detail || 'Unreachable')}</span>` : ''}
      </span>
      <span class="component-controls">
        <button class="icon-button" data-move-component="${escapeHtml(component.id)}" data-direction="-1" title="Move earlier" aria-label="Move ${escapeHtml(component.display_name)} earlier"${index === 0 ? ' disabled' : ''}>↑</button>
        <button class="icon-button" data-move-component="${escapeHtml(component.id)}" data-direction="1" title="Move later" aria-label="Move ${escapeHtml(component.display_name)} later"${index === components.length - 1 ? ' disabled' : ''}>↓</button>
        <button class="button secondary" data-edit-component="${escapeHtml(component.key)}">Edit</button>
        <button class="button secondary" data-delete-component="${escapeHtml(component.key)}">Remove</button>
      </span>
    </li>`;
  }).join('')}</ol>`;
}

function validationSummary(project: Project): string {
  const validation = state.validations[project.id];
  if (!validation) return '';
  const conflicts = validation.conflicts.length
    ? `<ul class="validation-conflicts">${validation.conflicts.map((conflict) => `<li>${escapeHtml(conflict)}</li>`).join('')}</ul>`
    : '';
  return `<div class="validation-result ${escapeHtml(validation.status)}">
    <strong>${validation.status === 'ok' ? 'Every active component answered' : 'Some components did not answer'}</strong>
    ${conflicts}
  </div>`;
}

function renderProjectDetail(project: Project) {
  currentDetailRecord = project;
  const changed = patchDetail(`<div class="detail-heading"><div><span class="eyebrow">${project.is_archived ? 'Archived project' : 'Project registry'}</span><h2>${escapeHtml(project.name)}</h2><p><code>${escapeHtml(project.key)}</code> · default target ${escapeHtml(project.default_target)}</p></div><div class="detail-actions"><button class="button secondary" id="validate-project">Check upstreams</button><button class="button secondary" id="edit-project">Edit</button><button class="button secondary" id="archive-project">${project.is_archived ? 'Unarchive' : 'Archive'}</button></div></div>
    ${project.description ? `<p class="project-description">${escapeHtml(project.description)}</p>` : ''}
    ${validationSummary(project)}
    <dl class="deployment-facts">
      <div><dt>Drone connection</dt><dd>${escapeHtml(connectionName(project.drone_connection_id))}</dd></div>
      <div><dt>Gitea connection</dt><dd>${escapeHtml(connectionName(project.gitea_connection_id))}</dd></div>
      <div><dt>Components</dt><dd>${project.components.length}</dd></div>
    </dl>
    <section class="component-section"><div class="subheading"><span class="eyebrow">Deployment order</span><h3>Components</h3></div>${componentRows(project)}<button class="button primary" id="add-component">Add component</button></section>`);
  if (!changed) return;
  document.querySelector('#edit-project').addEventListener('click', () => openProjectDialog(currentDetailRecord));
  document.querySelector('#archive-project').addEventListener('click', () => setProjectArchived(currentDetailRecord));
  document.querySelector('#validate-project').addEventListener('click', () => validateProject(currentDetailRecord));
  document.querySelector('#add-component').addEventListener('click', () => openComponentDialog(currentDetailRecord, null));
  // The four above are bound to nodes patchDetail just created, so they go with
  // the markup. The component-row buttons are delegated from elements.detail,
  // which survives every patch -- binding those here would stack one listener
  // per project viewed, so they live in bindEvents() instead.
}

function renderConnectionDetail(connection: UpstreamConnection) {
  currentDetailRecord = connection;
  const projects = state.projects.filter((project) => project.drone_connection_id === connection.id
    || project.gitea_connection_id === connection.id
    || project.components.some((component) => component.drone_connection_id === connection.id
      || component.gitea_connection_id === connection.id));
  const changed = patchDetail(`<div class="detail-heading"><div><span class="eyebrow">${escapeHtml(connection.kind)} connection</span><h2>${escapeHtml(connection.name)}</h2><p>${escapeHtml(connection.base_url)}</p></div><div class="detail-actions"><button class="button secondary" id="test-connection">Test</button><button class="button secondary" id="edit-connection">Edit</button><button class="button secondary" id="delete-connection">Delete</button></div></div>
    <dl class="deployment-facts">
      <div><dt>Token</dt><dd><code>${escapeHtml(connection.token_hint)}</code></dd></div>
      <div><dt>Default for ${escapeHtml(connection.kind)}</dt><dd>${connection.is_default ? 'Yes' : 'No'}</dd></div>
      <div><dt>Last checked</dt><dd>${connection.verified_at ? formatTime(connection.verified_at) : 'Never'}</dd></div>
      <div><dt>Result</dt><dd>${escapeHtml(connection.verify_status || 'Not verified')}${connection.verify_detail ? ` · ${escapeHtml(connection.verify_detail)}` : ''}</dd></div>
    </dl>
    <div class="connection-usage"><strong>Used by</strong><p>${projects.length ? escapeHtml(projects.map((project) => project.name).join(', ')) : 'No project references this connection.'}</p></div>`);
  if (!changed) return;
  document.querySelector('#test-connection').addEventListener('click', () => testConnection(currentDetailRecord));
  document.querySelector('#edit-connection').addEventListener('click', () => openConnectionDialog(currentDetailRecord));
  document.querySelector('#delete-connection').addEventListener('click', () => deleteConnection(currentDetailRecord));
}

function legacyActions(release: any) {
  if (release.status === 'PENDING') return '<button class="button secondary" data-legacy-action="reject">Reject</button><button class="button primary" data-legacy-action="approve">Approve</button>';
  if (release.status === 'APPROVED') return '<button class="button primary" data-legacy-action="start">Start deployment</button>';
  if (release.status === 'DEPLOYING') return '<button class="button secondary" data-legacy-action="failed">Mark failed</button><button class="button primary" data-legacy-action="success">Mark success</button>';
  return '';
}

function renderLegacyDetail(release: any, workflow: LegacyWorkflowState) {
  currentDetailRecord = release;
  const changed = patchDetail(`<div class="detail-heading"><div><span class="eyebrow">Preserved v1 record</span><h2>${escapeHtml(release.repository)}</h2><p>${escapeHtml(release.environment)} · ${escapeHtml(release.branch)} · ${shortSha(release.commit_sha)}</p></div><div class="detail-actions">${statusBadge(release.status)}${legacyActions(release)}</div></div>${workflowLifecycleDetail(release)}${bpmnViewerMarkup('LEGACY')}<div class="legacy-summary"><strong>Original release data remains intact</strong><p>${escapeHtml(release.message || 'No message')}</p><dl class="deployment-facts"><div><dt>Approved by</dt><dd>${escapeHtml(release.approved_by || '—')}</dd></div><div><dt>Rejected by</dt><dd>${escapeHtml(release.rejected_by || '—')}</dd></div><div><dt>Created</dt><dd>${formatTime(release.created_at)}</dd></div></dl></div>`);
  if (changed) elements.detail.querySelectorAll('[data-legacy-action]').forEach((button: any) => button.addEventListener('click', () => legacyAction(currentDetailRecord, button.dataset.legacyAction)));
  void renderBpmnProgress('LEGACY', { ...workflow, status: release.status }, [], []);
}

async function selectHistory(id: string) {
  const selectedView = state.view;
  state.selectedId = id;
  renderHistory();
  const item = currentHistory().find((entry) => entry.id === id);
  if (!item) return;
  try {
    if (state.view === 'workflows') {
      const workflow = item as WorkflowHistoryItem;
      if (workflow.workflowKind === 'schedule') {
        const schedule = await api(`${API}/schedules/${workflow.recordId}`);
        if (state.selectedId !== id || state.view !== selectedView) return;
        state.schedules = state.schedules.map((entry) => entry.id === workflow.recordId ? schedule : entry);
        renderHistory();
        renderScheduleDetail(schedule);
      } else if (workflow.workflowKind === 'bundle') {
        const [bundle, eventBody] = await Promise.all([api(`${API}/releases/${workflow.recordId}`), api(`${API}/releases/${workflow.recordId}/events`)]);
        if (state.selectedId !== id || state.view !== selectedView) return;
        state.bundles = state.bundles.map((entry) => entry.id === workflow.recordId ? bundle : entry);
        state.events = eventBody.items;
        renderHistory();
        renderBundleDetail(bundle);
      } else if (workflow.workflowKind === 'deployment') {
        const [deployment, eventBody] = await Promise.all([api(`${API}/deployments/${workflow.recordId}`), api(`${API}/deployments/${workflow.recordId}/events`)]);
        if (state.selectedId !== id || state.view !== selectedView) return;
        state.deployments = state.deployments.map((entry) => entry.id === workflow.recordId ? deployment : entry);
        state.events = eventBody.items;
        renderHistory();
        renderStandaloneDetail(deployment);
      } else {
        const [release, workflowState] = await Promise.all([
          api(`${API}/releases/${workflow.recordId}`),
          api(`${API}/releases/${workflow.recordId}/workflow`),
        ]);
        if (state.selectedId !== id || state.view !== selectedView) return;
        state.legacy = state.legacy.map((entry) => entry.id === workflow.recordId ? release : entry);
        renderHistory();
        renderLegacyDetail(release, workflowState);
      }
    } else if (state.view === 'releases') {
      const [bundle, eventBody] = await Promise.all([api(`${API}/releases/${id}`), api(`${API}/releases/${id}/events`)]);
      if (state.selectedId !== id || state.view !== selectedView) return;
      state.bundles = state.bundles.map((entry) => entry.id === id ? bundle : entry);
      state.events = eventBody.items;
      renderBundleDetail(bundle);
    } else if (state.view === 'recipients') renderRecipientDetail(item);
    else if (state.view === 'projects') renderProjectDetail(item as Project);
    else if (state.view === 'connections') renderConnectionDetail(item as UpstreamConnection);
  } catch (error) { showToast(error.message, true); }
}

async function loadHistory() {
  const results = await Promise.allSettled([
    loadViewHistory('releases'),
    loadDeployments(),
    loadSchedules(),
    loadViewHistory('recipients'),
    // The settings views are not release history, but one of them may be the
    // view being opened -- straight from a URL, on the very first load.
    SETTINGS_VIEWS.has(state.view) ? loadSettingsData() : Promise.resolve(),
  ]);
  const resultIndexes = state.view === 'workflows'
    ? [0, 1, 2]
    : SETTINGS_VIEWS.has(state.view)
      ? [4]
      : [state.view === 'recipients' ? 3 : 0];
  const failedResult = resultIndexes.map((index) => results[index]).find((result) => result.status === 'rejected');
  if (failedResult?.status === 'rejected') {
    renderHistoryFailure(failedResult.reason);
    throw failedResult.reason;
  }
  await finishHistoryLoad();
}

async function loadSchedules() {
  state.schedules = (await api(`${API}/schedules?limit=100`)).items;
}

async function loadDeployments() {
  state.deployments = (await api(`${API}/deployments?limit=100`)).items;
}

/**
 * Both settings views need both lists: a project names two connections, and a
 * connection reports which projects use it. Archived projects are included
 * because archiving is how a project is retired, so this is the only place one
 * can be found again.
 */
async function loadSettingsData() {
  const [, connections] = await Promise.all([
    loadProjects({ keepSelection: true }),
    api(`${API}/connections`),
  ]);
  state.connections = connections.items;
}

async function loadViewHistory(view: HistoryView) {
  if (view === 'workflows') {
    const [releaseBody, deploymentBody, scheduleBody] = await Promise.all([
      api(`${API}/releases?limit=100`),
      api(`${API}/deployments?limit=100`),
      api(`${API}/schedules?limit=100`),
    ]);
    state.bundles = releaseBody.items.filter((item: any) => item.mode === 'BUNDLE');
    state.legacy = releaseBody.items.filter((item: any) => item.repository);
    state.deployments = deploymentBody.items;
    state.schedules = scheduleBody.items;
    return;
  }
  if (view === 'recipients') {
    state.recipients = (await api(`${API}/notifications/recipients`)).items;
    return;
  }
  if (SETTINGS_VIEWS.has(view)) {
    await loadSettingsData();
    return;
  }
  const releaseBody = await api(`${API}/releases?limit=100`);
  state.bundles = releaseBody.items.filter((item: any) => item.mode === 'BUNDLE');
  state.legacy = releaseBody.items.filter((item: any) => item.repository);
}

async function finishHistoryLoad() {
  if (!currentHistory().some((item) => item.id === state.selectedId)) state.selectedId = null;
  renderHistory();
  if (state.selectedId) await selectHistory(state.selectedId);
  else if (currentHistory()[0]) await selectHistory(currentHistory()[0].id);
}

function renderHistoryFailure(error: Error) {
  if (patchRows(elements.history, `<div class="history-error"><strong>Unable to load this view</strong><span>${escapeHtml(error.message)}</span><button class="button secondary" id="retry-history">Retry</button></div>`)) {
    document.querySelector('#retry-history').addEventListener('click', () => switchView(state.view, { updateLocation: false }));
  }
  patchDetail('<div class="empty-state"><span class="empty-icon">!</span><strong>History unavailable</strong><p>Check the API response and database migration, then retry.</p></div>');
}

function multipartRequest(payload: Record<string, unknown>, form: HTMLFormElement) {
  const body = new FormData();
  body.set('payload', JSON.stringify(payload));
  const input = form.elements.namedItem('attachment') as HTMLInputElement | null;
  const file = input?.files?.[0];
  if (file) body.set('attachment', file, file.name);
  return { method: 'POST', body };
}

function openStandalonePromotion(component: ComponentName) {
  const build = selectedBuild(component);
  if (!build) return;
  elements.promoteDialog.dataset.component = component;
  elements.promoteSummary.innerHTML = `<span>${escapeHtml(componentLabel(component))} #${build.number}</span><strong>${escapeHtml(elements.target.value)}</strong>`;
  (document.querySelector('#promote-form') as HTMLFormElement).reset();
  elements.promoteDialog.showModal();
}

async function promoteStandalone(form: HTMLFormElement) {
  const component = elements.promoteDialog.dataset.component as ComponentName;
  const build = selectedBuild(component);
  if (!build) return;
  try {
    const deployment = await api(
      `${API}/projects/${encodeURIComponent(state.project!.key)}/components/${encodeURIComponent(component)}/builds/${build.number}/promote`,
      { ...multipartRequest({ target: elements.target.value }, form) },
    );
    elements.promoteDialog.close();
    state.view = 'workflows';
    state.workflowKindFilter = 'deployment';
    state.workflowStatusFilter = 'all';
    state.selectedId = `deployment:${deployment.id}`;
    updateViewLocation(state.view, false, true);
    updateViewTabs();
    await loadHistory();
    await selectHistory(`deployment:${deployment.id}`);
    showToast(`${componentLabel(component)} deployment started`);
  } catch (error) { showToast(error.message, true); }
}

function deploymentNotes(record: ReleaseBundle | Deployment): string {
  const deployments = 'deployments' in record ? record.deployments : [record];
  return [
    '## Deployment',
    '',
    `Target: ${record.target}`,
    '',
    ...deployments.flatMap((deployment) => [
      `### ${componentLabel(deployment.component)}`,
      `- Build: #${deployment.source_build_number}`,
      `- Commit: ${deployment.commit_sha || 'unknown'}`,
      '',
    ]),
  ].join('\n').trim();
}

function openPublishDialog(
  record: ReleaseBundle | Deployment,
  kind: 'bundle' | 'deployment',
) {
  const deployments = 'deployments' in record ? record.deployments : [record];
  elements.publishDialog.dataset.publishKind = kind;
  elements.publishDialog.dataset.publishId = record.id;
  const title = kind === 'bundle'
    ? 'Publish Bundle'
    : `Publish ${componentLabel(deployments[0].component)}`;
  elements.publishDialog.querySelector('.dialog-heading h2').textContent = title;
  elements.publishSummary.innerHTML = deploymentOrder(deployments)
    .map((deployment) => `<article><div><span>${escapeHtml(componentLabel(deployment.component))}</span><strong>Build #${deployment.source_build_number}</strong></div><code>${escapeHtml(deployment.commit_sha || 'unknown')}</code><small>Repo: ${escapeHtml(state.giteaRepositories[deployment.component] || 'Not configured')}</small></article>`)
    .join('') + (kind === 'bundle' ? '<p>The same version is created in every repository this release covers.</p>' : '');
  const form = elements.publishDialog.querySelector('form');
  form.reset();
  delete form.elements.name.dataset.edited;
  form.elements.version.value = record.version || '';
  form.elements.name.value = ('release_name' in record ? record.release_name : null) || record.version || '';
  form.elements.release_notes.value = ('release_notes' in record ? record.release_notes : null) || deploymentNotes(record);
  elements.publishDialog.showModal();
}

async function refreshPublishedDetail(kind: 'bundle' | 'deployment', id: string) {
  if (kind === 'bundle') {
    const [bundle, eventBody] = await Promise.all([
      api(`${API}/releases/${id}`),
      api(`${API}/releases/${id}/events`),
    ]);
    state.bundles = state.bundles.map((item) => item.id === id ? bundle : item);
    state.events = eventBody.items;
    renderHistory();
    renderBundleDetail(bundle);
  } else {
    const [deployment, eventBody] = await Promise.all([
      api(`${API}/deployments/${id}`),
      api(`${API}/deployments/${id}/events`),
    ]);
    state.deployments = state.deployments.map((item) => item.id === id ? deployment : item);
    state.events = eventBody.items;
    renderHistory();
    renderStandaloneDetail(deployment);
  }
}

async function publishVersion(form: HTMLFormElement) {
  const kind = elements.publishDialog.dataset.publishKind as 'bundle' | 'deployment';
  const id = elements.publishDialog.dataset.publishId;
  const values: any = Object.fromEntries(new FormData(form));
  const payload = {
    version: values.version,
    name: values.name,
    release_notes: values.release_notes || '',
    draft: values.draft === 'on',
    prerelease: values.prerelease === 'on',
  };
  const endpoint = kind === 'bundle'
    ? `${API}/releases/${id}/publish`
    : `${API}/deployments/${id}/publish`;
  const submit = form.querySelector('button[type="submit"]') as HTMLButtonElement;
  submit.disabled = true;
  submit.textContent = 'Publishing…';
  try {
    await api(endpoint, { method: 'POST', body: JSON.stringify(payload) });
    elements.publishDialog.close();
    await refreshPublishedDetail(kind, id);
    showToast(`Version ${payload.version} published`);
  } catch (error) {
    await refreshPublishedDetail(kind, id).catch(() => undefined);
    showToast(error.message, true);
  } finally {
    submit.disabled = false;
    submit.textContent = 'Publish';
  }
}

function openConfirmation() {
  const components = includedComponents();
  if (components.length < 2) return;
  (document.querySelector('#confirmation-form') as HTMLFormElement).reset();
  const rows = components.map((component, index) => {
    const build = selectedBuild(component.key)!;
    const when = index === 0
      ? 'deploys first'
      : `waits for ${components[index - 1].display_name}`;
    return `<div><span>${escapeHtml(component.display_name)} — ${when}</span><strong>Build #${build.number}</strong><code>${shortSha(build.commit_sha)}</code></div>`;
  });
  elements.confirmationSummary.innerHTML = `<div><span>Target</span><strong>${escapeHtml(elements.target.value)}</strong></div>${rows.join('')}<div class="release-order"><span>Order</span><strong>${escapeHtml(components.map((component) => component.display_name).join(' → '))}</strong></div>`;
  elements.confirmation.showModal();
}

async function createBundle(form: HTMLFormElement) {
  const components = includedComponents();
  if (components.length < 2) return;
  try {
    const bundle = await api(`${API}/releases/promote`, {
      ...multipartRequest({
        project_id: state.project!.key,
        components: components.map((component) => ({
          key: component.key,
          build_number: selectedBuild(component.key)!.number,
        })),
        target: elements.target.value,
      }, form),
    });
    elements.confirmation.close();
    state.view = 'releases';
    state.selectedId = bundle.id;
    updateViewLocation(state.view);
    updateViewTabs();
    await loadHistory();
    await selectHistory(bundle.id);
    showToast(`Combined release started: ${includedComponents()[0]?.display_name || 'first component'} first`);
  } catch (error) { showToast(error.message, true); }
}

function localDateTimeValue(date: Date) {
  const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return shifted.toISOString().slice(0, 16);
}

function savedScheduleRecipients(): Set<string> | null {
  try {
    const saved = window.localStorage.getItem(SCHEDULE_RECIPIENTS_STORAGE_KEY);
    if (saved === null) return null;
    const values = JSON.parse(saved);
    return Array.isArray(values) ? new Set(values.map((value) => String(value).toLowerCase())) : null;
  } catch {
    return null;
  }
}

function persistScheduleRecipients() {
  const selected = (Array.from(
    elements.scheduleRecipientOptions.querySelectorAll('input[name="notification_recipients"]:checked'),
  ) as HTMLInputElement[]).map((input) => input.value);
  try {
    window.localStorage.setItem(SCHEDULE_RECIPIENTS_STORAGE_KEY, JSON.stringify(selected));
  } catch {
    // Scheduling remains usable if browser storage is unavailable.
  }
}

function renderScheduleRecipientOptions() {
  const saved = savedScheduleRecipients();
  const selected = saved ?? new Set(state.recipients.map((recipient) => recipient.email.toLowerCase()));
  if (!state.recipients.length) {
    elements.scheduleRecipientOptions.innerHTML = '<span class="recipient-options-empty">No saved addresses. Add one from Email recipients first.</span>';
    return;
  }
  elements.scheduleRecipientOptions.innerHTML = state.recipients.map((recipient) => `
    <label class="recipient-option">
      <input type="checkbox" name="notification_recipients" value="${escapeHtml(recipient.email)}" ${selected.has(recipient.email.toLowerCase()) ? 'checked' : ''}>
      <span>${escapeHtml(recipient.email)}</span>
    </label>
  `).join('');
}

/** `keys` is what to schedule: one component, or the ticked ones for a
 * combined release. */
function openSchedule(keys: ComponentName[]) {
  const components = activeComponents().filter(
    (component) => keys.includes(component.key) && selectedBuild(component.key),
  );
  if (!components.length) return;
  elements.scheduleDialog.dataset.components = components.map((component) => component.key).join(',');
  const summary = components
    .map((component) => `${component.display_name} #${selectedBuild(component.key)!.number}`)
    .join(' → ');
  elements.scheduleSummary.innerHTML = `<span>${escapeHtml(summary)}</span><strong>${escapeHtml(elements.target.value)}</strong>`;
  const form = document.querySelector('#schedule-form');
  form.reset();
  renderScheduleRecipientOptions();
  form.elements.scheduled_for.value = localDateTimeValue(new Date(Date.now() + 15 * 60000));
  form.elements.timezone.value = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  elements.scheduleDialog.showModal();
}

async function createSchedule(form: HTMLFormElement) {
  const keys = String(elements.scheduleDialog.dataset.components || '').split(',').filter(Boolean);
  const formData = new FormData(form);
  const values = Object.fromEntries(formData);
  const payload: Record<string, any> = {
    project_id: state.project!.key,
    components: keys.map((key) => ({ key, build_number: selectedBuild(key)!.number })),
    target: elements.target.value,
    scheduled_for: values.scheduled_for,
    timezone: values.timezone,
    notification_recipients: formData.getAll('notification_recipients').map((item) => String(item)),
    release_version: values.release_version,
    release_notes: values.release_notes,
  };
  try {
    const schedule = await api(`${API}/schedules`, multipartRequest(payload, form));
    elements.scheduleDialog.close();
    state.view = 'workflows';
    state.workflowKindFilter = 'schedule';
    state.workflowStatusFilter = 'all';
    state.selectedId = `schedule:${schedule.id}`;
    updateViewLocation(state.view, false, true);
    updateViewTabs();
    await loadHistory();
    await selectHistory(`schedule:${schedule.id}`);
    showToast('Deployment scheduled');
  } catch (error) { showToast(error.message, true); }
}

// --------------------------------------------------------------------------- //
// Projects, components and connections
// --------------------------------------------------------------------------- //

/** Empty string means "not chosen"; the API wants null for that. */
function optional(value: FormDataEntryValue | null): string | null {
  const text = String(value ?? '').trim();
  return text ? text : null;
}

function fillConnectionOptions(selector: string, kind: string, chosen: string | null, inherit: string) {
  const field = document.querySelector(selector);
  if (!field) return;
  field.replaceChildren(
    new Option(inherit, ''),
    ...state.connections
      .filter((connection) => connection.kind === kind)
      .map((connection) => new Option(
        connection.is_default ? `${connection.name} (default)` : connection.name,
        connection.id,
      )),
  );
  field.value = chosen || '';
}

function openProjectDialog(project: Project | null) {
  const form = document.querySelector('#project-form') as HTMLFormElement;
  form.reset();
  elements.projectDialog.dataset.projectKey = project?.key || '';
  document.querySelector('#project-dialog-title').textContent = project ? 'Edit project' : 'New project';
  document.querySelector('#project-submit').textContent = project ? 'Save project' : 'Create project';
  // The key is part of every stored deployment's identity, so it is set once.
  document.querySelector('#project-key-field').hidden = Boolean(project);
  (form.elements.namedItem('key') as HTMLInputElement).required = !project;
  if (project) {
    (form.elements.namedItem('name') as HTMLInputElement).value = project.name;
    (form.elements.namedItem('default_target') as HTMLInputElement).value = project.default_target;
    (form.elements.namedItem('description') as HTMLTextAreaElement).value = project.description || '';
  }
  fillConnectionOptions('#project-drone-connection', 'drone', project?.drone_connection_id || null, 'Use the default connection');
  fillConnectionOptions('#project-gitea-connection', 'gitea', project?.gitea_connection_id || null, 'Use the default connection');
  elements.projectDialog.showModal();
}

async function saveProject(form: HTMLFormElement) {
  const values = Object.fromEntries(new FormData(form));
  const existingKey = elements.projectDialog.dataset.projectKey;
  const payload: Record<string, unknown> = {
    name: String(values.name).trim(),
    default_target: String(values.default_target).trim(),
    description: optional(values.description),
    drone_connection_id: optional(values.drone_connection_id),
    gitea_connection_id: optional(values.gitea_connection_id),
  };
  if (!existingKey) payload.key = String(values.key).trim();
  try {
    const project = await api(
      existingKey ? `${API}/projects/${encodeURIComponent(existingKey)}` : `${API}/projects`,
      { method: existingKey ? 'PATCH' : 'POST', body: JSON.stringify(payload) },
    );
    elements.projectDialog.close();
    await refreshProjectsView(project.id);
    showToast(existingKey ? 'Project saved' : 'Project created');
  } catch (error) { showToast(error.message, true); }
}

async function setProjectArchived(project: Project) {
  const action = project.is_archived ? 'unarchive' : 'archive';
  try {
    await api(`${API}/projects/${encodeURIComponent(project.key)}/${action}`, { method: 'POST' });
    await refreshProjectsView(project.id);
    showToast(project.is_archived ? 'Project unarchived' : 'Project archived');
  } catch (error) { showToast(error.message, true); }
}

async function validateProject(project: Project) {
  const button = document.querySelector('#validate-project');
  if (button) { button.disabled = true; button.textContent = 'Checking…'; }
  try {
    state.validations[project.id] = await api(`${API}/projects/${encodeURIComponent(project.key)}/validate`, { method: 'POST' });
    if (state.selectedId === project.id) renderProjectDetail(project);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    if (button && button.isConnected) { button.disabled = false; button.textContent = 'Check upstreams'; }
  }
}

function openComponentDialog(project: Project, component: ProjectComponent | null) {
  const form = document.querySelector('#component-form') as HTMLFormElement;
  form.reset();
  elements.componentDialog.dataset.projectKey = project.key;
  elements.componentDialog.dataset.componentKey = component?.key || '';
  document.querySelector('#component-dialog-title').textContent = component ? 'Edit component' : 'Add component';
  document.querySelector('#component-submit').textContent = component ? 'Save component' : 'Add component';
  document.querySelector('#component-key-field').hidden = Boolean(component);
  (form.elements.namedItem('key') as HTMLInputElement).required = !component;
  const set = (name: string, value: string) => {
    (form.elements.namedItem(name) as HTMLInputElement).value = value;
  };
  const check = (name: string, value: boolean) => {
    (form.elements.namedItem(name) as HTMLInputElement).checked = value;
  };
  if (component) {
    set('display_name', component.display_name);
    set('drone_owner', component.drone_owner);
    set('drone_repo', component.drone_repo);
    set('promote_target_override', component.promote_target_override || '');
    set('gitea_owner', component.gitea_owner || '');
    set('gitea_repo', component.gitea_repo || '');
    set('tag_prefix', component.tag_prefix);
    check('publish_enabled', component.publish_enabled);
    check('is_active', component.is_active);
  }
  fillConnectionOptions('#component-drone-connection', 'drone', component?.drone_connection_id || null, 'Inherit from the project');
  fillConnectionOptions('#component-gitea-connection', 'gitea', component?.gitea_connection_id || null, 'Inherit from the project');
  elements.componentDialog.showModal();
}

async function saveComponent(form: HTMLFormElement) {
  const values = Object.fromEntries(new FormData(form));
  const projectKey = elements.componentDialog.dataset.projectKey;
  const componentKey = elements.componentDialog.dataset.componentKey;
  const payload: Record<string, unknown> = {
    display_name: optional(values.display_name),
    drone_owner: String(values.drone_owner).trim(),
    drone_repo: String(values.drone_repo).trim(),
    promote_target_override: optional(values.promote_target_override),
    drone_connection_id: optional(values.drone_connection_id),
    gitea_owner: optional(values.gitea_owner),
    gitea_repo: optional(values.gitea_repo),
    gitea_connection_id: optional(values.gitea_connection_id),
    tag_prefix: String(values.tag_prefix ?? '').trim(),
    publish_enabled: values.publish_enabled === 'on',
    is_active: values.is_active === 'on',
  };
  // PATCH treats null as "no change", so clearing an override is its own flag.
  if (componentKey) payload.clear_promote_target_override = payload.promote_target_override === null;
  else payload.key = String(values.key).trim();
  const base = `${API}/projects/${encodeURIComponent(projectKey)}/components`;
  try {
    await api(
      componentKey ? `${base}/${encodeURIComponent(componentKey)}` : base,
      { method: componentKey ? 'PATCH' : 'POST', body: JSON.stringify(payload) },
    );
    elements.componentDialog.close();
    await refreshProjectsView(state.selectedId);
    showToast(componentKey ? 'Component saved' : 'Component added');
  } catch (error) { showToast(error.message, true); }
}

async function deleteComponent(project: Project, componentKey: string) {
  try {
    await api(`${API}/projects/${encodeURIComponent(project.key)}/components/${encodeURIComponent(componentKey)}`, { method: 'DELETE' });
    await refreshProjectsView(project.id);
    showToast('Component removed');
  } catch (error) {
    // A component with deployment history cannot be removed; deactivating it is
    // the honest alternative and the message says so.
    showToast(error.message, true);
  }
}

async function moveComponent(project: Project, componentId: string, direction: number) {
  const ordered = [...project.components].sort((left, right) => left.position - right.position);
  const index = ordered.findIndex((component) => component.id === componentId);
  const target = index + direction;
  if (index < 0 || target < 0 || target >= ordered.length) return;
  const reordered = [...ordered];
  [reordered[index], reordered[target]] = [reordered[target], reordered[index]];
  try {
    await api(`${API}/projects/${encodeURIComponent(project.key)}/components/reorder`, {
      method: 'POST',
      body: JSON.stringify({ component_ids: reordered.map((component) => component.id) }),
    });
    await refreshProjectsView(project.id);
    showToast('Deployment order updated');
  } catch (error) { showToast(error.message, true); }
}

/** Re-reads the project list after any edit and redraws both the settings view
 * and the launcher, which shares the same list. */
async function refreshProjectsView(selectId: string | null) {
  await loadProjects({ keepSelection: true });
  if (state.view !== 'projects') return;
  state.selectedId = state.projects.some((project) => project.id === selectId)
    ? selectId
    : state.projects[0]?.id ?? null;
  renderHistory();
  if (state.selectedId) await selectHistory(state.selectedId);
  else patchDetail('<div class="empty-state"><span class="empty-icon">▤</span><strong>No projects yet</strong><p>Create one to describe which repositories a release promotes, and in what order.</p></div>');
}

function openConnectionDialog(connection: UpstreamConnection | null) {
  const form = document.querySelector('#connection-form') as HTMLFormElement;
  form.reset();
  elements.connectionDialog.dataset.connectionId = connection?.id || '';
  document.querySelector('#connection-dialog-title').textContent = connection ? 'Edit connection' : 'New connection';
  document.querySelector('#connection-submit').textContent = connection ? 'Save connection' : 'Create connection';
  // The kind decides which API the token talks to; changing it would silently
  // repoint every project that uses this connection.
  document.querySelector('#connection-kind-field').hidden = Boolean(connection);
  const token = form.elements.namedItem('token') as HTMLInputElement;
  token.required = !connection;
  document.querySelector('#connection-token-hint').textContent = connection
    ? `Stored token ${connection.token_hint}. Leave empty to keep it.`
    : 'Stored encrypted. It is never sent back to this page.';
  if (connection) {
    (form.elements.namedItem('name') as HTMLInputElement).value = connection.name;
    (form.elements.namedItem('base_url') as HTMLInputElement).value = connection.base_url;
    (form.elements.namedItem('is_default') as HTMLInputElement).checked = connection.is_default;
    (form.elements.namedItem('kind') as HTMLSelectElement).value = connection.kind;
  }
  elements.connectionDialog.showModal();
}

async function saveConnection(form: HTMLFormElement) {
  const values = Object.fromEntries(new FormData(form));
  const connectionId = elements.connectionDialog.dataset.connectionId;
  const payload: Record<string, unknown> = {
    name: String(values.name).trim(),
    base_url: String(values.base_url).trim(),
    is_default: values.is_default === 'on',
  };
  // An omitted token keeps the stored one; there is no way to send an empty one.
  const token = optional(values.token);
  if (token) payload.token = token;
  if (!connectionId) payload.kind = String(values.kind);
  try {
    const connection = await api(
      connectionId ? `${API}/connections/${connectionId}` : `${API}/connections`,
      { method: connectionId ? 'PATCH' : 'POST', body: JSON.stringify(payload) },
    );
    elements.connectionDialog.close();
    await refreshConnectionsView(connection.id);
    showToast(connectionId ? 'Connection saved' : 'Connection created');
  } catch (error) { showToast(error.message, true); }
}

async function testConnection(connection: UpstreamConnection) {
  const button = document.querySelector('#test-connection');
  if (button) { button.disabled = true; button.textContent = 'Testing…'; }
  try {
    const result = await api(`${API}/connections/${connection.id}/test`, { method: 'POST' });
    state.connections = state.connections.map((item) => (item.id === connection.id
      ? { ...item, verify_status: result.status, verify_detail: result.detail, verified_at: result.checked_at }
      : item));
    renderHistory();
    const updated = state.connections.find((item) => item.id === connection.id);
    if (updated && state.selectedId === connection.id) renderConnectionDetail(updated);
    showToast(result.status === 'ok' ? 'Connection reachable' : `Connection ${result.status}`, result.status !== 'ok');
  } catch (error) {
    showToast(error.message, true);
  } finally {
    if (button && button.isConnected) { button.disabled = false; button.textContent = 'Test'; }
  }
}

async function deleteConnection(connection: UpstreamConnection) {
  try {
    await api(`${API}/connections/${connection.id}`, { method: 'DELETE' });
    await refreshConnectionsView(null);
    showToast('Connection deleted');
  } catch (error) { showToast(error.message, true); }
}

async function refreshConnectionsView(selectId: string | null) {
  state.connections = (await api(`${API}/connections`)).items;
  if (state.view !== 'connections') return;
  state.selectedId = state.connections.some((item) => item.id === selectId)
    ? selectId
    : state.connections[0]?.id ?? null;
  renderHistory();
  if (state.selectedId) await selectHistory(state.selectedId);
  else patchDetail('<div class="empty-state"><span class="empty-icon">⚿</span><strong>No connections yet</strong><p>Add the Drone and Gitea servers this controller talks to. Tokens are stored encrypted.</p></div>');
}

async function createRecipient(form: HTMLFormElement) {
  const values = Object.fromEntries(new FormData(form));
  try {
    const recipient = await api(`${API}/notifications/recipients`, {
      method: 'POST',
      body: JSON.stringify({ email: values.email }),
    });
    elements.recipientDialog.close();
    form.reset();
    await switchView('recipients');
    await selectHistory(recipient.id);
    showToast('Email recipient added');
  } catch (error) { showToast(error.message, true); }
}

async function refreshSelectedBundle(showMessage = false) {
  const bundle = state.bundles.find((item) => item.id === state.selectedId || `bundle:${item.id}` === state.selectedId);
  if (!bundle || TERMINAL.has(bundle.status)) return;
  try {
    const updated = await api(`${API}/releases/${bundle.id}/refresh`, { method: 'POST' });
    state.bundles = state.bundles.map((item) => item.id === updated.id ? updated : item);
    state.events = (await api(`${API}/releases/${bundle.id}/events`)).items;
    renderHistory();
    renderBundleDetail(updated);
    if (showMessage) showToast('Release state refreshed');
  } catch (error) { if (showMessage) showToast(error.message, true); }
}

async function refreshSelectedDeployment(showMessage = false) {
  const deployment = state.deployments.find((item) => item.id === state.selectedId || `deployment:${item.id}` === state.selectedId);
  if (!deployment || TERMINAL.has(deployment.status)) return;
  try {
    const updated = await api(`${API}/deployments/${deployment.id}/refresh`, { method: 'POST' });
    state.events = (await api(`${API}/deployments/${deployment.id}/events`)).items;
    state.deployments = state.deployments.map((item) => item.id === updated.id ? updated : item);
    renderHistory();
    renderStandaloneDetail(updated);
    if (showMessage) showToast('Deployment state refreshed');
  } catch (error) { if (showMessage) showToast(error.message, true); }
}

function updateViewTabs() {
  elements.pageShell.dataset.view = state.view;
  elements.launcher.hidden = state.view !== 'releases';
  elements.workflowFilters.hidden = state.view !== 'workflows';
  elements.workflowKindFilter.value = state.workflowKindFilter;
  elements.workflowStatusFilter.value = state.workflowStatusFilter;
  const pendingSchedulesActive = state.workflowKindFilter === 'schedule'
    && state.workflowStatusFilter === 'pending';
  document.querySelector('#pending-schedules-filter').classList.toggle('active', pendingSchedulesActive);
  document.querySelector('#pending-schedules-filter').setAttribute('aria-pressed', String(pendingSchedulesActive));
  elements.settingsSwitcher.hidden = !SETTINGS_VIEWS.has(state.view);
  document.querySelectorAll('.topnav [data-view]').forEach((button: any) => {
    const active = button.dataset.view === state.view
      || (button.dataset.view === 'projects' && state.view === 'connections');
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  document.querySelectorAll('[data-settings-view]').forEach((button: any) => {
    const active = button.dataset.settingsView === state.view;
    button.classList.toggle('active', active);
    button.setAttribute('aria-pressed', String(active));
  });
}

function viewFromLocation(): HistoryView {
  const requestedView = new URLSearchParams(window.location.search).get('view');
  if (requestedView === 'schedules' || requestedView === 'legacy' || requestedView === 'deployments') return 'workflows';
  return requestedView && HISTORY_VIEWS.has(requestedView as HistoryView)
    ? requestedView as HistoryView
    : 'releases';
}

function syncWorkflowFiltersFromLocation() {
  const params = new URLSearchParams(window.location.search);
  const requestedKind = params.get('workflow_kind') as WorkflowKindFilter | null;
  const requestedStatus = params.get('workflow_status') as WorkflowStatusFilter | null;
  const legacyViewKind = params.get('view') === 'schedules'
    ? 'schedule'
    : params.get('view') === 'legacy'
      ? 'legacy'
      : params.get('view') === 'deployments' ? 'deployment' : null;
  if (legacyViewKind) state.workflowKindFilter = legacyViewKind;
  else {
    state.workflowKindFilter = requestedKind && WORKFLOW_KIND_FILTERS.has(requestedKind)
      ? requestedKind
      : 'all';
  }
  state.workflowStatusFilter = requestedStatus && WORKFLOW_STATUS_FILTERS.has(requestedStatus)
    ? requestedStatus
    : 'all';
}

function selectedIdFromLocation(): string | null {
  const params = new URLSearchParams(window.location.search);
  const selected = params.get('selected');
  const requestedView = params.get('view');
  if (selected && !selected.includes(':')) {
    if (requestedView === 'schedules') return `schedule:${selected}`;
    if (requestedView === 'legacy') return `legacy:${selected}`;
    if (requestedView === 'deployments') return `deployment:${selected}`;
  }
  return selected;
}

function updateViewLocation(view: HistoryView, replace = false, preserveSelected = false) {
  const url = new URL(window.location.href);
  if (view === 'releases') url.searchParams.delete('view');
  else url.searchParams.set('view', view);
  if (view === 'workflows') {
    if (state.workflowKindFilter === 'all') url.searchParams.delete('workflow_kind');
    else url.searchParams.set('workflow_kind', state.workflowKindFilter);
    if (state.workflowStatusFilter === 'all') url.searchParams.delete('workflow_status');
    else url.searchParams.set('workflow_status', state.workflowStatusFilter);
  } else {
    url.searchParams.delete('workflow_kind');
    url.searchParams.delete('workflow_status');
  }
  if (preserveSelected && state.selectedId) url.searchParams.set('selected', state.selectedId);
  else if (!preserveSelected) url.searchParams.delete('selected');
  window.history[replace ? 'replaceState' : 'pushState']({ view }, '', `${url.pathname}${url.search}${url.hash}`);
}

async function applyWorkflowFilters(kind: WorkflowKindFilter, status: WorkflowStatusFilter) {
  state.workflowKindFilter = kind;
  state.workflowStatusFilter = status;
  const selectedStillVisible = currentHistory().some((item) => item.id === state.selectedId);
  if (!selectedStillVisible) state.selectedId = null;
  updateViewLocation('workflows', false, selectedStillVisible);
  updateViewTabs();
  renderHistory();
  if (state.selectedId) return;
  patchDetail('<div class="empty-state"><span class="empty-icon">⌁</span><strong>Select a workflow</strong><p>Choose a workflow to inspect its persisted execution state.</p></div>');
  const first = currentHistory()[0];
  if (first) await selectHistory(first.id);
}

async function switchView(view: HistoryView, { updateLocation = true } = {}) {
  if (!HISTORY_VIEWS.has(view)) view = 'releases';
  destroyBpmnViewer();
  state.view = view;
  state.selectedId = updateLocation ? null : selectedIdFromLocation();
  if (updateLocation) updateViewLocation(view);
  updateViewTabs();
  patchRows(elements.history, '<div class="loading-row">Loading history…</div>');
  patchDetail('<div class="empty-state"><span class="empty-icon">⌁</span><strong>Select a record</strong><p>Its persisted execution state will appear here.</p></div>');
  try {
    await loadViewHistory(view);
    if (state.view !== view) return;
    await finishHistoryLoad();
    if (!currentHistory()[0] && view === 'releases') renderWorkflowPreview();
    if (!currentHistory()[0] && view === 'recipients') {
      patchDetail('<div class="empty-state"><span class="empty-icon">@</span><strong>No recipients yet</strong><p>Add an email address to make it selectable in Schedule release.</p></div>');
    }
  } catch (error) {
    if (state.view !== view) return;
    renderHistoryFailure(error);
    showToast(error.message, true);
  }
}

async function legacyAction(release: any, action: string) {
  if (action === 'approve' || action === 'reject') {
    const form = document.querySelector('#decision-form');
    form.reset();
    form.elements.decision.value = action;
    document.querySelector('#decision-title').textContent = action === 'approve' ? 'Approve release' : 'Reject release';
    document.querySelector('#decision-submit').textContent = action === 'approve' ? 'Approve' : 'Reject';
    document.querySelector('#decision-message-field').hidden = action === 'approve';
    elements.decisionDialog.dataset.releaseId = release.id;
    elements.decisionDialog.showModal();
    return;
  }
  const path = action === 'start' ? 'deployment/start' : 'deployment/finish';
  const options = action === 'start' ? { method: 'POST' } : { method: 'POST', body: JSON.stringify({ status: action.toUpperCase() }) };
  try { await api(`${API}/releases/${release.id}/${path}`, options); await loadHistory(); await selectHistory(`legacy:${release.id}`); }
  catch (error) { showToast(error.message, true); }
}

function bindEvents() {
  elements.target.addEventListener('input', renderSelections);
  elements.history.addEventListener('click', (event: Event) => {
    if (!(event.target instanceof Element)) return;
    const link = event.target.closest<HTMLAnchorElement>('a[data-history-id]');
    if (!link || !elements.history.contains(link)) return;
    if (event instanceof MouseEvent && (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)) return;
    event.preventDefault();
    const id = link.dataset.historyId;
    if (!id) return;
    // Re-clicking the selected row must not stack duplicate history entries.
    if (link.href !== window.location.href) {
      window.history.pushState({ view: state.view, selected: id }, '', link.href);
    }
    void selectHistory(id);
  });
  // Delegated on the grid rather than on each card: the cards are rebuilt when
  // the project changes, and per-card listeners would be lost with them. It also
  // keeps renderBuilds free to reuse the existing row nodes instead of rebuilding
  // the whole list on every selection change.
  document.querySelector('#component-grid').addEventListener('click', (event: any) => {
    if (!(event.target instanceof Element)) return;
    const retry = event.target.closest('[data-retry-builds]');
    if (retry) {
      void loadBuilds(retry.dataset.retryBuilds);
      return;
    }
    const deploy = event.target.closest('[data-deploy-component]');
    if (deploy) {
      if (!deploy.disabled) openStandalonePromotion(deploy.dataset.deployComponent);
      return;
    }
    const schedule = event.target.closest('[data-schedule-component]');
    if (schedule) {
      if (!schedule.disabled) openSchedule([schedule.dataset.scheduleComponent]);
      return;
    }
    const button = event.target.closest('[data-build-number]');
    if (!button || button.disabled) return;
    const card = button.closest('[data-component]');
    if (!card) return;
    const component = card.dataset.component as ComponentName;
    const number = Number(button.dataset.buildNumber);
    if (state.selected[component] === number) return;
    state.selected[component] = number;
    renderBuilds(component);
    renderSelections();
  });
  document.querySelector('#component-grid').addEventListener('change', (event: any) => {
    if (!(event.target instanceof Element)) return;
    const include = event.target.closest('[data-include]');
    if (include) {
      state.included[include.dataset.include] = include.checked;
      renderSelections();
      return;
    }
    const card = event.target.closest('[data-component]');
    if (!card || !event.target.matches('select')) return;
    const component = card.dataset.component as ComponentName;
    state.selectedBranch[component] = event.target.value;
    selectFirstPromotable(component);
    renderBuilds(component);
    renderSelections();
  });
  // The zoom controls live inside the detail markup, so bind them once here rather
  // than on every BPMN render.
  elements.detail.addEventListener('click', (event: any) => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest('[data-bpmn-zoom]');
    if (!button || !bpmnViewer) return;
    const canvas = bpmnViewer.get('canvas');
    const action = button.dataset.bpmnZoom;
    if (action === 'fit') canvas.zoom('fit-viewport');
    else {
      const nextZoom = canvas.zoom() + (action === 'in' ? 0.15 : -0.15);
      canvas.zoom(Math.min(4, Math.max(0.2, nextZoom)));
    }
  });
  // Component rows are rebuilt by every project-detail render, so they are
  // delegated from the surviving container -- bound once, like the zoom controls
  // above. currentDetailRecord is what keeps this pointing at the open project.
  elements.detail.addEventListener('click', (event: any) => {
    if (!(event.target instanceof Element)) return;
    if (state.view !== 'projects') return;
    const project = currentDetailRecord as Project | null;
    if (!project?.components) return;
    const move = event.target.closest('[data-move-component]');
    if (move) {
      void moveComponent(project, move.dataset.moveComponent, Number(move.dataset.direction));
      return;
    }
    const edit = event.target.closest('[data-edit-component]');
    if (edit) {
      const component = project.components.find((item) => item.key === edit.dataset.editComponent);
      if (component) openComponentDialog(project, component);
      return;
    }
    const remove = event.target.closest('[data-delete-component]');
    if (remove) void deleteComponent(project, remove.dataset.deleteComponent);
  });
  document.querySelector('#project-selector').addEventListener('change', (event: any) => {
    void selectProject(event.currentTarget.value);
  });
  document.querySelector('#schedule-both').addEventListener('click', () => {
    openSchedule(includedComponents().map((component) => component.key));
  });
  document.querySelector('#release-both').addEventListener('click', openConfirmation);
  document.querySelector('#confirmation-form').addEventListener('submit', (event: any) => { event.preventDefault(); createBundle(event.currentTarget); });
  document.querySelector('#promote-form').addEventListener('submit', (event: any) => { event.preventDefault(); promoteStandalone(event.currentTarget); });
  document.querySelector('#schedule-form').addEventListener('submit', (event: any) => { event.preventDefault(); createSchedule(event.currentTarget); });
  elements.workflowKindFilter.addEventListener('change', (event: any) => {
    void applyWorkflowFilters(event.currentTarget.value as WorkflowKindFilter, state.workflowStatusFilter);
  });
  elements.workflowStatusFilter.addEventListener('change', (event: any) => {
    void applyWorkflowFilters(state.workflowKindFilter, event.currentTarget.value as WorkflowStatusFilter);
  });
  document.querySelector('#pending-schedules-filter').addEventListener('click', () => {
    void applyWorkflowFilters('schedule', 'pending');
  });
  elements.scheduleRecipientOptions.addEventListener('change', persistScheduleRecipients);
  document.querySelector('#new-recipient').addEventListener('click', () => elements.recipientDialog.showModal());
  document.querySelector('#recipient-form').addEventListener('submit', (event: any) => { event.preventDefault(); createRecipient(event.currentTarget); });
  document.querySelector('#new-project').addEventListener('click', () => openProjectDialog(null));
  document.querySelector('#project-form').addEventListener('submit', (event: any) => { event.preventDefault(); void saveProject(event.currentTarget); });
  document.querySelector('#component-form').addEventListener('submit', (event: any) => { event.preventDefault(); void saveComponent(event.currentTarget); });
  document.querySelector('#new-connection').addEventListener('click', () => openConnectionDialog(null));
  document.querySelector('#connection-form').addEventListener('submit', (event: any) => { event.preventDefault(); void saveConnection(event.currentTarget); });
  document.querySelectorAll('[data-settings-view]').forEach((button: any) => {
    button.addEventListener('click', () => void switchView(button.dataset.settingsView as HistoryView));
  });
  document.querySelector('#publish-form').addEventListener('submit', (event: any) => { event.preventDefault(); publishVersion(event.currentTarget); });
  document.querySelector('#publish-form [name="version"]').addEventListener('input', (event: any) => {
    const name = document.querySelector('#publish-form [name="name"]');
    if (!name.dataset.edited) name.value = event.currentTarget.value;
  });
  document.querySelector('#publish-form [name="name"]').addEventListener('input', (event: any) => { event.currentTarget.dataset.edited = 'true'; });
  document.querySelector('#refresh-all').addEventListener('click', async () => { try { await loadHistory(); await Promise.all(activeComponents().map((component) => loadBuilds(component.key))); showToast('Data refreshed'); } catch (error) { showToast(error.message, true); } });
  document.querySelectorAll('.topnav [data-view]').forEach((link: any) => link.addEventListener('click', (event: Event) => {
    event.preventDefault();
    void switchView(link.dataset.view as HistoryView);
  }));
  // The only listener bindEvents() attaches outside the React shell, so the only
  // one teardown() has to remove: React discards the nodes carrying the rest.
  window.addEventListener('popstate', handlePopState);
  document.querySelectorAll('.dialog-close').forEach((button: any) => button.addEventListener('click', () => button.closest('dialog').close()));
  document.querySelector('#new-release').addEventListener('click', () => elements.createDialog.showModal());
  document.querySelector('#create-form').addEventListener('submit', async (event: any) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values: any = Object.fromEntries(new FormData(form));
    values.message ||= null;
    try {
      const release = await api(`${API}/releases`, { method: 'POST', body: JSON.stringify(values) });
      const workflowId = `legacy:${release.id}`;
      elements.createDialog.close();
      form.reset();
      state.view = 'workflows';
      state.workflowKindFilter = 'legacy';
      state.workflowStatusFilter = 'all';
      state.selectedId = workflowId;
      updateViewLocation(state.view, false, true);
      updateViewTabs();
      await loadHistory();
      await selectHistory(workflowId);
      showToast('Legacy release created');
    }
    catch (error) { showToast(error.message, true); }
  });
  document.querySelector('#decision-form').addEventListener('submit', async (event: any) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form));
    const payload = values.decision === 'approve' ? {} : { message: values.message || null };
    try { await api(`${API}/releases/${elements.decisionDialog.dataset.releaseId}/${values.decision}`, { method: 'POST', body: JSON.stringify(payload) }); elements.decisionDialog.close(); await loadHistory(); await selectHistory(`legacy:${elements.decisionDialog.dataset.releaseId}`); }
    catch (error) { showToast(error.message, true); }
  });
}

function handlePopState() {
  syncWorkflowFiltersFromLocation();
  void switchView(viewFromLocation(), { updateLocation: false });
}

/**
 * Back to a just-loaded module. Called by initialize() rather than teardown() on
 * purpose: a request still in flight from the previous session can resolve after
 * teardown, and resetting on the way in means whatever it scribbles cannot
 * survive into the next one.
 */
function resetState() {
  Object.assign(state, initialLegacyState());
  componentLabels = new Map();
  eventStageIndex = null;
  currentDetailRecord = null;
  projectsLoad = null;
  // bpmnXmlCache survives: a workflow definition is the same document for the
  // life of the deployment, whoever is signed in.
}

/**
 * Stops this module cleanly so a second sign-in gets a working interface.
 *
 * Signing out unmounts the React shell, which takes every element `elements`
 * points at with it. Without this, the 5s poll kept requesting releases with a
 * dead session and writing the answers into detached nodes, the popstate
 * listener stayed live, and `initialized` still being true meant the next
 * sign-in returned early from initialize(): no events bound, `elements` still
 * holding the unmounted nodes, an interface that did nothing until a reload.
 *
 * `elements` is deliberately left pointing at the old nodes rather than
 * emptied. In-flight renders then write into detached DOM, which is harmless,
 * instead of dereferencing undefined; queryElements() replaces them wholesale on
 * the way back in.
 */
function teardown() {
  generation += 1;
  clearInterval(pollTimer);
  pollTimer = undefined;
  clearTimeout(toastTimer);
  toastTimer = undefined;
  window.removeEventListener('popstate', handlePopState);
  destroyBpmnViewer();
  initialized = false;
}

/** A status endpoint that answers 200 with a non-ok body is still a failure. */
async function requireOk(path: string, message: string) {
  const body = await api(path);
  if (body.status !== 'ok') throw new Error(message);
}

async function probeHealth(
  indicator: any,
  probe: () => Promise<unknown>,
  healthyLabel: string,
  unhealthyLabel: string,
) {
  let healthy = true;
  try {
    await probe();
  } catch {
    healthy = false;
  }
  indicator.classList.add(healthy ? 'healthy' : 'unhealthy');
  indicator.querySelector('span:last-child').textContent = healthy ? healthyLabel : unhealthyLabel;
}

async function initialize() {
  if (initialized) return;
  initialized = true;
  const run = ++generation;
  resetState();
  // Before anything reads elements.* -- updateViewTabs() below is the first.
  queryElements();
  syncWorkflowFiltersFromLocation();
  state.view = viewFromLocation();
  state.selectedId = selectedIdFromLocation();
  updateViewLocation(state.view, true, true);
  updateViewTabs();
  bindEvents();
  renderWorkflowPreview();
  // Nothing below reads a health result, and the three probes are independent of
  // each other and of the data load -- two of them are round-trips the backend
  // makes to Drone and Gitea. Running them in sequence put three upstream
  // latencies in front of the first useful paint, so everything starts at once
  // and each indicator settles on its own.
  //
  // loadProjects and loadHistory stay concurrent too. History detail can
  // therefore render before the project list lands, in which case componentLabel
  // falls back to the humanised component key until the next render corrects it.
  await Promise.allSettled([
    probeHealth(elements.health, () => api('/health'), 'Service healthy', 'Service unavailable'),
    probeHealth(elements.droneHealth, () => requireOk(`${API}/drone/status`, 'Drone unavailable'), 'Drone available', 'Drone unavailable'),
    probeHealth(elements.giteaHealth, () => requireOk(`${API}/gitea/status`, 'Gitea unavailable'), 'Gitea healthy', 'Gitea unavailable'),
    loadProjects(),
    loadHistory(),
  ]);
  // Signed out while the first load was still running: leave the next session
  // alone rather than drawing into it and starting a poll it did not ask for.
  if (run !== generation) return;
  if (state.view === 'releases' && !state.selectedId) renderWorkflowPreview();
  pollTimer = setInterval(async () => {
    if (document.hidden) return;
    if (state.view === 'releases') await refreshSelectedBundle();
    else if (state.view === 'workflows' && state.selectedId?.startsWith('deployment:')) {
      await refreshSelectedDeployment();
    }
  }, 5000);
}

export { initialize, teardown };
