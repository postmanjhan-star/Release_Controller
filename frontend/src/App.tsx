import { useEffect } from "react";
import { loadSession, logout } from "./authSlice";
import { useAppDispatch, useAppSelector } from "./store";
import type { HistoryView } from "./domain";

const HISTORY_TABS: ReadonlyArray<{ view: HistoryView; label: string }> = [
  { view: "releases", label: "Releases" },
  { view: "workflows", label: "Workflows" },
  { view: "recipients", label: "Email recipients" },
  { view: "projects", label: "Projects" },
];

/**
 * The controller reaches into the DOM this shell renders, so it has to stop when
 * the shell goes away. Signing out unmounts everything under #app, and without
 * the teardown the module kept polling into detached nodes and refused to
 * initialise again on the next sign-in.
 */
function useReleaseController(enabled: boolean): void {
  useEffect(() => {
    if (!enabled) return;
    let active = true;
    let stop: (() => void) | undefined;
    void import("./controller").then((controller) => {
      if (!active) return;
      stop = controller.teardown;
      void controller.initialize();
    });
    return () => {
      active = false;
      stop?.();
    };
  }, [enabled]);
}

function TopBar({
  login,
  fullName,
  onLogout,
}: {
  login: string;
  fullName: string | null;
  onLogout: () => void;
}) {
  return (
    <header className="topbar">
      <div className="brand-mark">RC</div>
      <div className="brand-copy">
        <strong>Release Controller</strong>
        <span>Release Worker v{__APP_VERSION__}</span>
      </div>
      <nav className="topnav" aria-label="History views">
        {HISTORY_TABS.map(({ view, label }, index) => (
          <a
            key={view}
            className={`nav-tab${index === 0 ? " active" : ""}`}
            data-view={view}
            href={view === "releases" ? "/" : `/?view=${view}`}
          >
            {label}
          </a>
        ))}
      </nav>
      <div className="health-group">
        <div className="service-health" id="service-health">
          <span className="health-dot" />
          <span>Checking service</span>
        </div>
        <div className="service-health" id="drone-health">
          <span className="health-dot" />
          <span>Checking Drone</span>
        </div>
        <div className="service-health" id="gitea-health">
          <span className="health-dot" />
          <span>Checking Gitea</span>
        </div>
      </div>
      <div className="user-menu">
        <span>{fullName || login}</span>
        <small>@{login}</small>
        <button type="button" onClick={onLogout}>
          Sign out
        </button>
      </div>
    </header>
  );
}

function LoginScreen({
  configured,
  loading,
  error,
}: {
  configured: boolean;
  loading: boolean;
  error: string | null;
}) {
  const callbackError = new URLSearchParams(window.location.search).get(
    "auth_error",
  );
  return (
    <main className="login-shell">
      <section className="login-card" aria-labelledby="login-title">
        <div className="login-mark">RC</div>
        <span className="eyebrow">Release Controller</span>
        <h1 id="login-title">Sign in to continue</h1>
        <p>
          Use your Gitea account. Authentication and MFA remain managed by
          Gitea.
        </p>
        {(error || callbackError) && (
          <div className="login-error" role="alert">
            {error || callbackError}
          </div>
        )}
        {!configured ? (
          <div className="login-warning" role="status">
            Gitea OAuth is not configured. Add the OAuth client settings to the
            server environment.
          </div>
        ) : (
          <a
            className="button primary login-button"
            href="/api/v1/auth/login"
            aria-disabled={loading}
          >
            {loading ? "Checking session…" : "Sign in with Gitea"}
          </a>
        )}
      </section>
    </main>
  );
}

function LaunchPanel() {
  return (
    <section
      className="launch-panel"
      id="launch-panel"
      aria-labelledby="launch-title"
    >
      <div className="section-heading">
        <div>
          <span className="eyebrow">New deployment</span>
          <h1 id="launch-title">Choose independent source builds</h1>
        </div>
        <div className="launch-controls">
          <label className="project-field">
            Project{" "}
            <select id="project-selector" disabled>
              <option>Loading…</option>
            </select>
          </label>
          <label className="target-field">
            Target{" "}
            <input
              id="release-target"
              placeholder="Loading target…"
              maxLength={100}
            />
          </label>
        </div>
      </div>
      {/* Filled by the controller: how many components there are, and what
          each is called, comes from the selected project. */}
      <div className="component-grid" id="component-grid">
        <div className="loading-row">Loading components…</div>
      </div>
      <div className="bundle-action">
        <div>
          <strong>Combined release order</strong>
          <span id="release-order">Loading…</span>
        </div>
        <div className="action-pair">
          <button
            className="button secondary on-dark"
            id="schedule-both"
            disabled
          >
            Schedule
          </button>
          <button className="button primary" id="release-both" disabled>
            Release selected components
          </button>
        </div>
      </div>
    </section>
  );
}

