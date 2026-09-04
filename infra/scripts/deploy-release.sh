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
# Absolute, because `env` applies the assignments it is given before resolving
# the command that follows them: a bare `timeout` would be looked up under a
# `PATH` this script did not choose. The configuration parser refuses a `PATH`
# key outright, and these are what make that refusal unnecessary rather than
# load-bearing -- two independent reasons the host configuration cannot decide
# what root executes.
python_bin="${AUTHIFI_DOCS_PYTHON_BIN:-/usr/bin/python3}"
uvicorn_bin="${AUTHIFI_DOCS_UVICORN_BIN:-}"
curl_bin="${AUTHIFI_DOCS_CURL_BIN:-/usr/bin/curl}"
systemctl_bin="${AUTHIFI_DOCS_SYSTEMCTL_BIN:-/usr/bin/systemctl}"
timeout_bin="${AUTHIFI_DOCS_TIMEOUT_BIN:-/usr/bin/timeout}"
# setpriv rather than runuser or su: it execs the command instead of forking a
# supervised child, so the candidate server stays a direct child of `timeout`
# and the kill that stops it still reaches uvicorn.
setpriv_bin="${AUTHIFI_DOCS_SETPRIV_BIN:-/usr/bin/setpriv}"
env_bin="${AUTHIFI_DOCS_ENV_BIN:-/usr/bin/env}"
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
  # `exec`, so the process Systems Manager started *is* the lock holder rather
  # than a shell waiting on one. Bash defers a signal until the foreground
  # child it is waiting on finishes, so an intervening shell here meant a
  # cancelled command killed the wrapper and left the deployment running to
  # completion, cleaning up nothing.
  exec "$python_bin" - "$lock" "$BASH" "$0" "$sha" <<'PY'
import fcntl
import os
import signal
import subprocess
import sys

lock_path, *command = sys.argv[1:]
env = os.environ.copy()
env["AUTHIFI_DOCS_LOCK_HELD"] = "1"

with open(lock_path, "w", encoding="utf-8") as lock_file:
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("deployment already running", file=sys.stderr)
        sys.exit(75)

    child = subprocess.Popen(command, env=env)

    # Forwarded rather than handled: the shell doing the work is the one with
    # the cleanup handler, and it is the one that has to decide what a
    # cancelled deployment leaves behind.
    for number in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda received, _frame: child.send_signal(received))

    status = child.wait()

    # A shell killed by a signal reports 128 + the number, and that is what
    # Systems Manager and the workflow have to see: a plain 1 reads as a
    # deployment that failed on its own, and a 0 reads as one that worked.
    sys.exit(128 - status if status < 0 else status)
PY
fi

candidate_pid=""
# Whether this candidate became the release the host is serving. Set once, at
# the point everything that could still roll it back has succeeded.
candidate_activated=0
# Whether `current` already points at the candidate. Distinct from activation:
# a cancellation after the swap but before the active health check must roll
# back even though the host is not yet serving the release.
candidate_swapped=0
release_restored=0

stop_candidate_server() {
  if [[ -n "$candidate_pid" ]]; then
    kill "$candidate_pid" 2>/dev/null || true
    wait "$candidate_pid" 2>/dev/null || true
    candidate_pid=""
  fi
}

# `releases/<sha>` is created before anything is extracted into it, so every
# failure from there to activation used to leave a release tree behind:
# unpacked, virtualenv and all. Repeated failures on the same commit
# accumulated them, and `prune_releases` then counted them as recent releases
# -- it keeps the two most recently modified non-active directories whose names
# look like a SHA, and three failed attempts are three such directories, all
# newer than the release a rollback needed. The next successful deployment
# pruned the good one and kept the wreckage.
#
# Only this run's candidate, and only while it is not what the host is serving.
# That one condition covers every path: a SHA that was already active takes the
# early exit with a zero status and keeps its tree, an activated release is
# what `current` points at, and a rolled-back one is not -- `abandon_activation`
# restores the previous release before this runs.
discard_candidate() {
  local active=""

  # `readlink -f`, not `readlink`. `swap_current` writes an absolute target,
  # but `current` is a file on a host that outlives any one version of this
  # script -- an older deploy, a manual repair, or a restore can leave
  # `current -> releases/<sha>`. Compared as written, a relative target never
  # equals the absolute candidate path, so this guard would have deleted the
  # very tree the host was serving.
  if [[ -e "$current" ]]; then
    active="$(readlink -f "$current" 2>/dev/null || true)"
  fi
  if [[ -n "$active" && "$active" == "$(readlink -f "$candidate" 2>/dev/null || echo "$candidate")" ]]; then
    return 0
  fi

  rm -rf "$candidate"
}

