# Release Worker v2.2 規格

> 歷史版本規格：保留原始需求與設計，部分流程與部署方式已變更。
> 目前操作以[文件索引](../README.md)、[API 指南](../api-guide.md)及[部署設定](../deployment.md)為準。

> Version: **2.2**
>
> Upgrade path: **v1 → v2.2 directly**
>
> This specification replaces the previous v2/v2.1 draft.  
> The current project is still on v1, so implementation must upgrade the existing v1 codebase directly to this specification without requiring an intermediate v2.1 deployment.

---

# 1. Goal

Upgrade the existing `release-controller` into a Release Worker / Release Orchestrator that can:

1. Query Drone builds from two independent repositories.
2. Deploy only Frontend.
3. Deploy only Backend.
4. Select one Frontend build and one Backend build and publish them together as one Release Bundle.
5. Keep Frontend and Backend Drone build numbers independent.
6. Track source build and promotion build separately.
7. Show exactly which stage failed.
8. Mark downstream work as `CANCELLED` when a previous required stage fails.
9. Preserve failure reason, error code, failed stage, and cancellation reason.
10. Represent release execution with BPMN / workflow state.
11. Keep Drone responsible for the actual deployment.
12. Keep the existing Podman deployment contract working.

---

# 2. Current Environment Contract

Do not break the current deployment.

```text
Gitea VM: gitea.example.test

Drone:
http://drone.example.test

Release Controller:
http://<INTERNAL_HOST>

Container:
release-controller

Image:
localhost/release-controller:latest

Container Port:
8000

Podman Network:
release-net

SQLite Host:
~/services/release-controller/data/release.db

SQLite Container:
/data/release.db
```

Existing health endpoint:

```http
GET /health
```

must remain available.

---

# 3. Repository Model

Frontend and Backend are **two independent projects**.

```text
Frontend Repository
├─ independent Gitea repository
├─ independent Drone repository
├─ independent Drone build sequence
└─ independent promote action

Backend Repository
├─ independent Gitea repository
├─ independent Drone repository
├─ independent Drone build sequence
└─ independent promote action
```

Example:

```text
Frontend Build #123
Backend  Build #456
```

This is valid.

Never require:

```text
Frontend build number == Backend build number
```

Never treat both repositories as one Drone repository.

---

# 4. Supported Release Modes

The system must support all three modes.

```text
FRONTEND_ONLY
BACKEND_ONLY
BUNDLE
```

## FRONTEND_ONLY

```text
Frontend Build #123
        ↓
Validate
        ↓
Promote Frontend Repo
        ↓
Wait Drone Deployment
        ↓
SUCCESS / FAILED
```

Backend does not participate in this workflow.

Do **not** create a fake Backend `CANCELLED` record.

---

## BACKEND_ONLY

```text
Backend Build #456
        ↓
Validate
        ↓
Promote Backend Repo
        ↓
Wait Drone Deployment
        ↓
SUCCESS / FAILED
```

Frontend does not participate in this workflow.

Do **not** create a fake Frontend `CANCELLED` record.

---

## BUNDLE

```text
Frontend Build #123
Backend Build #456
        ↓
Create one Release Bundle
        ↓
Backend First
        ↓
Backend succeeds
        ↓
Frontend Promote
        ↓
Frontend succeeds
        ↓
Bundle SUCCESS
```

---

# 5. Why Backend First

Default combined release strategy:

```text
Backend → Frontend
```

Reason:

```text
A newer frontend may depend on a newer backend API.
```

Therefore v2.2 does not deploy both components in parallel by default.

Parallel release is a future feature.

---

# 6. High-Level Architecture

```text
                     Gitea
                       │
          ┌────────────┴────────────┐
          │                         │
          ▼                         ▼
   Frontend Repo               Backend Repo
          │                         │
          ▼                         ▼
   Drone Frontend CI          Drone Backend CI
          │                         │
    Build #123 success         Build #456 success
          │                         │
          └────────────┬────────────┘
                       ▼
            ┌──────────────────────┐
            │ Release Controller   │
            │                      │
            │ Drone API Client     │
            │ Release Service      │
            │ Deployment Service   │
            │ Release Orchestrator │
            │ BPMN Workflow        │
            │ SQLite               │
            └──────────┬───────────┘
                       │
             ┌─────────┴─────────┐
             │                   │
             ▼                   ▼
      Promote Backend      Promote Frontend
             │                   │
             ▼                   ▼
       Drone Deploy         Drone Deploy
```

---

# 7. Release Controller Responsibilities

Release Controller is responsible for:

```text
Deployment Gate
Release Orchestration
Workflow State
Deployment History
Failure Diagnosis
Cancellation Tracking
```

It must:

- Query Frontend builds.
- Query Backend builds.
- Validate source builds before promotion.
- Promote Frontend independently.
- Promote Backend independently.
- Create combined Release Bundles.
- Execute Backend-first orchestration.
- Track Drone promotion builds.
- Refresh Drone build status.
- Record the exact failed stage.
- Record error code and message.
- Mark downstream stages/components `CANCELLED` when appropriate.
- Aggregate child Deployment states into Release Bundle state.
- Expose release progress to the UI.

It must **not**:

