#!/usr/bin/env bash
set -Eeuo pipefail

# Build and deploy release-controller from a checked-out repository on the Gitea VM.
#
# Usage:
#   bash ./scripts/deploy-release-controller.sh /path/to/release-controller-repo
#
# Settings can be overridden by the deployment runner environment. HOST_IP
# must be set there or in the runtime env file; no production address is built in.

CONTAINER_NAME="${CONTAINER_NAME:-release-controller}"
IMAGE_NAME="${IMAGE_NAME:-localhost/release-controller:latest}"
ROLLBACK_IMAGE="${ROLLBACK_IMAGE:-localhost/release-controller:rollback}"
NETWORK_NAME="${NETWORK_NAME:-release-net}"

HOST_IP="${HOST_IP:-}"
HOST_PORT="${HOST_PORT:-3100}"
CONTAINER_PORT="${CONTAINER_PORT:-8000}"
HEALTH_PATH="${HEALTH_PATH:-/health}"

SERVICE_ROOT="${RELEASE_CONTROLLER_SERVICE_ROOT:-$HOME/services/release-controller}"
DATA_DIR="${DATA_DIR:-$SERVICE_ROOT/data}"
ENV_FILE="${ENV_FILE:-${RELEASE_CONTROLLER_ENV_FILE:-$SERVICE_ROOT/config/release-controller.env}}"
APP_DIR="${APP_DIR:-${1:-$PWD}}"

log() {
  printf '\n\033[1;32m[INFO]\033[0m %s\n' "$*"
}

warn() {
  printf '\n\033[1;33m[WARN]\033[0m %s\n' "$*"
}

die() {
  printf '\n\033[1;31m[ERROR]\033[0m %s\n' "$*" >&2
  exit 1
}

show_logs() {
  if podman container exists "$CONTAINER_NAME" 2>/dev/null; then
    printf '\n===== %s logs =====\n' "$CONTAINER_NAME"
    podman logs --tail 100 "$CONTAINER_NAME" || true
    printf '=====================\n'
  fi
}

run_container() {
  local image="$1"

  podman run -d \
    --name "$CONTAINER_NAME" \
    --network "$NETWORK_NAME" \
    -p "$HOST_IP:$HOST_PORT:$CONTAINER_PORT" \
    -v "$DATA_DIR:/data:Z" \
    --env-file "$ENV_FILE" \
    -e DATABASE_URL=sqlite:////data/release.db \
    --restart=unless-stopped \
    "$image"
}

wait_for_health() {
  local retries="${1:-20}"
  local url="http://$HOST_IP:$HOST_PORT$HEALTH_PATH"

  if ! command -v curl >/dev/null 2>&1; then
    warn "curl is unavailable; checking only whether the container remains running."
    sleep 3
    [[ "$(podman inspect -f '{{.State.Status}}' "$CONTAINER_NAME" 2>/dev/null || true)" == "running" ]]
    return
  fi

  log "Waiting for health check: $url"

  for ((attempt = 1; attempt <= retries; attempt++)); do
    if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
      return 0
    fi

    if [[ "$(podman inspect -f '{{.State.Status}}' "$CONTAINER_NAME" 2>/dev/null || true)" != "running" ]]; then
      return 1
    fi

    printf '.'
    sleep 2
  done

  printf '\n'
  return 1
}

rollback() {
  warn "The new deployment failed; attempting rollback."

  if podman container exists "$CONTAINER_NAME" 2>/dev/null; then
    podman rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
  fi

  if ! podman image exists "$ROLLBACK_IMAGE" 2>/dev/null; then
    die "No rollback image is available; manual recovery is required."
  fi

  log "Starting rollback image: $ROLLBACK_IMAGE"
  if ! run_container "$ROLLBACK_IMAGE" >/dev/null; then
    die "The rollback container could not be started; manual recovery is required."
  fi

  if wait_for_health 15; then
    log "Rollback succeeded."
    exit 1
  fi

  show_logs
  die "Rollback health check also failed; manual recovery is required."
}

command -v podman >/dev/null 2>&1 || die "Podman is required."

