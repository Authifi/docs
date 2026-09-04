#!/usr/bin/env bash
set -euo pipefail

sha="${1:?usage: deploy-release.sh SHA}"
if [[ ! "$sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "SHA must be 40 lowercase hexadecimal characters" >&2
  exit 2
fi

root="${AUTHIFI_DOCS_ROOT:-/opt/authifi-docs}"
etc_dir="${AUTHIFI_DOCS_ETC:-/etc/authifi-docs}"
lock="${AUTHIFI_DOCS_LOCK:-/run/lock/authifi-docs-deploy.lock}"
python_bin="${AUTHIFI_DOCS_PYTHON_BIN:-python3}"
uvicorn_bin="${AUTHIFI_DOCS_UVICORN_BIN:-}"
curl_bin="${AUTHIFI_DOCS_CURL_BIN:-curl}"
systemctl_bin="${AUTHIFI_DOCS_SYSTEMCTL_BIN:-systemctl}"
timeout_bin="${AUTHIFI_DOCS_TIMEOUT_BIN:-timeout}"
candidate_attempts="${AUTHIFI_DOCS_CANDIDATE_HEALTH_ATTEMPTS:-30}"
active_attempts="${AUTHIFI_DOCS_ACTIVE_HEALTH_ATTEMPTS:-15}"
health_sleep_seconds="${AUTHIFI_DOCS_HEALTH_SLEEP_SECONDS:-1}"
curl_connect_timeout_seconds="${AUTHIFI_DOCS_CURL_CONNECT_TIMEOUT_SECONDS:-2}"
curl_max_time_seconds="${AUTHIFI_DOCS_CURL_MAX_TIME_SECONDS:-5}"

releases="$root/releases"
current="$root/current"
incoming_root="$root/incoming"
incoming="$incoming_root/$sha"
archive="$incoming/$sha.tar.gz"
checksum_file="$archive.sha256"
candidate="$releases/$sha"

# This runs as root under the Systems Manager agent, whose umask is not this
# script's to assume. Everything below — the release directory, the extracted
# tree, the virtualenv pip populates — inherits it, and a single
# group-writable file inside a release is enough for the service account to
# replace the code systemd loads on the next restart.
umask 022

mkdir -p "$releases" "$incoming" "$(dirname "$lock")"
chmod 0755 "$releases"
# Staged archives come straight off the network and only root reads them.
chmod 0700 "$incoming_root" "$incoming"

if [[ "${AUTHIFI_DOCS_LOCK_HELD:-0}" != "1" ]]; then
  status=0
  "$python_bin" - "$lock" "$BASH" "$0" "$sha" <<'PY' || status=$?
import fcntl
import os
import subprocess
import sys

lock_path, *command = sys.argv[1:]
env = os.environ.copy()
env["AUTHIFI_DOCS_LOCK_HELD"] = "1"

with open(lock_path, "w", encoding="utf-8") as lock_file:
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(75)

    completed = subprocess.run(command, env=env)
    sys.exit(completed.returncode)
PY

  if [[ "$status" -ne 0 ]]; then
    if [[ "$status" -eq 75 ]]; then
      echo "deployment already running" >&2
    fi
    exit "$status"
  fi
  exit 0
fi

if [[ ! -s "$archive" || ! -s "$checksum_file" ]]; then
  echo "SSM did not stage the release archive and checksum" >&2
  exit 1
fi

"$python_bin" - "$archive" "$checksum_file" <<'PY'
from pathlib import Path
import hashlib
import sys

archive = Path(sys.argv[1])
checksum_path = Path(sys.argv[2])
parts = checksum_path.read_text(encoding="utf-8").split()
if len(parts) != 2:
    raise SystemExit("checksum file must contain '<sha256>  <filename>'")

expected, filename = parts
if filename != archive.name:
    raise SystemExit(f"checksum filename {filename!r} does not match {archive.name!r}")

actual = hashlib.sha256(archive.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit(f"checksum mismatch for {archive.name}")
PY

poll_health() {
  local url="$1"
  local attempts="$2"
  local attempt

  for ((attempt = 1; attempt <= attempts; attempt += 1)); do
    if "$curl_bin" \
      --fail \
      --silent \
      --connect-timeout "$curl_connect_timeout_seconds" \
      --max-time "$curl_max_time_seconds" \
      "$url" >/dev/null; then
      return 0
    fi
    if (( attempt < attempts )); then
      sleep "$health_sleep_seconds"
    fi
  done
  return 1
}

swap_current() {
  local target="$1"
  local next="$current.next"

  rm -f "$next"
  ln -s "$target" "$next"
  "$python_bin" - "$next" "$current" <<'PY'
import os
import sys

os.replace(sys.argv[1], sys.argv[2])
PY
}

prune_releases() {
  "$python_bin" - "$releases" "$1" <<'PY'
import os
import re
from pathlib import Path
import shutil
import sys

releases = Path(sys.argv[1])
active_target = Path(sys.argv[2]).resolve()
release_name = re.compile(r"^[0-9a-f]{40}$")

entries = [
    path
    for path in releases.iterdir()
    if path.is_dir()
    and not path.is_symlink()
    and release_name.fullmatch(path.name)
    and path.resolve() != active_target
]
for stale in sorted(entries, key=lambda path: path.stat().st_mtime, reverse=True)[2:]:
    shutil.rmtree(stale)
PY
}

previous=""
if [[ -L "$current" ]]; then
  previous="$(readlink "$current")"
fi

if [[ "$previous" == "$candidate" ]]; then
  echo "release $sha is already active" >&2
  exit 0
fi

rm -rf "$candidate"
mkdir -p "$candidate"
chmod 0755 "$candidate"
tar -xzf "$archive" -C "$candidate"

"$python_bin" -m venv "$candidate/.venv"
"$candidate/.venv/bin/pip" install --no-index \
  --find-links "$candidate/wheelhouse" \
  -r "$candidate/requirements.txt"

set -a
# shellcheck disable=SC1090,SC1091
source "$etc_dir/environment"
# shellcheck disable=SC1090,SC1091
source "$etc_dir/session.env"
set +a

if [[ -z "$uvicorn_bin" ]]; then
  uvicorn_bin="$candidate/.venv/bin/uvicorn"
fi

SITE_DIR="$candidate/site" "$timeout_bin" 30 \
  "$uvicorn_bin" server.main:app \
  --app-dir "$candidate" \
  --host 127.0.0.1 \
  --port 18080 &
candidate_pid=$!

cleanup_candidate_server() {
  kill "$candidate_pid" 2>/dev/null || true
  wait "$candidate_pid" 2>/dev/null || true
}

trap cleanup_candidate_server EXIT

if ! poll_health "http://127.0.0.1:18080/health" "$candidate_attempts"; then
  echo "candidate release failed health check" >&2
  rm -rf "$candidate"
  exit 1
fi

cleanup_candidate_server
trap - EXIT

# Everything past the swap fails the same way and has to be undone the same
# way. `systemctl restart` returning non-zero used to end the installer on the
# spot under `set -e`, with `current` already pointing at the candidate and the
# previous release intact beside it: nothing rolled back, nothing restarted,
# and the host left on a release that had never started — the one state the
# two-stage swap exists to prevent.
abandon_activation() {
  local reason="$1"

  if [[ -n "$previous" ]]; then
    swap_current "$previous"
    if "$systemctl_bin" restart authifi-docs; then
      echo "$reason; previous release restored" >&2
    else
      # The symlink is back either way, so a reboot or a later deploy comes up
      # on the previous release rather than on the one that just failed.
      echo "$reason; previous release symlink restored but its service did not restart" >&2
    fi
  else
    rm -f "$current"
    # Best effort: there is nothing to fall back to, and the useful outcome is
    # an unhealthy target rather than one serving a release that will not run.
    "$systemctl_bin" stop authifi-docs || true
    echo "$reason; no previous release to restore" >&2
  fi

  exit 1
}

swap_current "$candidate"

if ! "$systemctl_bin" restart authifi-docs; then
  abandon_activation "candidate release did not restart under systemd"
fi

if ! poll_health "http://127.0.0.1:8080/health" "$active_attempts"; then
  abandon_activation "active release failed health check"
fi

prune_releases "$candidate"
rm -rf "$incoming"