function HistoryWorkspace() {
  return (
    <section className="history-layout">
      <aside className="history-panel">
        <div className="history-heading">
          <div>
            <span className="eyebrow" id="history-eyebrow">
              Audit history
            </span>
            <h2 id="history-title">Release bundles</h2>
          </div>
          <button
            className="icon-button"
            id="refresh-all"
            aria-label="Refresh history"
            title="Refresh history"
          >
            ↻
          </button>
        </div>
        <div
          className="settings-switcher"
          id="settings-switcher"
          aria-label="Project settings"
          hidden
        >
          <button type="button" data-settings-view="projects">
            Projects
          </button>
          <button type="button" data-settings-view="connections">
            Connections
          </button>
        </div>
        <div
          className="workflow-filters"
          id="workflow-filters"
          aria-label="Workflow filters"
          hidden
        >
          <label>
            Type
            <select id="workflow-kind-filter">
              <option value="all">All workflows</option>
              <option value="schedule">Scheduled releases</option>
              <option value="bundle">Release bundles</option>
              <option value="deployment">Standalone deployments</option>
              <option value="legacy">Legacy approvals</option>
            </select>
          </label>
          <label>
            Status
            <select id="workflow-status-filter">
              <option value="all">All statuses</option>
              <option value="pending">Pending</option>
              <option value="running">Running</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed or cancelled</option>
            </select>
          </label>
          <button
            className="button secondary workflow-quick-filter"
            id="pending-schedules-filter"
            type="button"
          >
            Pending schedules
          </button>
        </div>
        <div id="history-list" className="history-list" />
        <button
          className="button secondary legacy-create"
          id="new-release"
          hidden
        >
          Create legacy release
        </button>
        <button
          className="button primary recipient-create"
          id="new-recipient"
          hidden
        >
          Add email address
        </button>
        <button
          className="button primary recipient-create"
          id="new-project"
          hidden
        >
          New project
        </button>
        <button
          className="button primary recipient-create"
          id="new-connection"
          hidden
        >
          New connection
        </button>
      </aside>
      <section className="detail-panel" id="release-detail">
        <div className="empty-state">
          <span className="empty-icon">⌁</span>
          <strong>Select a release</strong>
          <p>
            Deployment stages, failures, cancellations, and workflow events
            appear here. Open Workflows to review every workflow and deployment
            in one place.
          </p>
        </div>
      </section>
    </section>
  );
}

function DialogHeading({ eyebrow, title }: { eyebrow: string; title: string }) {
  return (
    <div className="dialog-heading">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h2>{title}</h2>
      </div>
      <button
        type="button"
        className="icon-button dialog-close"
        aria-label="Close"
      >
        ×
      </button>
    </div>
  );
}

function ConfirmationDialog() {
  return (
    <dialog id="confirmation-dialog" className="app-dialog">
      <form id="confirmation-form" method="dialog">
        <DialogHeading
          eyebrow="Backend-first orchestration"
          title="Confirm combined release"
        />
        <div id="confirmation-summary" className="confirmation-summary" />
        <div className="form-grid one-column attachment-field-grid">
          <label>
            Email attachment (optional)
            <input type="file" name="attachment" />
            <span className="field-help">
              Included in workflow notification emails. Maximum 10 MB.
            </span>
          </label>
        </div>
        <div className="dialog-actions">
          <button type="button" className="button secondary dialog-close">
            Cancel
          </button>
          <button type="submit" className="button primary">
            Release
          </button>
        </div>
      </form>
    </dialog>
  );
}

