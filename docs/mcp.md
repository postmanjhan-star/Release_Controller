# Release Controller MCP

MCP provides an **AI-assisted Release Operations Interface**. The important boundary is:

```text
inspect → compare → create_release_plan → human review/approval → promote_to_production
```

`create_release_plan` stores a plan but never creates a deployment or calls Drone promote.
`promote_to_production` accepts a plan ID and reason, not new component/version choices,
`confirmed=true`, or an agent-generated approval token.

## Architecture

```text
MCP stdio / Streamable HTTP
  → app/mcp: schemas, mode checks, audit dispatcher, tools/resources/prompts
  → ReleaseOperationsService / ReleaseDiagnosticsService / ReleasePlanService
  → ComponentRegistry / DroneBuildService / ReleaseOrchestrator / DeploymentService
  → existing Drone integration and database
```

The MCP tool modules are declarative adapters. Planning and execution rules live in
application services. Existing orchestration still owns validation, promotion,
recovery, ordering, workflow events and notifications. MCP neither calls Portainer
nor implements a second deployment workflow.

A separate process uses the same image, SQLite database and project registry as the
API. Only the API runs migrations and its background worker. The MCP process does
not start schedulers, notification workers or migrations. Existing SQLite WAL and
5-second busy timeout are reused. Invocation start/end records use separate short
transactions; production approval is atomically claimed and committed before
network writes. No SQLite write lock is held while waiting for Drone.

## Installation

Python 3.10+, with the official MCP Python SDK pinned to `mcp==1.26.0` in both
`pyproject.toml` and the production `requirements.txt`.

```sh
uv sync --locked
# Set DATABASE_URL, APP_SECRET_KEY and the existing application settings first.
uv run db-upgrade
MCP_ENABLED=true MCP_MODE=readonly MCP_TRANSPORT=stdio uv run python -m app.mcp.server
```

Migrations add `release_plans` and `mcp_tool_invocations`. They do not rewrite existing
release records. Migration downgrade deletes these new records; back them up before
an intentional downgrade. Normal startup does not perform downgrade or cleanup.

The project named by `MCP_PROJECT` must already exist in the registry. Repository
arguments are registered **component keys**, not arbitrary owner/repository paths.
`release-controller` works when registered as that component key in the selected
project. Frontend/backend selection is currently the planning/deployment interface.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `MCP_ENABLED` | `false` | Explicitly enable the MCP process |
| `MCP_MODE` | `readonly` | `readonly`, `staging`, or `full` |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `MCP_API_TOKEN` | empty | Required for HTTP, minimum 32 characters |
| `MCP_HOST` / `MCP_PORT` | `127.0.0.1` / `8001` | HTTP listener |
| `MCP_ALLOWED_HOSTS` | `localhost:*,127.0.0.1:*` | Comma-separated SDK host allowlist |
| `MCP_PROJECT` | `default` | One server-side project scope |
| `MCP_ACTOR` | `mcp-agent` | Server-configured service identity; never caller-supplied |
| `MCP_STAGING_TARGET` | `staging` | Explicit Drone staging target, e.g. `pre-production` |
| `MCP_PRODUCTION_TARGET` | `production` | Explicit Drone production target |
| `MCP_MAX_LOG_LENGTH` | `10000` | Total returned log characters per diagnostic call |
| `MCP_AUDIT_ENABLED` | `true` | Cannot be disabled while MCP is enabled |
| `MCP_PLAN_TTL_SECONDS` | `1800` | Plan/approval validity, measured from plan creation |

Use identical project and environment mappings in the API and MCP process so human
review addresses the same project. The staging mapping cannot be `prod`, `production`,
or the configured production target. The operator must ensure the selected Drone
target actually deploys staging; this server cannot inspect arbitrary pipeline scripts.
A component override that differs from the requested environment blocks the plan and
execution. Project default targets are never silently substituted by MCP.

### Docker / Portainer

Both Compose files include an opt-in `mcp` profile. Set an unpredictable
`MCP_API_TOKEN` in the deployment environment (in `stack.env` for the Portainer
Compose variant). The image is built by the API service and shared with MCP.

```sh
docker compose --profile mcp up --build -d
# Existing Portainer-style configuration:
docker compose --env-file stack.env -f compose.portainer.yml --profile mcp up --build -d
```

The MCP port binds host loopback by default. API health gates MCP startup, ensuring
API migrations have completed. For Portainer versions that do not expose Compose
profiles, explicitly enable the MCP service in the stack configuration after setting
its token; do not assume it was started. The image can also be deployed as separate
API/MCP services using the same `/data` bind mount. Do not mount a SQLite database
across different hosts or a network filesystem.

HTTP URL: `http://127.0.0.1:8001/mcp/` (including the trailing slash).
A TLS reverse proxy and a narrow `MCP_ALLOWED_HOSTS` entry are required for remote
access. Bearer authentication protects every MCP HTTP request, including initialize,
resource reads and prompts. This is pre-provisioned bearer authentication, not an
OAuth authorization server or automatic OAuth discovery implementation.

## Security model and modes