current_points_at_candidate() {
  local active=""

  if [[ ! -e "$current" ]]; then
    return 1
  fi
  active="$(readlink -f "$current" 2>/dev/null || true)"
  [[ -n "$active" && "$active" == "$(readlink -f "$candidate" 2>/dev/null || echo "$candidate")" ]]
}

cleanup_done=0

# `incoming/<sha>` is this deployment's staging directory and nothing else's,
# so clearing it on every path — a rejected checksum, an unhealthy candidate, a
# SHA that was already active, a cancelled command — never touches another
# deployment's data. Only the success path used to clear it, which left a
# failed deploy's archive on the root volume until somebody noticed, and there
# is nothing in there worth keeping: the same bytes are in S3 under the same
# SHA.
#
# Guarded, because four traps share one handler and a signal that arrives
# during an exit would otherwise run it twice -- the second pass after
# `abandon_activation` may already have restored `current`, so its decision
# would depend on which pass got there first.
run_cleanup() {
  local failed="$1"

  if (( cleanup_done == 1 )); then
    return 0
  fi
  cleanup_done=1

  stop_candidate_server
  if current_points_at_candidate && (( candidate_swapped == 1 )) && (( candidate_activated == 0 )) && (( release_restored == 0 )); then
    restore_previous_release "deployment interrupted"
  fi
  rm -rf "$incoming"
  if (( failed != 0 )) && (( candidate_activated == 0 )); then
    discard_candidate
  fi
}

# Installed before anything is staged or started, and only inside the branch
# that holds the lock: the outer invocation must not clear staging for a
# deployment it just refused to interleave with.
on_exit() {
  local status=$?

  trap - EXIT HUP INT TERM
  run_cleanup "$status"

  return "$status"
}

# Systems Manager cancels a command by signalling the installer, and a
# cancelled workflow run is the ordinary way that happens. `trap ... EXIT`
# alone does not cover it: bash runs the EXIT trap when the shell exits, and a
# default-disposition SIGTERM kills it without one, so a cancelled deploy left
# both the staged archive and a half-built release tree behind.
#
# The signal is re-raised rather than turned into `exit 1`, so the status stays
# what a process killed by it reports. Turning it into an ordinary failure
# would tell Systems Manager the deployment failed on its own, which is a
# different thing for an operator to read.
on_signal() {
  local name="$1"

  trap - EXIT HUP INT TERM
  run_cleanup 1
  echo "deployment interrupted by SIG$name" >&2
  kill -s "$name" $$
}

trap on_exit EXIT
trap 'on_signal HUP' HUP
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM

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

# The rollback target, as an absolute path. Resolved rather than read verbatim
# for the same reason `discard_candidate` resolves: a relative `current` from
# an older deploy or a manual repair never compares equal to the absolute
# candidate path, so a redeploy of the release already running would have
# missed the early exit below and gone on to `rm -rf` the live tree.
previous=""
if [[ -e "$current" ]]; then
  previous="$(readlink -f "$current" 2>/dev/null || true)"
fi

if [[ -n "$previous" && "$previous" == "$candidate" ]]; then
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
# `python -m venv` copies its activation templates with their packaged modes,
# so a writable template bypasses this script's umask. Normalise the whole
# candidate only after every writer that populates it has finished.
chmod -R go-w "$candidate"