function PromoteDialog() {
  return (
    <dialog id="promote-dialog" className="app-dialog compact-dialog">
      <form id="promote-form" method="dialog">
        <DialogHeading
          eyebrow="Standalone deployment"
          title="Confirm promote"
        />
        <div id="promote-summary" className="schedule-summary" />
        <div className="form-grid one-column">
          <label>
            Email attachment (optional)
            <input type="file" name="attachment" />
            <span className="field-help">
              Included in workflow notification emails. Maximum 10 MB.
            </span>
          </label>
        </div>
        <div className="dialog-actions">
          <button type="button" className="button secondary dialog-close">
            Cancel
          </button>
          <button type="submit" className="button primary">
            Promote
          </button>
        </div>
      </form>
    </dialog>
  );
}

function ScheduleDialog() {
  return (
    <dialog id="schedule-dialog" className="app-dialog compact-dialog">
      <form id="schedule-form" method="dialog">
        <DialogHeading
          eyebrow="Persisted deployment schedule"
          title="Schedule release"
        />
        <div id="schedule-summary" className="schedule-summary" />
        <div className="form-grid one-column">
          <label>
            Run at
            <input type="datetime-local" name="scheduled_for" required />
          </label>
          <label>
            Timezone
            <input name="timezone" required maxLength={100} />
          </label>
          <label>
            Release version
            <input
              name="release_version"
              required
              maxLength={100}
              placeholder="v1.5.0"
            />
          </label>
          <label>
            Update details
            <textarea
              name="release_notes"
              required
              maxLength={4000}
              rows={4}
              placeholder={
                "One change per line\nFix login timeout\nImprove loading speed"
              }
            />
          </label>
          <label>
            Email attachment (optional)
            <input type="file" name="attachment" />
            <span className="field-help">
              Included in schedule and workflow notification emails. Maximum 10
              MB.
            </span>
          </label>
          <fieldset className="recipient-picker">
            <legend>Additional email recipients</legend>
            <div
              id="schedule-recipient-options"
              className="recipient-options"
              aria-live="polite"
            >
              <span className="recipient-options-empty">
                Loading saved email recipients…
              </span>
            </div>
            <span className="field-help">
              Manage reusable addresses from Email recipients. Your selection is
              remembered for the next schedule.
            </span>
          </fieldset>
        </div>
        <div className="dialog-actions">
          <button type="button" className="button secondary dialog-close">
            Cancel
          </button>
          <button type="submit" className="button primary">
            Create schedule
          </button>
        </div>
      </form>
    </dialog>
  );
}

function RecipientDialog() {
  return (
    <dialog id="recipient-dialog" className="app-dialog compact-dialog">
      <form id="recipient-form" method="dialog">
        <DialogHeading
          eyebrow="Production notifications"
          title="Add email recipient"
        />
        <div className="form-grid one-column">
          <label>
            Email address
            <input
              name="email"
              type="email"
              required
              maxLength={320}
              placeholder="person@example.com"
            />
          </label>
        </div>
        <div className="dialog-actions">
          <button type="button" className="button secondary dialog-close">
            Cancel
          </button>
          <button type="submit" className="button primary">
            Add recipient
          </button>
        </div>
      </form>
    </dialog>
  );
}

function PublishDialog() {
  return (
    <dialog id="publish-dialog" className="app-dialog">
      <form id="publish-form" method="dialog">
        <DialogHeading eyebrow="Gitea release" title="Publish Version" />
        <div id="publish-summary" className="publish-summary" />
        <div className="form-grid one-column">
          <label>
            Version
            <input
              name="version"
              required
              maxLength={100}
              pattern="v?[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.-]+)?"
              placeholder="v1.8.0"
            />
          </label>
          <label>
            Release name
            <input name="name" required maxLength={255} placeholder="v1.8.0" />
          </label>
          <label>
            Release notes
            <textarea name="release_notes" maxLength={20000} rows={8} />
          </label>
          <label className="checkbox-field">
            <input name="prerelease" type="checkbox" /> Prerelease
          </label>
          <label className="checkbox-field">
            <input name="draft" type="checkbox" /> Draft
          </label>
        </div>
        <div className="dialog-actions">
          <button type="button" className="button secondary dialog-close">
            Cancel
          </button>
          <button type="submit" className="button primary">
            Publish
          </button>
        </div>
      </form>
    </dialog>
  );
}