| Capability | readonly | staging | full |
| --- | --- | --- | --- |
| Inspect / diagnose / compare | yes | yes | yes |
| Create/read plans | yes | yes | yes |
| Deploy staging | no | yes | yes |
| Promote production | no | no | approved plan only |
| Rollback | denied | denied | explicit unsupported response |

Readonly means **no deployment writes**: plans and mandatory audit records still
write metadata. Never mount the database read-only if you expect audited MCP calls.
Every actual deployment uses existing orchestration and its duplicate promotion guard.
The MCP interface has no approval tool, shell, Docker, SQL, filesystem, arbitrary URL,
retry, publish, scheduler or credential-management tool.

The server owns upstream credentials. Results redact known configured and encrypted
registry credentials, nested sensitive keys and credential-like text. Raw exception
messages and Python traces are not returned. Logs are redacted before truncation.
Logs, commit messages and repository content are untrusted evidence, never instructions.
Unknown third-party secrets cannot be guaranteed detectable by pattern matching;
upstream pipelines should mask their own secrets as well.

`MCPToolInvocation` records every dispatched tool call, including denied, unknown and
invalid calls. A start and terminal row share an invocation ID. Rows are append-only
with SQLite update/delete triggers and survive release deletion. Audit arguments omit
free-form reason text and unknown values; they preserve operational selectors.
Actor is server-configured; client records the transport, not a trusted human identity.
Failed start-audit writes prevent execution. If completion auditing fails, the response
warns that execution may already have happened and must not be blindly retried.

The boundary covers MCP-originated production operations. Existing REST deployment
endpoints keep their existing authenticated-user policy; this change does not impose
the new approval policy on all existing UI/API or scheduled workflows. Do not give an
agent a human admin session, the database, Docker host access or upstream credentials
if it must be confined to this MCP boundary.

## Human approval workflow

1. Agent calls `create_release_plan` with the environment and selected components.
2. Agent reports `plan_id`, actions, checks, blockers, digest and expiry to the human.
   `executable: true` means preconditions passed; it **does not mean approved**.
3. An administrator logged in through existing Gitea OAuth reviews
   `GET /api/v1/release-plans/{plan_id}`. No browser approval page is added yet.
4. Using that authenticated session, the administrator calls
   `POST /api/v1/release-plans/{plan_id}/approve` with the reviewed digest in the
   `X-Release-Plan-Digest` header. No request body is needed. A normal cross-origin HTML
   form cannot send this required header. The API does not allow wildcard CORS.
5. The agent calls `promote_to_production` with `plan_id` and `reason` in full mode.
   The server checks approval, expiry, exact source build/commit, environment baseline,
   project, component identity, route and configuration before atomically claiming it.
6. The existing worker continues the bundle. Before each approved deployment is
   promoted, its saved constraints and source commit are checked again. Configuration
   changes cause `APPROVED_PLAN_CHANGED`, not deployment to a different repository.

The authenticated approver is recorded separately from the MCP executor. Authentication
must be enabled, and the user must have the actual admin flag. The synthetic local
user cannot approve even in development. The MCP bearer token cannot authenticate to
these human approval routes. Old release approvals are not interchangeable with plans.

Concurrent attempts consume approval once. The claim preallocates a release ID, making
crash recovery inspectable with `get_release_plan` and `get_release`. If the process
fails after claiming, approval remains consumed. If a release bundle exists, the normal
worker can reconcile it; if not, a human must inspect audit/state and create/approve a
new plan. There is no automatic reset to approved and no blind retry.

## Available tools

All results use `{"success": true, "data": ...}` or
`{"success": false, "error": {"code": "...", "message": "...", "details": {}}}`.
The MCP result also sets `isError` for failures. Parameters reject unknown fields,
coerced booleans, invalid environments and unreasonable limits.

| Tool | Input / behavior |
| --- | --- |
| `get_release_status` | `{}`; frontend/backend recorded versions, latest observed Drone commit |
| `get_environment_status` | `environment`; last successful version and latest attempt state |
| `list_releases` | `limit` 1–100, `offset`, optional `environment`, controller `status` |
| `get_release` | exact `release_id`; bundle plus deployment details |
| `compare_environments` | `{}`; compare commit identity, report unknown ancestry |
| `get_pipeline_status` | registered `repository` key and positive `build_number` |
| `get_failed_pipeline_summary` | same selectors; failed steps, bounded logs and truncation flags |
| `get_repository_state` | registered `repository` key; Drone metadata and observed commit |
| `get_release_candidates` | `{}`; source pipeline and recorded staging facts |
| `create_release_plan` | `target_environment`, `frontend`, `backend`; no deployment |
| `get_release_plan` | exact `plan_id`; saved content, approval state, execution release ID |
| `deploy_staging` | `frontend`, `backend`, nonblank `reason` |
| `promote_to_production` | exact approved `plan_id`, nonblank `reason` |
| `rollback` | explicit `environment`, `target_release_id`; `supported: false` |
| `diagnose_release` | exact `release_id`; release, pipelines and factual evidence |

