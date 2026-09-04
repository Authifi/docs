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
mkdir -p "$release/server" "$release/wheelhouse" "$output_dir"

"$python_bin" -m mkdocs build --strict --config-file "$root/mkdocs.yml" --site-dir "$release/site"
cp "$root/server/app.py" "$root/server/main.py" "$root/server/__init__.py" "$release/server/"
cp "$root/server/requirements.txt" "$release/requirements.txt"

"$python_bin" -m pip download \
  --requirement "$root/server/requirements.txt" \
  --dest "$release/wheelhouse" \
  --only-binary=:all: \
  --platform manylinux_2_17_x86_64 \
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