# The candidate server's environment, parsed rather than sourced.
#
# These two files were loaded with `source`, which runs as root here. That made
# every value Terraform sets root shell: an accepted absolute `site_dir`
# containing a space split into an assignment plus a command and aborted every
# deployment, and a command substitution or a semicolon in any value ran. The
# values are URLs and filesystem paths, so refusing punctuation is not an
# option and a blacklist of shell metacharacters is never complete. Nothing
# evaluates them now — the parser hands back one assignment per line and they
# reach uvicorn through `env`, a word at a time.
#
# One line per assignment is unambiguous only because the parser refuses a
# value carrying a control character, which it does for systemd's sake as well:
# an `EnvironmentFile` assignment cannot represent a newline either.
#
# Through a file rather than a command substitution. Bash tracks quotes while
# it looks for the closing paren of a `$(...)`, even across a quoted heredoc,
# so an apostrophe in a comment inside the program used to decide whether this
# script parsed at all -- and it happened to balance. A redirection has no such
# rule, and `set -e` fails the deployment on a parser that refused the file
# rather than leaving an empty result to be noticed later.
#
# `umask 077` scoped to this line: the file holds the session secret for as
# long as it takes to read it back.
environment_dump="$incoming/service-environment"
(umask 077; : > "$environment_dump")
"$python_bin" - "$etc_dir/config.json" "$etc_dir/session.env" > "$environment_dump" <<'PY'
import json
import re
import sys
from pathlib import Path

# Exactly what `local.host_config` declares, and exactly what the service
# needs. An allowlist rather than a name pattern, because the pattern accepted
# anything shaped like an environment variable -- including `PATH`, which
# decides what root's `env` resolves the following command to, and
# `LD_PRELOAD`, which decides what that command loads. Both apply before
# `setpriv` drops privileges.
#
# Written down here as well as in `main.tf` on purpose: this is the end that
# has to hold if the file is ever written by something other than the
# bootstrap this repository ships.
EXPECTED_CONFIG = frozenset(
    ("OIDC_ISSUER", "OIDC_CLIENT_ID", "PUBLIC_BASE_URL", "SITE_DIR", "POST_LOGOUT_PATH")
)
# Generated on the host, and the only thing that file is for.
EXPECTED_SESSION = frozenset(("SESSION_SECRET",))


def reject(message):
    raise SystemExit(f"cannot load host configuration: {message}")


def accept(resolved, name, value, origin):
    if not isinstance(value, str):
        reject(f"{origin}: {name} must be a string")
    if any(character < " " or character == "\x7f" for character in value):
        reject(f"{origin}: {name} carries a control character")
    resolved[name] = value


def require_exactly(present, expected, origin):
    unexpected = sorted(set(present) - expected)
    missing = sorted(expected - set(present))

    if unexpected:
        reject(f"{origin}: unexpected {', '.join(unexpected)}")
    if missing:
        reject(f"{origin}: missing {', '.join(missing)}")


def one_of_each(pairs, origin):
    """The pairs as a mapping, refusing a name that appears twice.

    JSON permits a repeated member name and every parser silently keeps one of
    them, so the `SITE_DIR` an operator reads in the file need not be the one
    that takes effect.
    """
    seen = {}
    for name, value in pairs:
        if name in seen:
            reject(f"{origin}: {name} is assigned more than once")
        seen[name] = value
    return seen


config_path, session_path = (Path(argument) for argument in sys.argv[1:3])
resolved = {}

# Terraform's channel onto this host: strict JSON, so a value means exactly
# what it says and nothing about it is a token.
try:
    config = json.loads(
        config_path.read_text(encoding="utf-8"),
        object_pairs_hook=lambda pairs: one_of_each(pairs, config_path),
    )
except (OSError, ValueError) as error:
    reject(f"{config_path}: {error}")

if not isinstance(config, dict):
    reject(f"{config_path}: expected a JSON object")