- SSH directly into the production server.
- Execute production deployment shell scripts itself.
- Replace Drone deployment pipelines.
- Modify Drone secrets.
- Automatically rollback in v2.2.

---

# 8. Drone Runtime Configuration

Use environment variables.

```env
DRONE_SERVER=http://drone.example.test
DRONE_TOKEN={drone_token}

DRONE_FRONTEND_REPO_OWNER=example-owner
DRONE_FRONTEND_REPO_NAME=frontend-repo

DRONE_BACKEND_REPO_OWNER=example-owner
DRONE_BACKEND_REPO_NAME=backend-repo

DRONE_DEFAULT_TARGET=pre-production
```

Do not hard-code:

```text
DRONE_TOKEN
repo owner
repo name
target
```

---

# 9. Server Runtime Env File

Use:

```text
~/services/release-controller/config/release-controller.env
```

Example:

```env
DRONE_SERVER=http://drone.example.test
DRONE_TOKEN={drone_token}

DRONE_FRONTEND_REPO_OWNER=example-owner
DRONE_FRONTEND_REPO_NAME=Frontend_Project

DRONE_BACKEND_REPO_OWNER=example-owner
DRONE_BACKEND_REPO_NAME=Backend_Project

DRONE_DEFAULT_TARGET=pre-production
```

Permissions:

```bash
chmod 600 ~/services/release-controller/config/release-controller.env
```

Deployment must pass this file to Podman:

```bash
--env-file ~/services/release-controller/config/release-controller.env
```

The real token must never be committed.

---

# 10. Component Enum

```text
frontend
backend
```

Example:

```python
class Component(str, Enum):
    FRONTEND = "frontend"
    BACKEND = "backend"
```

Repository resolution must be centralized.

Example:

```python
get_drone_repo(component)
```

Do not scatter repository mapping logic throughout routes.

---

# 11. Release Mode Enum

```text
FRONTEND_ONLY
BACKEND_ONLY
BUNDLE
```

Example:

```python
class ReleaseMode(str, Enum):
    FRONTEND_ONLY = "FRONTEND_ONLY"
    BACKEND_ONLY = "BACKEND_ONLY"
    BUNDLE = "BUNDLE"
```

---

# 12. Drone API Client

Recommended structure:

```text
app/integrations/drone/
├── __init__.py
├── client.py
├── schemas.py
└── exceptions.py
```

Required operations:

```python
list_builds(owner, repo, limit=20)
get_build(owner, repo, build_number)
promote_build(owner, repo, build_number, target)
```

Use:

```text
httpx
```

Authentication:

```http
Authorization: Bearer <DRONE_TOKEN>
```

Timeout:

```text
5–10 seconds
```

No unlimited requests.

---

# 13. Drone Exceptions

Define explicit integration exceptions.

```text
DroneConnectionError
DroneAuthenticationError
DroneNotFoundError
DronePromoteError
DroneTimeoutError
DroneUnexpectedResponseError
```

Never expose:

```text
DRONE_TOKEN
Authorization header
httpx traceback
```

to the frontend.

---

# 14. Build Query API

Frontend:

```http
GET /api/v1/drone/frontend/builds
```

Backend:

```http
GET /api/v1/drone/backend/builds
```

Query:

```text
limit
```

Default:

```text
20
```

Maximum:

```text
100
```

Example response:

```json
{
  "component": "frontend",
  "items": [
    {
      "number": 123,
      "status": "success",
      "event": "push",
      "branch": "pre-production",
      "commit_sha": "abcdef1234567890",
      "commit_message": "fix validation",
      "author": "user",
      "created_at": "2026-08-21T04:00:00Z",
      "promotable": true
    }
  ]
}
```

---

# 15. Promotable Rules

A source build is promotable only when:

```text
status = success
event = push
```

At minimum reject:

```text
failure
error
killed
running
pending
promote
rollback
```

Before every promote operation:

```text
Release Controller
    ↓
Drone get_build(...)
    ↓
Validate actual current build
```

Do not trust a status sent by the browser.

Invalid source build:

```http
409 Conflict
```

Example:

```json
{
  "detail": "Build is not promotable"
}
```

---

# 16. Standalone Frontend Promote API

```http
POST /api/v1/drone/frontend/builds/{build_number}/promote
```

Request:

```json
{
  "target": "pre-production"
}
```

If omitted:

```text
target = DRONE_DEFAULT_TARGET
```

This creates a standalone deployment:

```text
release_bundle_id = null
component = frontend
```

---

# 17. Standalone Backend Promote API

```http
POST /api/v1/drone/backend/builds/{build_number}/promote
```

Same behavior, but using the Backend Drone repository.

This creates:

```text
release_bundle_id = null
component = backend
```

---

# 18. Combined Release API

Create and start one combined release:

```http
POST /api/v1/releases/promote
```

Request:

```json
{
  "frontend_build_number": 123,
  "backend_build_number": 456,
  "target": "pre-production"
}
```

Both build numbers are independent.

Do not require equality.

---

# 19. Combined Validation Must Finish Before Promote

Before sending any Drone Promote request:

```text
Validate Frontend #123
        ↓
Validate Backend #456
        ↓
Duplicate checks
        ↓
Only then create/start promotion workflow
```

