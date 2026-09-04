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
# setpriv rather than runuser or su: it execs the command instead of forking a
# supervised child, so the candidate server stays a direct child of `timeout`
# and the kill that stops it still reaches uvicorn.
setpriv_bin="${AUTHIFI_DOCS_SETPRIV_BIN:-setpriv}"
service_user="${AUTHIFI_DOCS_SERVICE_USER:-authifi-docs}"
candidate_port="${AUTHIFI_DOCS_CANDIDATE_PORT:-18080}"
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

candidate_pid=""

stop_candidate_server() {
  if [[ -n "$candidate_pid" ]]; then
    kill "$candidate_pid" 2>/dev/null || true
    wait "$candidate_pid" 2>/dev/null || true
    candidate_pid=""
  fi
}

# One exit handler for the whole run, installed before anything is staged or
# started, and only inside the branch that holds the lock: the outer invocation
# must not clear staging for a deployment it just refused to interleave with.
#
# `incoming/<sha>` is this deployment's staging directory and nothing else's,
# so clearing it on every path — a rejected checksum, an unhealthy candidate, a
# SHA that was already active, a signal — never touches another deployment's
# data. Only the success path used to clear it, which left a failed deploy's
# archive on the root volume until somebody noticed, and there is nothing in
# there worth keeping: the same bytes are in S3 under the same SHA.
on_exit() {
  local status=$?

  stop_candidate_server
  rm -rf "$incoming"

  return "$status"
}

trap on_exit EXIT

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

# A leftover uvicorn from an interrupted deploy still holding the candidate port
# would answer the health check below, and the release that passed would be the
# old one — a candidate promoted without ever having been probed. Asking for the
# port the way uvicorn asks for it is the check: SO_REUSEADDR set, so a
# TIME_WAIT connection is not mistaken for a listener, and no listener is
# mistaken for a free port.
"$python_bin" - "$candidate_port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError as error:
        raise SystemExit(f"candidate port {port} is already in use: {error}")
PY

# As the service account, not as root. The candidate probe exists to answer
# "will the release systemd is about to start actually serve?", and root can
# read a site the service user cannot — which is exactly the failure this step
# is supposed to catch before the swap rather than after it.
SITE_DIR="$candidate/site" "$timeout_bin" 30 \
  "$setpriv_bin" \
  --reuid="$service_user" \
  --regid="$service_user" \
  --init-groups \
  --no-new-privs \
  -- \
  "$uvicorn_bin" server.main:app \
  --app-dir "$candidate" \
  --host 127.0.0.1 \
  --port "$candidate_port" &
candidate_pid=$!

if ! poll_health "http://127.0.0.1:$candidate_port/health" "$candidate_attempts"; then
  echo "candidate release failed health check" >&2
  rm -rf "$candidate"
  exit 1
fi

stop_candidate_server

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

# Best effort, and deliberately the only step here that is. Everything above
# either succeeded or exited non-zero already, so reaching this line means the
# release is swapped in, restarted, and answering its health check. Pruning old
# releases is housekeeping on top of that: an EPERM on one stale directory, an
# EBUSY from something still holding a file open, an ENOSPC part-way through an
# rmtree. As the script's last statement under `set -e`, any of those became
# the exit status, which Systems Manager reports as a failed command and the
# workflow treats as a failed deployment -- telling an operator to roll back a
# release that is live and healthy because a directory nobody will read again
# could not be deleted.
#
# The failure is still reported, on stderr, where the SSM invocation output
# picks it up: a host that stops pruning will fill its root volume eventually,
# so this is worth seeing, just not worth failing for.
if ! prune_releases "$candidate"; then
  echo "release pruning failed; deployment is active" >&2
fi