function LegacyDialogs() {
  return (
    <>
      <dialog id="create-dialog" className="app-dialog">
        <form id="create-form" method="dialog">
          <DialogHeading
            eyebrow="Preserved v1 workflow"
            title="Create legacy release"
          />
          <div className="form-grid">
            <label>
              Repository
              <input name="repository" required maxLength={255} />
            </label>
            <label>
              Environment
              <input
                name="environment"
                required
                maxLength={100}
                defaultValue="pre-production"
              />
            </label>
            <label>
              Branch
              <input
                name="branch"
                required
                maxLength={255}
                defaultValue="main"
              />
            </label>
            <label>
              Commit SHA
              <input name="commit_sha" required maxLength={64} minLength={7} />
            </label>
            <label className="full-width">
              Message
              <textarea name="message" rows={3} />
            </label>
          </div>
          <div className="dialog-actions">
            <button type="button" className="button secondary dialog-close">
              Cancel
            </button>
            <button type="submit" className="button primary">
              Create release
            </button>
          </div>
        </form>
      </dialog>
      <dialog id="decision-dialog" className="app-dialog compact-dialog">
        <form id="decision-form" method="dialog">
          <div className="dialog-heading">
            <div>
              <span className="eyebrow">Legacy approval gate</span>
              <h2 id="decision-title">Approve release</h2>
            </div>
            <button
              type="button"
              className="icon-button dialog-close"
              aria-label="Close"
            >
              ×
            </button>
          </div>
          <input type="hidden" name="decision" />
          <div className="form-grid one-column">
            <label id="decision-message-field">
              Message
              <textarea name="message" rows={3} />
            </label>
          </div>
          <div className="dialog-actions">
            <button type="button" className="button secondary dialog-close">
              Cancel
            </button>
            <button
              type="submit"
              className="button primary"
              id="decision-submit"
            >
              Approve
            </button>
          </div>
        </form>
      </dialog>
    </>
  );
}

/**
 * Project, component and connection editors.
 *
 * The controller fills the connection dropdowns and flips the create-only
 * fields, because what a form is editing is only known at open time. Nothing
 * here ever renders a stored token: the API returns a hint, never the secret.
 */
