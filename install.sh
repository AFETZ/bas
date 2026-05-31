#!/usr/bin/env bash
# BAS one-line installer.
#
# Quick start:
#   bash <(curl -fsSL https://raw.githubusercontent.com/AFETZ/bas/main/install.sh)
#
# It clones or updates https://github.com/AFETZ/bas.git, then runs
# scripts/bootstrap.sh from the checked-out repository.
set -euo pipefail

REPO_URL="${BAS_REPO_URL:-https://github.com/AFETZ/bas.git}"
BRANCH="${BAS_BRANCH:-main}"
RUN_USER="${SUDO_USER:-${USER:-$(id -un)}}"
RUN_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6 2>/dev/null || printf '%s' "${HOME:-/tmp}")"
INSTALL_DIR="${BAS_INSTALL_DIR:-${RUN_HOME}/bas}"
RUN_BOOTSTRAP=1
BOOTSTRAP_ARGS=()

usage() {
    cat <<'EOF'
BAS one-line installer

Usage:
  bash <(curl -fsSL https://raw.githubusercontent.com/AFETZ/bas/main/install.sh) [options]

Options:
  --full              Pass through to scripts/bootstrap.sh: install Sionna RT too
  --no-docker         Pass through to scripts/bootstrap.sh: skip Docker setup/build
  --no-gpu            Pass through to scripts/bootstrap.sh: skip WSL2 Vulkan/Dozen setup
  --skip-bootstrap    Only clone/update the repository
  --dir PATH          Install directory (default: ~/bas)
  --branch NAME       Git branch/tag to install (default: main)
  --repo URL          Git repository URL (default: https://github.com/AFETZ/bas.git)
  -h, --help          Show this help

Environment overrides:
  BAS_INSTALL_DIR=/opt/bas
  BAS_BRANCH=main
  BAS_REPO_URL=https://github.com/AFETZ/bas.git
EOF
}

log() {
    printf '[bas-install] %s\n' "$*"
}

die() {
    printf '[bas-install] ERROR: %s\n' "$*" >&2
    exit 1
}

sudo_cmd=()
ensure_sudo() {
    if [ "$EUID" -eq 0 ]; then
        sudo_cmd=()
        return
    fi
    command -v sudo >/dev/null 2>&1 || die "sudo is required for system package/bootstrap steps"
    sudo -v
    sudo_cmd=(sudo)
}

run_as_user() {
    if [ "$EUID" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && command -v sudo >/dev/null 2>&1; then
        sudo -H -u "$RUN_USER" "$@"
    else
        "$@"
    fi
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --full|--no-docker|--no-gpu)
            BOOTSTRAP_ARGS+=("$1")
            shift
            ;;
        --skip-bootstrap)
            RUN_BOOTSTRAP=0
            shift
            ;;
        --dir)
            [ "$#" -ge 2 ] || die "--dir requires a path"
            INSTALL_DIR="$2"
            shift 2
            ;;
        --dir=*)
            INSTALL_DIR="${1#--dir=}"
            shift
            ;;
        --branch)
            [ "$#" -ge 2 ] || die "--branch requires a name"
            BRANCH="$2"
            shift 2
            ;;
        --branch=*)
            BRANCH="${1#--branch=}"
            shift
            ;;
        --repo)
            [ "$#" -ge 2 ] || die "--repo requires a URL"
            REPO_URL="$2"
            shift 2
            ;;
        --repo=*)
            REPO_URL="${1#--repo=}"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown argument: $1"
            ;;
    esac
done

log "repo=${REPO_URL}"
log "branch=${BRANCH}"
log "dir=${INSTALL_DIR}"

if ! command -v git >/dev/null 2>&1; then
    ensure_sudo
    if command -v apt-get >/dev/null 2>&1; then
        log "installing git via apt"
        "${sudo_cmd[@]}" apt-get update -q
        "${sudo_cmd[@]}" apt-get install -y --no-install-recommends git ca-certificates curl
    else
        die "git is not installed and apt-get is not available"
    fi
fi

install_parent="$(dirname "$INSTALL_DIR")"
if [ ! -d "$install_parent" ]; then
    if [ "$EUID" -eq 0 ]; then
        mkdir -p "$install_parent"
        chown "$RUN_USER" "$install_parent" 2>/dev/null || true
    else
        mkdir -p "$install_parent"
    fi
fi

if [ -d "${INSTALL_DIR}/.git" ]; then
    log "existing checkout found; updating with git pull --ff-only"
    origin_url="$(run_as_user git -C "$INSTALL_DIR" remote get-url origin 2>/dev/null || true)"
    if [ -n "$origin_url" ] && [ "$origin_url" != "$REPO_URL" ]; then
        log "existing origin is ${origin_url}; requested repo is ${REPO_URL}"
    fi
    if [ -z "$(run_as_user git -C "$INSTALL_DIR" status --porcelain)" ]; then
        run_as_user git -C "$INSTALL_DIR" fetch origin "$BRANCH"
        if run_as_user git -C "$INSTALL_DIR" rev-parse --verify "$BRANCH" >/dev/null 2>&1; then
            run_as_user git -C "$INSTALL_DIR" checkout "$BRANCH"
        else
            run_as_user git -C "$INSTALL_DIR" checkout -b "$BRANCH" "origin/$BRANCH"
        fi
        run_as_user git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
    else
        log "local changes detected; keeping checkout as-is"
    fi
elif [ -e "$INSTALL_DIR" ]; then
    die "${INSTALL_DIR} exists but is not a git checkout"
else
    log "cloning repository"
    run_as_user git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

bootstrap="${INSTALL_DIR}/scripts/bootstrap.sh"
[ -f "$bootstrap" ] || die "bootstrap script not found at ${bootstrap}"

if [ "$RUN_BOOTSTRAP" -eq 1 ]; then
    ensure_sudo
    log "running bootstrap"
    "${sudo_cmd[@]}" bash "$bootstrap" "${BOOTSTRAP_ARGS[@]}"
else
    log "bootstrap skipped"
fi

cat <<EOF

[bas-install] DONE

Next commands:
  cd "${INSTALL_DIR}"
  bash scripts/demo.sh --list
  bash scripts/demo.sh

Docs:
  ${INSTALL_DIR}/README.md
  ${INSTALL_DIR}/docs/QUICKSTART.md
EOF