If Frontend validation fails:

```text
Do not promote Backend.
```

If Backend validation fails:

```text
Do not promote Frontend.
```

This prevents a half-started release caused by pre-validation errors.

---

# 20. BPMN / Workflow Requirement

v2.2 must include executable or machine-readable workflow state for release execution.

The project already uses / may continue using:

```text
SpiffWorkflow
```

Recommended BPMN files:

```text
app/workflows/bpmn/
├── frontend_only.bpmn
├── backend_only.bpmn
└── release_bundle.bpmn
```

If the existing architecture uses another location, preserve project conventions.

BPMN is used to represent:

- current stage
- successful stages
- failed stage
- cancelled downstream stages
- workflow completion state

The database remains the persistent audit source.

---

# 21. Frontend-Only BPMN

Logical model:

```text
Start
  ↓
Validate Frontend
  ↓
Create Deployment
  ↓
Promote Frontend
  ↓
Wait Frontend Deployment
  ↓
Success?
  ├─ Yes → SUCCESS End
  └─ No  → FAILED End
```

Errors should be represented with Error Boundary Events or equivalent workflow error transitions.

---

# 22. Backend-Only BPMN

Logical model:

```text
Start
  ↓
Validate Backend
  ↓
Create Deployment
  ↓
Promote Backend
  ↓
Wait Backend Deployment
  ↓
Success?
  ├─ Yes → SUCCESS End
  └─ No  → FAILED End
```

---

# 23. Combined Release BPMN

Required logical flow:

```text
Start
  ↓
Validate Frontend
  ↓
Validate Backend
  ↓
Create Release Bundle
  ↓
Create Backend Deployment
  ↓
Create Frontend Deployment (WAITING)
  ↓
Promote Backend
  ↓
Wait Backend Deployment
  ↓
Backend Result
  ├─ FAILED
  │    ↓
  │  Mark Backend FAILED
  │    ↓
  │  Cancel Frontend
  │    ↓
  │  Release FAILED
  │
  └─ SUCCESS
       ↓
     Promote Frontend
       ↓
     Wait Frontend Deployment
       ↓
     Frontend Result
       ├─ FAILED
       │    ↓
       │  Release PARTIAL_FAILURE
       │
       └─ SUCCESS
            ↓
          Release SUCCESS
```

---

# 24. BPMN Error Events

Use BPMN error paths for meaningful failures.

Examples:

```text
DroneAuthenticationError
DroneConnectionError
DronePromoteError
DroneDeploymentFailed
DroneTimeout
```

A task failure must result in:

```text
failed_stage
error_code
error_message
```

being persisted.

---

# 25. BPMN Timer / Timeout

Long-running deployment waiting must have a timeout concept.

Recommended model:

```text
Wait Drone Deployment
        │
        ├─ completion message/poll result
        │
        └─ Timer Boundary Event
                ↓
             FAILED
```

Configuration may be environment-based.

Example:

```env
DEPLOYMENT_TIMEOUT_SECONDS=1800
```

Default:

```text
1800 seconds
```

v2.2 does not require a background worker; timeout can be evaluated during refresh calls.

---

# 26. Workflow Stages

Use stable stage identifiers.

At minimum:

```text
VALIDATE_FRONTEND
VALIDATE_BACKEND

CREATE_RELEASE_BUNDLE

PROMOTE_BACKEND
WAIT_BACKEND_DEPLOYMENT

PROMOTE_FRONTEND
WAIT_FRONTEND_DEPLOYMENT

COMPLETE_RELEASE
```

Standalone workflows use only relevant stages.

Example Frontend-only:

```text
VALIDATE_FRONTEND
PROMOTE_FRONTEND
WAIT_FRONTEND_DEPLOYMENT
COMPLETE_RELEASE
```

---

# 27. Why Stable Stage IDs Matter

Do not store only human-readable text.

Store:

```text
failed_stage = PROMOTE_BACKEND
```

and optionally render:

```text
Promote Backend
```

in UI.

Stable IDs allow:

- filtering
- tests
- workflow migration
- analytics
- consistent UI

---

# 28. Deployment Status

Deployment states:

```text
WAITING
PROMOTING
DEPLOYING
SUCCESS
FAILED
CANCELLED
```

Meaning:

### WAITING

Component belongs to the workflow but cannot start yet.

Example:

```text
Frontend WAITING
while Backend is deploying
```

### PROMOTING

Release Controller is requesting a Drone promote build.

### DEPLOYING

Drone promotion build exists and is pending/running.

### SUCCESS

Drone deployment completed successfully.

### FAILED

This component attempted execution and failed.

### CANCELLED

This component was intentionally not executed because a required earlier stage/component failed.

---

# 29. CANCELLED Semantics

Use `CANCELLED` only for a component that **belongs to this release** but is prevented from executing.

Example:

```text
Combined Release

Backend FAILED
Frontend CANCELLED
```

Frontend cancellation reason:

```text
Backend deployment failed
```

Do not use `CANCELLED` for:

```text
Frontend-only release → Backend
```

because Backend is not part of that workflow at all.

---

# 30. Release Bundle Status

Bundle states:

```text
PENDING
PROMOTING
DEPLOYING
SUCCESS
FAILED
PARTIAL_FAILURE
```

Meaning:

### PENDING

Bundle created but no promote started yet.

### PROMOTING

A Drone promotion build is being created.

### DEPLOYING

At least one child Deployment is actively deploying.

### SUCCESS

All participating components completed successfully.

### FAILED

No prior participating component completed successfully and the release cannot continue.

Example:

```text
Backend FAILED
Frontend CANCELLED
→ Release FAILED
```

### PARTIAL_FAILURE

At least one component was successfully deployed, but a later component failed.

Example:

```text
Backend SUCCESS
Frontend FAILED
→ PARTIAL_FAILURE
```

---

# 31. Failure Outcome Matrix

| Backend | Frontend | Bundle |
|---|---|---|
| SUCCESS | SUCCESS | SUCCESS |
| FAILED | CANCELLED | FAILED |
| SUCCESS | FAILED | PARTIAL_FAILURE |
| DEPLOYING | WAITING | DEPLOYING |
| SUCCESS | DEPLOYING | DEPLOYING |

Standalone releases do not require a Bundle record unless the existing design prefers a common parent abstraction.

---

# 32. Release Bundle Table

Create:

```text
release_bundles
```

Required fields:

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID string | yes | Release ID |
| mode | string | yes | `BUNDLE` |
| target | string | yes | Target environment |
| status | string | yes | Bundle status |
| workflow_instance_id | string/null | no | Workflow instance |
| current_stage | string/null | no | Current stage |
| failed_stage | string/null | no | Failed stage |
| error_code | string/null | no | Stable error code |
| error_message | text/null | no | Safe error message |
| requested_by | string/null | no | User |
| created_at | datetime | yes | UTC |
| updated_at | datetime | yes | UTC |
| started_at | datetime/null | no | UTC |
| failed_at | datetime/null | no | UTC |
| finished_at | datetime/null | no | UTC |

---

# 33. Deployment Table

Create:

```text
deployments
```

Required fields:

| Field | Type | Required | Description |
|---|---|---:|---|
| id | UUID string | yes | Deployment ID |
| release_bundle_id | UUID/null | no | Bundle FK |
| component | string | yes | frontend/backend |
| drone_owner | string | yes | Repo owner |
| drone_repository | string | yes | Repo name |
| source_build_number | integer | yes | Original CI build |
| promotion_build_number | integer/null | no | Drone promote build |
| commit_sha | string | yes | Full Git SHA |
| branch | string | yes | Source branch |
| target | string | yes | Deployment target |
| status | string | yes | Deployment status |
| current_stage | string/null | no | Current execution stage |
| failed_stage | string/null | no | Stage that failed |
| error_code | string/null | no | Stable error code |
| error_message | text/null | no | Safe failure message |
| cancel_reason | text/null | no | Why cancelled |
| requested_by | string/null | no | User |
| created_at | datetime | yes | UTC |
| updated_at | datetime | yes | UTC |
| started_at | datetime/null | no | UTC |
| failed_at | datetime/null | no | UTC |
| cancelled_at | datetime/null | no | UTC |
| finished_at | datetime/null | no | UTC |

---

# 34. Workflow Event / Audit Table

Add an append-only workflow event table:

```text
workflow_events
```

Recommended fields:

| Field | Type |
|---|---|
| id | UUID string |
| release_bundle_id | UUID/null |
| deployment_id | UUID/null |
| workflow_instance_id | string/null |
| stage | string |
| event_type | string |
| status | string/null |
| error_code | string/null |
| message | text/null |
| created_at | datetime |

Example events:

```text
STAGE_STARTED
STAGE_SUCCEEDED
STAGE_FAILED
DEPLOYMENT_CANCELLED
PROMOTION_CREATED
DRONE_STATUS_CHANGED
RELEASE_COMPLETED
```

This table is append-only audit history.

Do not update old events.

---

# 35. Example Failure History

Example:

```text
Release R001

VALIDATE_FRONTEND
SUCCESS

VALIDATE_BACKEND
SUCCESS

PROMOTE_BACKEND
SUCCESS

WAIT_BACKEND_DEPLOYMENT
FAILED
error_code = DRONE_BUILD_FAILED

Frontend Deployment
CANCELLED
cancel_reason = Backend deployment failed

Release
FAILED
failed_stage = WAIT_BACKEND_DEPLOYMENT
```

The UI must be able to reconstruct this from persisted state/events.

---

# 36. Error Codes

Use stable error codes.

At minimum:

```text
BUILD_NOT_FOUND
BUILD_NOT_PROMOTABLE
DUPLICATE_PROMOTION

DRONE_UNAVAILABLE
DRONE_TIMEOUT
DRONE_AUTH_FAILED
DRONE_PROMOTE_FAILED
DRONE_BUILD_FAILED
DRONE_BUILD_KILLED

WORKFLOW_TIMEOUT
WORKFLOW_STATE_ERROR
UNKNOWN_RELEASE_ERROR
```

Do not use free text as the only failure classification.

---

# 37. Duplicate Promote Protection

For:

```text
component
source_build_number
target
```

if an existing deployment is:

```text
PROMOTING
DEPLOYING
SUCCESS
```