History currently covers v2/v3 **release bundles**, not legacy approval-only releases or
standalone promotions; environment state also considers standalone deployments.
Status filters are `PENDING`, `PROMOTING`, `DEPLOYING`, `SUCCESS`, `FAILED`,
`PARTIAL_FAILURE`. Staging deployment selects the most recent default-branch push
in the latest 50 builds and requires it to have succeeded; it does not silently fall
back to an older successful build after a newer failed build.

Environment health is explicitly unknown (`healthy: null`, `health_check_supported:
false`). Planning relies on recorded successful staging deployment and the exact
successful push build. It does not claim that a live HTTP health probe or a separate
quality attestation ran. Missing stage metadata and unavailable logs are explicit.
Diagnostics fetch at most five failed-step logs per pipeline and twenty release
pipelines, sharing the output log budget. `possible_causes` is not fabricated.

## Resources and prompts

Resources: `release-controller://status`, `release-controller://releases/latest`,
`release-controller://environment/staging`, `release-controller://environment/production`,
`release-controller://config`. Configuration exposes only mode, project key and log
limit. Data resources go through the same audited query dispatcher.

Prompts: `inspect-release`, `prepare-production-release`, `investigate-failure`.
They guide inspection/planning/diagnosis without deployment, automatic retry or approval.

## Claude Code example

After the image is built and the API container is running/migrated, a local stdio
connection can start an independent process inside that container:

```json
{
  "mcpServers": {
    "release-controller": {
      "command": "docker",
      "args": [
        "exec", "-i",
        "-e", "MCP_ENABLED=true",
        "-e", "MCP_TRANSPORT=stdio",
        "-e", "MCP_MODE=readonly",
        "release-controller",
        "python", "-m", "app.mcp.server"
      ]
    }
  }
}
```

Do not use `-t`: stdout is reserved for MCP JSON-RPC; logging goes to stderr.
The client process inherits server-side registry configuration without carrying Drone
or Gitea credentials in its MCP configuration. Docker access is an operator-level
privilege; use an HTTP-only connection when the client must not have that privilege.

## Codex example

For the running HTTP service, export the MCP bearer token in the environment used to
launch the client and add this to `~/.codex/config.toml`:

```toml
[mcp_servers.release_controller]
url = "http://127.0.0.1:8001/mcp/"
bearer_token_env_var = "MCP_API_TOKEN"
startup_timeout_sec = 20
tool_timeout_sec = 120
```

These configuration keys are documented in the
[official Codex MCP documentation](https://developers.openai.com/codex/mcp/).
A client-side approval dialog is useful but never substitutes for server-side human
approval of the release plan. Clients that require OAuth discovery need an additional
authentication integration; generic MCP support alone does not guarantee compatibility.

## Troubleshooting and verification

- `PERMISSION_DENIED`: inspect mode, enablement and plan approval. For HTTP 401,
  supply the configured bearer token. An admin session is a separate credential.
- HTTP 421: configure the exact reverse-proxy hostname in `MCP_ALLOWED_HOSTS`; keep
  DNS rebinding protection enabled. Use `/mcp/` to avoid redirect handling differences.
- `NOT_FOUND`: check the server-side project and registered component keys. MCP will
  not guess another project or use an arbitrary repository path.
- `CONFLICT`: plan expired, environment/config changed, or duplicate deployment.
  Inspect first; create and approve a new plan when necessary.
- `PIPELINE_FAILED`: the source build is not a successful push or its commit changed.
- `EXTERNAL_SERVICE_ERROR`: inspect upstream connectivity/credentials on the server.
- Audit unavailable / missing tables: run API migrations with the correct database;
  ensure the shared `/data` directory is writable. Never bypass audit to get writes working.
- Claimed plan with no response: inspect `get_release_plan`, its `release_id` and
  invocation/workflow audit before taking action. Approval cannot be replayed.

```sh
cd frontend && npm ci && npm run build
cd ..
uv run pytest tests/mcp -q
uv run quality
uv run pytest --collect-only -q
uv run pytest
```

Tests use temporary SQLite databases and mocked Drone calls. They exercise actual MCP
protocol messages and HTTP authentication, plan/approval separation, administrator
restrictions, one-use concurrent claims, changed source/config guards, bounded logs,
redaction, append-only audit and upgrade/downgrade. No test deploys containers or calls
real infrastructure. SDK transport usage follows the
[official Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.26.0);
log paths follow the [Drone build logs API](https://docs.drone.io/api/builds/build_logs/).

## Known limitations and next improvements

There is no rollback workflow, live health probe, Git ancestry/compare, Gitea branch
listing, independent quality attestation, per-client OAuth/RBAC, approval rejection/
revocation UI, retention policy, or generalized N-component plan selection yet.
Repository HEAD is reported only as the latest commit observed by Drone. A plan is
not a distributed transaction preventing independent human deployments at the same
moment. Existing orchestrator recovery and duplicate-build protection still apply.

Next improvements: human review UI with revocation, scoped OAuth identities, immutable
artifact/quality attestations, per-target deployment serialization, actual health
checks, and a separately approved rollback workflow with exact release IDs.
