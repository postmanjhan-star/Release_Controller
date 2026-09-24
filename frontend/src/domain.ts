// A component is whatever a project calls it. It was "frontend" | "backend"
// while those were the only two a project could have.
export type ComponentName = string;
export type HistoryView =
  "releases" | "workflows" | "recipients" | "projects" | "connections";
// SINGLE and BUNDLE are the shapes a release has: one component or several.
// FRONTEND_ONLY and BACKEND_ONLY only appear on records made before v3.0.
export type ReleaseMode =
  "SINGLE" | "BUNDLE" | "FRONTEND_ONLY" | "BACKEND_ONLY";
export type BpmnMode = ReleaseMode | "LEGACY" | "SCHEDULED";
export type ConnectionKind = "drone" | "gitea";
export type ConnectionVerifyStatus =
  "ok" | "unauthorized" | "unreachable" | "key_mismatch";
export type PublishStatus =
  "NOT_PUBLISHED" | "PUBLISHING" | "PUBLISHED" | "FAILED" | "PARTIAL_FAILURE";

export interface DroneBuild {
  number: number;
  branch: string;
  commit_sha: string;
  commit_message?: string;
  author?: string;
  status: string;
  promotable: boolean;
}

export interface RepositorySummary {
  slug: string;
  link?: string;
  default_branch?: string;
}

export interface DroneBuildList {
  items: DroneBuild[];
  branches: string[];
  repository: RepositorySummary;
}

export interface ProjectComponent {
  id: string;
  project_id: string;
  key: ComponentName;
  display_name: string;
  position: number;
  drone_owner: string;
  drone_repo: string;
  drone_slug: string;
  promote_target_override?: string | null;
  effective_target: string;
  drone_connection_id?: string | null;
  publish_enabled: boolean;
  gitea_owner?: string | null;
  gitea_repo?: string | null;
  gitea_slug?: string | null;
  gitea_connection_id?: string | null;
  tag_prefix: string;
  is_active: boolean;
}

export interface Project {
  id: string;
  key: string;
  name: string;
  description?: string | null;
  default_target: string;
  drone_connection_id?: string | null;
  gitea_connection_id?: string | null;
  is_archived: boolean;
  components: ProjectComponent[];
}

export interface UpstreamConnection {
  id: string;
  kind: ConnectionKind;
  name: string;
  base_url: string;
  token_hint: string;
  is_default: boolean;
  verify_status?: ConnectionVerifyStatus | null;
  verify_detail?: string | null;
  verified_at?: string | null;
}

export interface ConnectionTestResult {
  status: ConnectionVerifyStatus;
  detail?: string | null;
  checked_at: string;
}

export interface ComponentCheck {
  component_id: string;
  component_key: string;
  status: string;
  drone?: string | null;
  gitea?: string | null;
  detail?: string | null;
}

export interface ProjectValidation {
  status: string;
  checks: ComponentCheck[];
  conflicts: string[];
}

export interface Deployment {
  id: string;
  project_id?: string | null;
  component_id?: string | null;
  component: ComponentName;
  status: string;
  publish_status: PublishStatus;
  version?: string | null;
  gitea_release_id?: string | null;
  gitea_release_tag?: string | null;
  gitea_release_url?: string | null;
  publish_error_code?: string | null;
  publish_error_message?: string | null;
  published_at?: string | null;
  target: string;
  source_build_number: number;
  promotion_build_number?: number | null;
  commit_sha?: string | null;
  current_stage?: string | null;
  failed_stage?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  cancel_reason?: string | null;
  attachment_filename?: string | null;
  attachment_content_type?: string | null;
  attachment_size?: number | null;
  created_at?: string;
  updated_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface ReleaseBundle {
  id: string;
  project_id?: string | null;
  selected_component_keys?: string | null;
  mode: ReleaseMode;
  status: string;
  deployment_status: string;
  publish_status: PublishStatus;
  version?: string | null;
  release_name?: string | null;
  release_notes?: string | null;
  publish_error_code?: string | null;
  publish_error_message?: string | null;
  published_at?: string | null;
  publish_records: PublishRecord[];
  target: string;
  deployments: Deployment[];
  workflow_instance_id?: string | null;
  current_stage?: string | null;
  failed_stage?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  attachment_filename?: string | null;
  attachment_content_type?: string | null;
  attachment_size?: number | null;
  created_at?: string;
  updated_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface PublishRecord {
  id: string;
  deployment_id: string;
  project_id?: string | null;
  component_id?: string | null;
  component: ComponentName;
  version: string;
  repo_owner: string;
  repo_name: string;
  commit_sha: string;
  tag_name: string;
  status: "PUBLISHING" | "PUBLISHED" | "FAILED";
  gitea_release_url?: string | null;
  error_code?: string | null;
  error_message?: string | null;
}

export interface ScheduleComponentBuild {
  component_id: string;
  component_key: ComponentName;
  build_number: number;
}

export interface DeploymentSchedule {
  id: string;
  project_id?: string | null;
  mode: ReleaseMode;
  status: string;
  target: string;
  selected_component_keys?: string | null;
  component_builds: ScheduleComponentBuild[];
  // Written only for the two components that predate v3.0; component_builds is
  // the list to read.
  frontend_build_number?: number | null;
  backend_build_number?: number | null;
  scheduled_for_utc: string;
  timezone: string;
  notification_recipients: string[];
  notification_status?: string | null;
  notification_sent_at?: string | null;
  release_version?: string | null;
  release_notes?: string | null;
  attachment_filename?: string | null;
  attachment_content_type?: string | null;
  attachment_size?: number | null;
  requested_by?: string | null;
  release_bundle_id?: string | null;
  deployment_id?: string | null;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface WorkflowEvent {
  id: string;
  event_type: string;
  stage: string;
  component?: ComponentName | null;
  deployment_id?: string | null;
  message?: string | null;
  created_at: string;
}

export interface LegacyWorkflowState {
  release_id: string;
  definition_id: string;
  is_complete: boolean;
  current_element_ids: string[];
  completed_element_ids: string[];
  steps: Array<{
    element_id: string;
    name: string;
    state: string;
  }>;
  updated_at: string;
}

export interface ApiList<T> {
  items: T[];
}