reject another promote:

```http
409 Conflict
```

Example:

```json
{
  "detail": "This build has already been promoted to this target"
}
```

If the previous deployment is:

```text
FAILED
CANCELLED
```

a retry may be allowed according to workflow rules.

A retry creates a new deployment record rather than mutating old audit history.

---

# 38. Combined Release Duplicate Rule

Before a Bundle starts, perform duplicate checks for:

```text
Frontend source build + target
Backend source build + target
```

If either is already successfully deployed to the same target:

```http
409 Conflict
```

v2.2 does not implement automatic:

```text
skip already deployed component
```

---

# 39. Promotion Build Tracking

Drone promote creates a new build.

Example:

```text
Frontend Source #123
        ↓ promote
Frontend Promotion #124

Backend Source #456
        ↓ promote
Backend Promotion #457
```

Always persist:

```text
source_build_number
promotion_build_number
```

---

# 40. Standalone Deployment Refresh

API:

```http
POST /api/v1/deployments/{deployment_id}/refresh
```

Behavior:

```text
Read promotion_build_number
        ↓
Drone get_build(...)
        ↓
Map Drone state
        ↓
Persist Deployment
        ↓
Persist workflow event
```

Mapping:

```text
pending/running
→ DEPLOYING

success
→ SUCCESS

failure/error/killed
→ FAILED
```

---

# 41. Combined Release Refresh

API:

```http
POST /api/v1/releases/{release_id}/refresh
```

This endpoint is allowed to advance the workflow.

Responsibilities:

```text
1. Load workflow / release state
2. Refresh active Drone promotion build
3. Persist status transition
4. Detect failure / timeout
5. Cancel downstream component when required
6. Start next component when previous required component succeeds
7. Aggregate Bundle status
8. Append workflow events
9. Return current release state
```

---

# 42. No Background Worker in v2.2

Do not add:

```text
Redis
Celery
Huey
RQ
Kafka
```

for v2.2.

The UI may poll:

```text
every 5 seconds
```

while the release is:

```text
PROMOTING
DEPLOYING
```

Polling stops on terminal state:

```text
SUCCESS
FAILED
PARTIAL_FAILURE
```

---

# 43. Backend Failure Cancellation Rule

If Backend fails before Frontend begins:

```text
Backend:
FAILED

Frontend:
CANCELLED

Frontend.cancel_reason:
"Backend deployment failed"

Release:
FAILED
```

Frontend Promote must not be called.

Persist a workflow event:

```text
DEPLOYMENT_CANCELLED
component=frontend
reason=Backend deployment failed
```

---

# 44. Frontend Failure Rule

If Backend has already succeeded and Frontend later fails:

```text
Backend:
SUCCESS

Frontend:
FAILED

Release:
PARTIAL_FAILURE
```

Backend remains `SUCCESS`.

Do not erase its deployment history.

v2.2 does not automatically roll it back.

---

# 45. Promote API Failure Before Drone Build Exists

Example:

```text
PROMOTE_BACKEND
Drone API timeout/error
```

Then:

```text
Backend FAILED
failed_stage = PROMOTE_BACKEND
promotion_build_number = null
```

For a Bundle:

```text
Frontend CANCELLED
Release FAILED
```

---

# 46. Deployment Failure After Drone Promotion Exists

Example:

```text
promotion_build_number = 457
Drone build #457 = failure
```

Persist:

```text
status = FAILED
failed_stage = WAIT_BACKEND_DEPLOYMENT
error_code = DRONE_BUILD_FAILED
```

---

# 47. Frontend UI Main Screen

Recommended:

```text
Release Controller

┌──────────────────────────────────────┐
│ Frontend                             │
│                                      │
│ Selected Build: #123                 │
│ Commit: abc1234                      │
│ Message: Fix validation              │
│                                      │
│ [ Deploy Frontend Only ]             │
└──────────────────────────────────────┘

┌──────────────────────────────────────┐
│ Backend                              │
│                                      │
│ Selected Build: #456                 │
│ Commit: def5678                      │
│ Message: Update API                  │
│                                      │
│ [ Deploy Backend Only ]              │
└──────────────────────────────────────┘

Target:
[ pre-production ▼ ]

[ Release Frontend + Backend ]
```

---

# 48. Build Selection UI

Frontend and Backend lists are independent.

Frontend:

```text
#123 success abc1234 Fix validation
#122 success 987abcd Update UI
#121 failure ...
```

Backend:

```text
#456 success def5678 Update API
#455 success ...
#454 failure ...
```

No shared build number assumption.

---

# 49. Combined Release Confirmation

Before running:

```text
Release Frontend + Backend
```

show confirmation:

```text
Target:
pre-production

Frontend:
Build #123
Commit abc1234

Backend:
Build #456
Commit def5678

Order:
Backend → Frontend

[Cancel] [Release]
```

---

# 50. Release Progress UI

The UI must show the current workflow stage.

Example:

```text
Release R001
Target: pre-production
Overall: DEPLOYING

Backend
Source Build: #456
Promotion Build: #457
Status: SUCCESS

Stages:
✓ Validate Backend
✓ Promote Backend
✓ Wait Backend Deployment

Frontend
Source Build: #123
Promotion Build: #124
Status: DEPLOYING

Stages:
✓ Validate Frontend
✓ Promote Frontend
● Wait Frontend Deployment
```