require_exactly(config, EXPECTED_CONFIG, config_path)
for name, value in config.items():
    accept(resolved, name, value, config_path)

# The session secret, in systemd's own `EnvironmentFile` format because systemd
# reads that file too. Generated on the host, but read the same way: the one
# file here that holds a secret should not be the one that is still shell.
try:
    lines = session_path.read_text(encoding="utf-8").splitlines()
except OSError as error:
    reject(f"{session_path}: {error}")

session = {}
for number, line in enumerate(lines, 1):
    if not line.strip() or line.lstrip().startswith(("#", ";")):
        continue
    name, separator, value = line.partition("=")
    if not separator:
        reject(f"{session_path}:{number}: not an assignment")
    name = name.strip()
    if name in session:
        reject(f"{session_path}:{number}: {name} is assigned more than once")
    if value.startswith('"'):
        if len(value) < 2 or not value.endswith('"'):
            reject(f"{session_path}:{number}: unterminated quote")
        value = re.sub(r"\\(.)", r"\1", value[1:-1])
    session[name] = value

require_exactly(session, EXPECTED_SESSION, session_path)
for name, value in session.items():
    accept(resolved, name, value, session_path)

# Sorted, so the argument list root hands to `env` is the same for the same
# configuration however the file happened to be ordered. It is the one thing
# about this step an operator can read back out of an SSM invocation log.
for name, value in sorted(resolved.items()):
    print(f"{name}={value}")
PY

service_environment=()
while IFS= read -r assignment; do
  if [[ -n "$assignment" ]]; then
    service_environment+=("$assignment")
  fi
done < "$environment_dump"
# Read once and gone. It holds the session secret, and while it lives in a
# directory only root can enter and the cleanup handler clears, the shorter it
# exists the less there is to reason about.
rm -f "$environment_dump"

if (( ${#service_environment[@]} == 0 )); then
  echo "host configuration under $etc_dir produced no environment" >&2
  exit 1
fi

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
# `SITE_DIR` last, so the candidate serves its own tree rather than whatever
# the active release is configured to serve.
"$env_bin" ${service_environment[@]+"${service_environment[@]}"} \
  SITE_DIR="$candidate/site" \
  "$timeout_bin" 30 \
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
  # The exit handler discards the candidate, the way it does for every other
  # failure that never reached activation.
  echo "candidate release failed health check" >&2
  exit 1
fi

stop_candidate_server

# Everything past the swap fails the same way and has to be undone the same
# way. `systemctl restart` returning non-zero used to end the installer on the
# spot under `set -e`, with `current` already pointing at the candidate and the
# previous release intact beside it: nothing rolled back, nothing restarted,
# and the host left on a release that had never started — the one state the
# two-stage swap exists to prevent.
restore_previous_release() {
  local reason="$1"

  if (( release_restored == 1 )); then
    return 0
  fi
  release_restored=1

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
}

abandon_activation() {
  local reason="$1"

  restore_previous_release "$reason"
  exit 1
}

maybe_test_pause() {
  [[ "${AUTHIFI_DOCS_TEST_PAUSE_POINT:-}" == "$1" ]] || return 0
  [[ -n "${AUTHIFI_DOCS_TEST_PAUSE_MARKER:-}" ]] || return 0

  : > "$AUTHIFI_DOCS_TEST_PAUSE_MARKER"
  while [[ -e "${AUTHIFI_DOCS_TEST_PAUSE_HOLD:-}" ]]; do
    sleep 0.05
  done
}

candidate_swapped=1
swap_current "$candidate"
maybe_test_pause after_replace_before_flag
maybe_test_pause after_swap

if ! "$systemctl_bin" restart authifi-docs; then
  abandon_activation "candidate release did not restart under systemd"
fi

if ! poll_health "http://127.0.0.1:8080/health" "$active_attempts"; then
  abandon_activation "active release failed health check"
fi

# Swapped in, restarted, and answering. Nothing below this line can roll the
# release back, so nothing below it may discard the tree the host is running.
candidate_activated=1

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