APP_DIR="$(cd "$APP_DIR" 2>/dev/null && pwd)" || die "APP_DIR does not exist: $APP_DIR"

[[ -f "$ENV_FILE" ]] || die "Runtime env file was not found: $ENV_FILE"
[[ -r "$ENV_FILE" ]] || die "Runtime env file is not readable: $ENV_FILE"
chmod 600 "$ENV_FILE"

# Read only the literal bind address, never execute/source the secrets file.
# Match Podman's KEY=value format and accept Windows CRLF line endings.
if [[ -z "$HOST_IP" ]]; then
  HOST_IP="$(awk '/^HOST_IP=/ {value=substr($0, 9); sub(/\r$/, "", value)} END {print value}' "$ENV_FILE")"
fi
[[ -n "$HOST_IP" ]] || die "Set HOST_IP in the runtime env file or deployment environment."

log "Podman: $(podman --version)"
log "Source repository: $APP_DIR"
log "Container: $CONTAINER_NAME"
log "Image: $IMAGE_NAME"
log "Network: $NETWORK_NAME"
log "Port: $HOST_IP:$HOST_PORT -> $CONTAINER_PORT"
log "SQLite data: $DATA_DIR"
log "Runtime env: $ENV_FILE"

# Which repositories a release promotes is a project_components row from v3.0
# onward, so the DRONE_*_REPO_* / GITEA_*_REPO_* variables are no longer required
# here: migration 20260902_0014 reads them once if they are present, and leaves
# the registry empty if they are not.  APP_SECRET_KEY is required instead --
# without it the application refuses to start in production, so failing here is
# better than starting a container that exits.
required_env_vars=(
  DRONE_SERVER
  DRONE_TOKEN
  GITEA_SERVER
  GITEA_TOKEN
  APP_SECRET_KEY
  AUTH_ENABLED
  GITEA_OAUTH_CLIENT_ID
  GITEA_OAUTH_CLIENT_SECRET
  GITEA_OAUTH_CALLBACK_URL
  AUTH_COOKIE_SECURE
)

for variable in "${required_env_vars[@]}"; do
  grep -Eq "^[[:space:]]*${variable}=.+" "$ENV_FILE" \
    || die "Runtime env variable is missing or empty: $variable"
done

if ! podman network exists "$NETWORK_NAME"; then
  log "Creating Podman network: $NETWORK_NAME"
  podman network create "$NETWORK_NAME" >/dev/null
fi

if [[ -f "$APP_DIR/Containerfile" ]]; then
  BUILD_FILE="$APP_DIR/Containerfile"
elif [[ -f "$APP_DIR/Dockerfile" ]]; then
  BUILD_FILE="$APP_DIR/Dockerfile"
else
  die "Containerfile or Dockerfile was not found in: $APP_DIR"
fi

mkdir -p "$DATA_DIR"

if podman image exists "$IMAGE_NAME" 2>/dev/null; then
  log "Saving current image for rollback: $ROLLBACK_IMAGE"
  podman tag "$IMAGE_NAME" "$ROLLBACK_IMAGE"
else
  warn "This is the first deployment; no rollback image is available."
fi

# Build before removing the current container so the service stays online during the build.
log "Building the new image."
podman build \
  -t "$IMAGE_NAME" \
  -f "$BUILD_FILE" \
  "$APP_DIR"

if podman container exists "$CONTAINER_NAME" 2>/dev/null; then
  log "Removing old container: $CONTAINER_NAME"
  podman rm -f "$CONTAINER_NAME"
fi

log "Starting the new container."
if ! run_container "$IMAGE_NAME"; then
  rollback
fi

if wait_for_health 20; then
  printf '\n'
  log "Deployment succeeded."
  podman ps --filter "name=^${CONTAINER_NAME}$"

  cat <<EOF

Health:
  http://$HOST_IP:$HOST_PORT$HEALTH_PATH

SQLite:
  Host:      $DATA_DIR/release.db
  Container: /data/release.db

Network:
  $NETWORK_NAME

EOF
else
  printf '\n'
  show_logs
  rollback
fi