---

# 51. Failure UI

Example:

```text
Release R002
Overall: FAILED

Backend
Status: FAILED

✓ Validate Backend
✓ Promote Backend
✗ Wait Backend Deployment

Error:
DRONE_BUILD_FAILED
Drone promotion build #457 failed

Frontend
Status: CANCELLED

Reason:
Backend deployment failed
```

The failed stage must be visually obvious.

Do not only display:

```text
Release failed
```

---

# 52. BPMN Visualization

If practical with the existing frontend, render the BPMN definition and overlay execution state.

At minimum show:

```text
completed stage
current stage
failed stage
cancelled stage
```

If full interactive BPMN rendering is too large for the first v2.2 implementation, the backend BPMN/workflow model and stage history are still mandatory, and the UI may initially render a stage timeline.

Do not block the entire v2.2 delivery solely on advanced BPMN canvas rendering.

---

# 53. API: Release List

```http
GET /api/v1/releases
```

Query:

```text
status
target
limit
offset
```

Sort:

```text
created_at DESC
```

---

# 54. API: Release Detail

```http
GET /api/v1/releases/{release_id}
```

Response must expose enough workflow information for UI diagnosis.

Example:

```json
{
  "id": "release-id",
  "mode": "BUNDLE",
  "target": "pre-production",
  "status": "FAILED",
  "current_stage": null,
  "failed_stage": "WAIT_BACKEND_DEPLOYMENT",
  "error_code": "DRONE_BUILD_FAILED",
  "error_message": "Drone promotion build #457 failed",
  "deployments": [
    {
      "component": "backend",
      "source_build_number": 456,
      "promotion_build_number": 457,
      "status": "FAILED",
      "failed_stage": "WAIT_BACKEND_DEPLOYMENT"
    },
    {
      "component": "frontend",
      "source_build_number": 123,
      "promotion_build_number": null,
      "status": "CANCELLED",
      "cancel_reason": "Backend deployment failed"
    }
  ]
}
```

---

# 55. API: Release Workflow Events

Add:

```http
GET /api/v1/releases/{release_id}/events
```

Return ordered audit events.

Example:

```json
{
  "items": [
    {
      "stage": "VALIDATE_BACKEND",
      "event_type": "STAGE_SUCCEEDED",
      "created_at": "..."
    },
    {
      "stage": "WAIT_BACKEND_DEPLOYMENT",
      "event_type": "STAGE_FAILED",
      "error_code": "DRONE_BUILD_FAILED",
      "created_at": "..."
    },
    {
      "stage": "PROMOTE_FRONTEND",
      "event_type": "DEPLOYMENT_CANCELLED",
      "message": "Backend deployment failed",
      "created_at": "..."
    }
  ]
}
```

---

# 56. API: Deployment List

```http
GET /api/v1/deployments
```

Query:

```text
component
status
target
release_bundle_id
limit
offset
```

---

# 57. API: Deployment Detail

```http
GET /api/v1/deployments/{deployment_id}
```

Must include:

```text
current_stage
failed_stage
error_code
error_message
cancel_reason
source_build_number
promotion_build_number
```

---

# 58. Drone Status API

Add:

```http
GET /api/v1/drone/status
```

Drone unavailable:

```json
{
  "status": "unavailable"
}
```

This must not break:

```http
GET /health
```

`/health` checks only:

```text
FastAPI
SQLite
```

Drone availability is an upstream dependency, not container liveness.

---

# 59. Error HTTP Mapping

Recommended:

```text
Build not found
→ 404

Local domain conflict
→ 409

Drone timeout/unavailable
→ 503

Drone authentication / upstream protocol error
→ 502

Invalid request
→ 422 / 400
```

---

# 60. Logging

Log useful workflow context:

```text
release_id
deployment_id
component
stage
repo
source_build_number
promotion_build_number
target
status
error_code
```

Never log:

```text
DRONE_TOKEN
Authorization header
password
secret
```

---

# 61. Alembic Upgrade From v1

The current system is v1.

Create migrations that upgrade **directly from the current v1 Alembic head**.

Do not require:

```text
deploy v2.1 first
```

Required new persistence:

```text
release_bundles
deployments
workflow_events
```

If v1 already has:

```text
releases
```

preserve existing data.

Do not destructively drop v1 data as part of v2.2.

A future migration may archive or transform old v1 records.

---

# 62. Service Layer

Recommended:

```text
app/services/
├── drone_build_service.py
├── deployment_service.py
├── release_service.py
├── release_orchestrator.py
└── workflow_service.py
```

Responsibilities:

## drone_build_service

```text
repo resolution
list builds
get build
promotable validation
```

## deployment_service

```text
create deployment
duplicate checks
promote component
refresh Drone deployment
failure persistence
cancellation persistence
```

## release_service

```text
create/query Bundle
aggregate Bundle status
release detail
history
```

## release_orchestrator

```text
Backend-first orchestration
advance workflow
stop workflow after prerequisite failure
start Frontend after Backend success
PARTIAL_FAILURE handling
```