function SettingsDialogs() {
  return (
    <>
      <dialog id="project-dialog" className="app-dialog">
        <form id="project-form" method="dialog">
          <div className="dialog-heading">
            <div>
              <span className="eyebrow">Project registry</span>
              <h2 id="project-dialog-title">New project</h2>
            </div>
            <button
              type="button"
              className="icon-button dialog-close"
              aria-label="Close"
            >
              ×
            </button>
          </div>
          <div className="form-grid">
            <label id="project-key-field">
              Key
              <input
                name="key"
                required
                maxLength={50}
                placeholder="smt-assistant"
              />
              <small>
                Lowercase letters, digits, hyphen or underscore. Permanent.
              </small>
            </label>
            <label>
              Name
              <input name="name" required maxLength={200} />
            </label>
            <label>
              Default target
              <input
                name="default_target"
                required
                maxLength={100}
                defaultValue="production"
              />
            </label>
            <label>
              Drone connection
              <select name="drone_connection_id" id="project-drone-connection">
                <option value="">Use the default connection</option>
              </select>
            </label>
            <label>
              Gitea connection
              <select name="gitea_connection_id" id="project-gitea-connection">
                <option value="">Use the default connection</option>
              </select>
            </label>
            <label className="full-width">
              Description
              <textarea name="description" rows={2} maxLength={2000} />
            </label>
          </div>
          <div className="dialog-actions">
            <button type="button" className="button secondary dialog-close">
              Cancel
            </button>
            <button
              type="submit"
              className="button primary"
              id="project-submit"
            >
              Create project
            </button>
          </div>
        </form>
      </dialog>

      <dialog id="component-dialog" className="app-dialog">
        <form id="component-form" method="dialog">
          <div className="dialog-heading">
            <div>
              <span className="eyebrow">Deployment order and repositories</span>
              <h2 id="component-dialog-title">Add component</h2>
            </div>
            <button
              type="button"
              className="icon-button dialog-close"
              aria-label="Close"
            >
              ×
            </button>
          </div>
          <div className="form-grid">
            <label id="component-key-field">
              Key
              <input name="key" required maxLength={50} placeholder="backend" />
              <small>Permanent. Used in stage names and in the API.</small>
            </label>
            <label>
              Display name
              <input name="display_name" maxLength={100} />
            </label>
            <label>
              Drone owner
              <input name="drone_owner" required maxLength={255} />
            </label>
            <label>
              Drone repository
              <input name="drone_repo" required maxLength={255} />
            </label>
            <label>
              Promote target override
              <input
                name="promote_target_override"
                maxLength={100}
                placeholder="Leave empty to use the release target"
              />
              <small>
                Two components sharing one repository must promote to different
                targets.
              </small>
            </label>
            <label>
              Drone connection
              <select
                name="drone_connection_id"
                id="component-drone-connection"
              >
                <option value="">Inherit from the project</option>
              </select>
            </label>
            <label>
              Gitea owner
              <input name="gitea_owner" maxLength={255} />
            </label>
            <label>
              Gitea repository
              <input name="gitea_repo" maxLength={255} />
            </label>
            <label>
              Gitea connection
              <select
                name="gitea_connection_id"
                id="component-gitea-connection"
              >
                <option value="">Inherit from the project</option>
              </select>
            </label>
            <label>
              Tag prefix
              <input name="tag_prefix" maxLength={30} placeholder="v" />
            </label>
            <label className="checkbox-field">
              <input type="checkbox" name="publish_enabled" defaultChecked />
              Publish a Gitea release for this component
            </label>
            <label className="checkbox-field">
              <input type="checkbox" name="is_active" defaultChecked />
              Active — offer this component when starting a release
            </label>
          </div>
          <div className="dialog-actions">
            <button type="button" className="button secondary dialog-close">
              Cancel
            </button>
            <button
              type="submit"
              className="button primary"
              id="component-submit"
            >
              Add component
            </button>
          </div>
        </form>
      </dialog>

      <dialog id="connection-dialog" className="app-dialog compact-dialog">
        <form id="connection-form" method="dialog">
          <div className="dialog-heading">
            <div>
              <span className="eyebrow">Upstream credentials</span>
              <h2 id="connection-dialog-title">New connection</h2>
            </div>
            <button
              type="button"
              className="icon-button dialog-close"
              aria-label="Close"
            >
              ×
            </button>
          </div>
          <div className="form-grid one-column">
            <label id="connection-kind-field">
              Kind
              <select name="kind">
                <option value="drone">Drone</option>
                <option value="gitea">Gitea</option>
              </select>
            </label>
            <label>
              Name
              <input name="name" required maxLength={100} />
            </label>
            <label>
              Server URL
              <input
                name="base_url"
                required
                maxLength={500}
                placeholder="https://drone.example.com"
              />
            </label>
            <label>
              Token
              <input
                name="token"
                type="password"
                autoComplete="new-password"
                maxLength={1000}
              />
              <small id="connection-token-hint">
                Stored encrypted. It is never sent back to this page.
              </small>
            </label>
            <label className="checkbox-field">
              <input type="checkbox" name="is_default" />
              Use as the default for this kind
            </label>
          </div>
          <div className="dialog-actions">
            <button type="button" className="button secondary dialog-close">
              Cancel
            </button>
            <button
              type="submit"
              className="button primary"
              id="connection-submit"
            >
              Create connection
            </button>
          </div>
        </form>
      </dialog>
    </>
  );
}

export function App() {
  const dispatch = useAppDispatch();
  const auth = useAppSelector((state) => state.auth);
  useEffect(() => {
    if (auth.status === "idle") void dispatch(loadSession());
  }, [auth.status, dispatch]);
  useReleaseController(auth.authenticated);

  if (auth.status === "idle" || auth.status === "loading") {
    return <LoginScreen configured={true} loading error={null} />;
  }
  if (!auth.authenticated || !auth.user) {
    return (
      <LoginScreen
        configured={auth.configured}
        loading={false}
        error={auth.error}
      />
    );
  }

  return (
    <>
      <div id="app">
        <TopBar
          login={auth.user.login}
          fullName={auth.user.full_name}
          onLogout={() => void dispatch(logout())}
        />
        <main className="page-shell" id="page-shell">
          <LaunchPanel />
          <HistoryWorkspace />
        </main>
      </div>
      <ConfirmationDialog />
      <PromoteDialog />
      <ScheduleDialog />
      <RecipientDialog />
      <PublishDialog />
      <LegacyDialogs />
      <SettingsDialogs />
      <div className="toast" id="toast" role="status" aria-live="polite" />
    </>
  );
}
