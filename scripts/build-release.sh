#!/usr/bin/env bash
set -euo pipefail

sha="${1:?usage: build-release.sh SHA OUTPUT_DIR}"
output_dir="${2:?usage: build-release.sh SHA OUTPUT_DIR}"

if [[ ! "$sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "SHA must be 40 lowercase hexadecimal characters" >&2
  exit 2
fi

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON:-$root/.venv/bin/python}"
if [[ ! -x "$python_bin" ]]; then
  python_bin="$(command -v python3)"
fi
export PIP_DISABLE_PIP_VERSION_CHECK=1

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

release="$work/release"
mkdir -p "$release/deploy" "$release/server" "$release/wheelhouse" "$output_dir"

"$python_bin" -m mkdocs build --strict --config-file "$root/mkdocs.yml" --site-dir "$release/site"
cp "$root/docs/_headers" "$release/site/_headers"
cp "$root/infra/scripts/deploy-release.sh" "$release/deploy/deploy-release.sh"
cp "$root/server/requirements.txt" "$release/requirements.txt"

# The whole package, minus the test suite and build by-products. Naming the
# modules by hand meant a module added and forgotten shipped nothing and broke
# nothing here: the archive is produced, its checksum matches, and the offline
# install succeeds, because no step imports the package. It fails on the host,
# at the first request that reaches the missing module, after the candidate has
# already been promoted.
#
# The requirements files are excluded because the runtime lock ships at the
# archive root, which is where the installer and the CI offline check read it.
"$python_bin" - "$root/server" "$release/server" <<'PY'
import shutil
import sys
from pathlib import Path

source, destination = (Path(argument) for argument in sys.argv[1:3])

shutil.copytree(
    source,
    destination,
    dirs_exist_ok=True,
    ignore=shutil.ignore_patterns(
        "tests",
        "__pycache__",
        "*.pyc",
        "requirements*.txt",
        "requirements*.in",
    ),
)
PY

"$python_bin" -m pip download \
  --requirement "$root/server/requirements.txt" \
  --dest "$release/wheelhouse" \
  --only-binary=:all: \
  --platform manylinux_2_17_x86_64 \
  --platform manylinux_2_28_x86_64 \
  --implementation cp \
  --python-version 3.12

archive="$output_dir/$sha.tar.gz"
"$python_bin" - "$release" "$archive" <<'PY'
import gzip
import hashlib
import os
import sys
import tarfile
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))

with destination.open("wb") as raw:
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as archive:
            for path in sorted(source.rglob("*")):
                name = path.relative_to(source).as_posix()
                info = archive.gettarinfo(path, arcname=name)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = epoch
                # Permission bits were the last thing here still read off the
                # filesystem, which made a release's checksum depend on the
                # umask of whatever built it and on whether anyone had run
                # chmod in the checkout. A rerun reuses an existing S3 release
                # only when the checksum matches, so that dependency was a
                # failed deploy waiting for a runner image to change its
                # default. Nothing in a release needs to be writable or
                # executable: the archive's copy of the installer is
                # provenance, and the on-host virtualenv is built by pip.
                info.mode = 0o755 if path.is_dir() else 0o644
                if path.is_file():
                    with path.open("rb") as stream:
                        archive.addfile(info, stream)
                else:
                    archive.addfile(info)

digest = hashlib.sha256(destination.read_bytes()).hexdigest()
(destination.parent / f"{destination.name}.sha256").write_text(
    f"{digest}  {destination.name}\n",
    encoding="utf-8",
)
PY