## workflow_service

```text
load BPMN/workflow definition
track current stage
append events
mark stage success/failure/cancel
workflow instance state
```

Routes should not contain the full workflow.

---

# 63. Suggested Repository Structure

```text
release-controller/
├── app/
│   ├── api/
│   │   └── routes/
│   │       ├── health.py
│   │       ├── drone.py
│   │       ├── deployments.py
│   │       └── releases.py
│   │
│   ├── core/
│   │   └── config.py
│   │
│   ├── db/
│   │   └── models/
│   │       ├── release_bundle.py
│   │       ├── deployment.py
│   │       └── workflow_event.py
│   │
│   ├── integrations/
│   │   └── drone/
│   │       ├── client.py
│   │       ├── schemas.py
│   │       └── exceptions.py
│   │
│   ├── schemas/
│   │   ├── drone.py
│   │   ├── deployment.py
│   │   ├── release.py
│   │   └── workflow.py
│   │
│   ├── services/
│   │   ├── drone_build_service.py
│   │   ├── deployment_service.py
│   │   ├── release_service.py
│   │   ├── release_orchestrator.py
│   │   └── workflow_service.py
│   │
│   ├── workflows/
│   │   └── bpmn/
│   │       ├── frontend_only.bpmn
│   │       ├── backend_only.bpmn
│   │       └── release_bundle.bpmn
│   │
│   └── main.py
│
├── frontend/
├── alembic/
├── tests/
├── Containerfile
├── requirements.txt
├── .env.example
├── RELEASE_CONTROLLER_SPEC.md
└── RELEASE_WORKER_V2_SPEC.md
```

---

# 64. Drone Pipeline Contract

Frontend Repo and Backend Repo each keep their own `.drone.yml`.

Each deploy pipeline should be triggered by Drone promotion.

Concept:

```yaml
trigger:
  event:
    - promote
  target:
    - pre-production
```

Frontend promote deploys only Frontend.

Backend promote deploys only Backend.

Release Controller only orchestrates the two independent promotions.

---

# 65. Automated Tests

All Drone API calls must be mocked.

Tests must not contact:

```text
drone.example.test
```

---

# 66. Required Test: Frontend Only

Verify:

```text
Frontend source build validated
Frontend promote called
Backend promote not called
No Backend deployment record created
```

---

# 67. Required Test: Backend Only

Verify:

```text
Backend source build validated
Backend promote called
Frontend promote not called
No Frontend deployment record created
```

---

# 68. Required Test: Independent Build Numbers

Use:

```text
Frontend #123
Backend #456
```

Combined Bundle must work.

---

# 69. Required Test: Validate Both Before Promote

If Frontend invalid:

```text
409
Backend Promote not called
Frontend Promote not called
```

If Backend invalid:

```text
409
Frontend Promote not called
Backend Promote not called
```

---

# 70. Required Test: Backend First

After Bundle starts:

```text
Backend = PROMOTING or DEPLOYING
Frontend = WAITING
```

Frontend Promote has not been called.

---

# 71. Required Test: Backend Success Starts Frontend

After refresh with Backend Drone success:

```text
Backend = SUCCESS
Frontend Promote called
Frontend = PROMOTING / DEPLOYING
```

---

# 72. Required Test: Backend Failure Cancels Frontend

Backend Drone returns failure.

Expected:

```text
Backend = FAILED
Backend.failed_stage = WAIT_BACKEND_DEPLOYMENT

Frontend = CANCELLED
Frontend.cancel_reason != null

Bundle = FAILED
Bundle.failed_stage = WAIT_BACKEND_DEPLOYMENT
```

Frontend Promote must not be called.

---

# 73. Required Test: Frontend Failure After Backend Success

Expected:

```text
Backend = SUCCESS
Frontend = FAILED
Bundle = PARTIAL_FAILURE
```

---

# 74. Required Test: Both Success

Expected:

```text
Backend = SUCCESS
Frontend = SUCCESS
Bundle = SUCCESS
finished_at != null
```

---

# 75. Required Test: Promote API Failure

If Backend promote API fails before a promotion build exists:

```text
Backend = FAILED
promotion_build_number = null
failed_stage = PROMOTE_BACKEND

Frontend = CANCELLED
Bundle = FAILED
```

---

# 76. Required Test: Timeout

If active deployment exceeds timeout:

```text
status = FAILED
error_code = WORKFLOW_TIMEOUT or DRONE_TIMEOUT
failed_stage = current waiting stage
```

For Backend-first Bundle:

```text
Frontend = CANCELLED
```

---

# 77. Required Test: Workflow Events

Verify ordered events include:

```text
STAGE_STARTED
STAGE_SUCCEEDED
STAGE_FAILED
DEPLOYMENT_CANCELLED
```

when appropriate.

---

# 78. Required Test: Duplicate Promote

Existing deployment:

```text
PROMOTING / DEPLOYING / SUCCESS
```

same:

```text
component + source_build_number + target
```

must return:

```text
409
```

---

# 79. Frontend Tests

At minimum:

```text
Frontend Build selection
Backend Build selection

Deploy Frontend Only
Deploy Backend Only
Release Both

Confirmation dialog

Backend-first progress
FAILED stage display
CANCELLED display
cancel reason display
PARTIAL_FAILURE display

Release history
Deployment history
Workflow event/timeline rendering
```

Do not represent state only through color.

Always include text.

---

# 80. Health Contract

Existing:

```http
GET /health
```

must continue to verify only:

```text
FastAPI
SQLite
```

Drone downtime must not make the container health check fail.

---

# 81. Podman Deployment Compatibility

The existing deployment command must remain valid:

```bash
bash ./scripts/deploy-release-controller.sh ~/services/release-controller-src
```

The script may be updated to include:

```bash
--env-file ~/services/release-controller/config/release-controller.env
```

but must preserve:

```text
Container port = 8000
SQLite = /data/release.db
Health = /health
Container name = release-controller
```

---

# 82. Non-Goals

Do not implement in v2.2:

```text
SMTP relay
Email notification
Gitea webhook
Redis
Celery
Huey
Kafka
Kubernetes
Direct SSH deployment
Automatic rollback
Parallel Bundle deployment
Automatic production approval
Git write operations
Drone secret management
Multi-node workflow worker
```

---

# 83. v2.2 Definition of Done

- [ ] Existing v1 project upgraded directly to v2.2
- [ ] Existing `/health` still works
- [ ] Existing SQLite data is preserved
- [ ] Alembic migration upgrades from current v1 head
- [ ] Drone config comes from environment
- [ ] Drone token never appears in DB/log/API/UI
- [ ] Frontend and Backend are independent Drone repos
- [ ] Frontend and Backend build numbers are independent
- [ ] Frontend builds can be queried
- [ ] Backend builds can be queried
- [ ] Frontend Only deployment works
- [ ] Backend Only deployment works
- [ ] Frontend Only does not create fake Backend cancellation
- [ ] Backend Only does not create fake Frontend cancellation
- [ ] Combined Release accepts different FE/BE build numbers
- [ ] Both source builds are validated before any promotion starts
- [ ] Combined release uses Backend First
- [ ] Backend success starts Frontend
- [ ] Backend failure prevents Frontend Promote
- [ ] Frontend becomes `CANCELLED` after Backend prerequisite failure
- [ ] `cancel_reason` is stored
- [ ] `failed_stage` is stored
- [ ] `error_code` is stored
- [ ] `error_message` is stored safely
- [ ] `workflow_instance_id` is supported
- [ ] workflow events are persisted
- [ ] BPMN/workflow definitions exist
- [ ] UI can show current stage
- [ ] UI can show exact failed stage
- [ ] UI can show cancelled downstream stage/component
- [ ] Backend success + Frontend failure becomes `PARTIAL_FAILURE`
- [ ] Both success becomes `SUCCESS`
- [ ] source build and promotion build are both stored
- [ ] duplicate promote returns 409
- [ ] deployment timeout is handled
- [ ] all Drone tests use mocks
- [ ] pytest passes
- [ ] frontend build passes
- [ ] Podman build passes
- [ ] existing deployment script still works
- [ ] service remains reachable at `http://<INTERNAL_HOST>`

---

# 84. Agent Implementation Instruction

The current project is **v1**.

Do not implement v2.1 first.

Upgrade the existing project directly to **v2.2**.

First read:

```text
RELEASE_CONTROLLER_SPEC.md
RELEASE_WORKER_V2_SPEC.md
```

Then inspect the current source code before modifying it.

Requirements:

1. Preserve working v1 behavior unless explicitly replaced by this spec.
2. Do not rewrite the entire project.
3. Upgrade Alembic directly from the current v1 head.
4. Preserve existing SQLite data.
5. Treat Frontend and Backend as separate Drone repositories.
6. Never assume their build numbers match.
7. Implement Frontend Only.
8. Implement Backend Only.
9. Implement Combined Release Bundle.
10. Validate both Bundle source builds before any Promote call.
11. Use Backend-first orchestration.
12. Add BPMN/workflow state.
13. Add stable workflow stage IDs.
14. Add `CANCELLED`.
15. Add `failed_stage`.
16. Add `error_code`.
17. Add `error_message`.
18. Add `cancel_reason`.
19. Add workflow audit events.
20. Backend prerequisite failure must cancel Frontend in Bundle mode.
21. Frontend-only/Backend-only flows must not create fake cancelled components.
22. Add Release progress APIs.
23. Add workflow event API.
24. Update frontend UI.
25. Show exact failed stage in UI.
26. Show cancellation reason in UI.
27. Keep Drone API mocked in automated tests.
28. Run all pytest tests.
29. Fix every failing test.
30. Run frontend tests/build.
31. Verify Containerfile.
32. Verify Podman build when available.
33. Preserve `/health`.
34. Do not implement Non-Goals.

After implementation, output:

```text
1. Modified file list
2. v1 → v2.2 migration summary
3. Database schema changes
4. BPMN/workflow definitions
5. API changes
6. Frontend UI changes
7. Frontend Only workflow
8. Backend Only workflow
9. Combined Release workflow
10. Failure / cancellation behavior
11. Test results
12. Frontend build result
13. Podman build result
14. Required server environment variables
15. Required deploy-release-controller.sh changes
16. Exact deployment commands
```
