#!/usr/bin/env python3
"""Rewrite the two Python locks from a clean install of their `.in` files.

Version-only transitive drift is applied in place so `# via` notes stay
accurate. A changed package set, a moved direct pin, disagreeing overlap,
or a stale via note is a review, not something this command will invent.
Both locks are computed before either file is written.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
PIN_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>[A-Za-z0-9._+!-]+)(?P<rest>.*)$"
)
INSTALLER_PACKAGES = frozenset({"pip", "setuptools", "wheel", "distribute", "pkg-resources"})
LOCK_PAIRS = (
    (REPO_ROOT / "requirements.in", REPO_ROOT / "requirements.txt"),
    (REPO_ROOT / "server" / "requirements.in", REPO_ROOT / "server" / "requirements.txt"),
)
REQUIRES_MARKER = "---LOCK-REFRESH-REQUIRES---"
# Same marker environment as server/tests/test_requirements.py, so via notes
# accepted here are the ones the PR suite will check.
BUILD_ENVIRONMENT = {
    "extra": "",
    "python_version": "3.12",
    "python_full_version": "3.12.14",
    "implementation_name": "cpython",
    "platform_python_implementation": "CPython",
    "sys_platform": "linux",
    "platform_system": "Linux",
    "os_name": "posix",
}
UNPINNED_MARKER_VARIABLES = ("platform_machine", "platform_release", "platform_version")


class PackageSetChanged(Exception):
    """The freeze named a different set of packages than the lock."""


class DirectPinDrift(Exception):
    """A reviewed direct pin would change if the freeze were applied."""


class LockOverlapConflict(Exception):
    """The two locks would pin an overlapping package at different versions."""


class ViaNotesStale(Exception):
    """A `# via` parent no longer requires the transitive."""


class MachineSpecificMarker(Exception):
    """A Requires-Dist edge depends on the machine the lock was resolved on."""


@dataclass(frozen=True)
class Freeze:
    versions: dict[str, str]
    requires: dict[str, set[str]]


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def needed_from_declared(declared: list[str], environment: dict[str, str]) -> set[str]:
    """Requirements that apply in `environment`."""
    from packaging.requirements import Requirement

    needed: set[str] = set()
    for raw in declared:
        requirement = Requirement(raw)
        if requirement.marker and not requirement.marker.evaluate(environment):
            continue
        needed.add(canonical(requirement.name))
    return needed


def assert_no_machine_markers(package: str, declared: list[str]) -> None:
    from packaging.requirements import Requirement

    for raw in declared:
        marker = str(Requirement(raw).marker or "")
        for variable in UNPINNED_MARKER_VARIABLES:
            if variable in marker:
                raise MachineSpecificMarker(f"{package} requires {raw!r}")


REQUIRES_DUMP = f"""
import importlib.metadata as metadata
import json
import re
import sys
from packaging.requirements import Requirement

BUILD_ENVIRONMENT = {json.dumps(BUILD_ENVIRONMENT)}
UNPINNED_MARKER_VARIABLES = {list(UNPINNED_MARKER_VARIABLES)!r}

def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()

def needed_from_declared(declared, environment):
    needed = set()
    for raw in declared:
        requirement = Requirement(raw)
        if requirement.marker and not requirement.marker.evaluate(environment):
            continue
        needed.add(canonical(requirement.name))
    return needed

INSTALLER = {set(INSTALLER_PACKAGES)!r}
requires = {{}}
machine_markers = []
for dist in metadata.distributions():
    raw_name = dist.metadata["Name"]
    if raw_name is None:
        continue
    name = canonical(raw_name)
    if name in INSTALLER:
        continue
    declared = list(dist.requires or [])
    for raw in declared:
        marker = str(Requirement(raw).marker or "")
        for variable in UNPINNED_MARKER_VARIABLES:
            if variable in marker:
                machine_markers.append(f"{{name}} requires {{raw!r}}")
    requires[name] = sorted(needed_from_declared(declared, BUILD_ENVIRONMENT))
sys.stdout.write({REQUIRES_MARKER!r} + "\\n")
json.dump({{"requires": requires, "machine_markers": machine_markers}}, sys.stdout)
sys.stdout.write("\\n")
"""


def pinned_from_text(lock_text: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in lock_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pin = stripped.split(" #", 1)[0].strip()
        match = PIN_PATTERN.match(pin)
        if match:
            pins[canonical(match["name"])] = match["version"]
    return pins


def apply_freeze_to_lock(
    lock_text: str,
    freeze: dict[str, str],
    protected: set[str] | None = None,
) -> str:
    freeze_by_name = {
        canonical(name): version
        for name, version in freeze.items()
        if canonical(name) not in INSTALLER_PACKAGES
    }
    lock_names = list(pinned_from_text(lock_text))

    extra = set(freeze_by_name) - set(lock_names)
    missing = set(lock_names) - set(freeze_by_name)
    if extra or missing:
        parts = []
        if extra:
            parts.append("added: " + ", ".join(sorted(extra)))
        if missing:
            parts.append("removed: " + ", ".join(sorted(missing)))
        raise PackageSetChanged("; ".join(parts))

    protected_names = {canonical(name) for name in (protected or set())}
    rewritten: list[str] = []
    for line in lock_text.splitlines(keepends=True):
        newline = ""
        body = line
        if line.endswith("\n"):
            newline = "\n"
            body = line[:-1]
        if body.endswith("\r"):
            newline = "\r" + newline
            body = body[:-1]

        stripped = body.strip()
        if not stripped or stripped.startswith("#"):
            rewritten.append(line)
            continue

        match = PIN_PATTERN.match(stripped)
        if not match:
            rewritten.append(line)
            continue

        name = match["name"]
        current = match["version"]
        rest = match["rest"]
        wanted = freeze_by_name[canonical(name)]
        if wanted != current and canonical(name) in protected_names:
            raise DirectPinDrift(f"{canonical(name)} is {current} in the lock and {wanted} in the freeze")
        rewritten.append(f"{name}=={wanted}{rest}{newline}")

    return "".join(rewritten)


def assert_overlapping_versions(*lock_texts: str) -> None:
    pin_maps = [pinned_from_text(text) for text in lock_texts]
    overlap = set.intersection(*(set(pins) for pins in pin_maps))
    conflicts = []
    for package in sorted(overlap):
        versions = {pins[package] for pins in pin_maps}
        if len(versions) > 1:
            conflicts.append(f"{package}: " + ", ".join(sorted(versions)))
    if conflicts:
        raise LockOverlapConflict("; ".join(conflicts))


def via_parents(comment: str) -> list[str]:
    note = comment.split(" -- ", 1)[0].strip()
    if not note.startswith("via "):
        return []
    return [canonical(name.strip()) for name in note.removeprefix("via ").split(",") if name.strip()]


def assert_via_notes(
    lock_text: str,
    requires: dict[str, set[str]],
    directs: set[str],
) -> None:
    direct_names = {canonical(name) for name in directs}
    for line in lock_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pin, _, comment = stripped.partition(" #")
        match = PIN_PATTERN.match(pin.strip())
        if match is None or canonical(match["name"]) in direct_names:
            continue
        package = canonical(match["name"])
        parents = via_parents(comment)
        if not parents:
            raise ViaNotesStale(f"{package} has no # via note")
        for parent in parents:
            needed = requires.get(parent, set())
            if package not in needed:
                raise ViaNotesStale(
                    f"{package} is via {parent}, which requires {sorted(needed)}"
                )


def build_image() -> str:
    match = re.search(
        r"python:3\.12-slim@sha256:[0-9a-f]{64}",
        DOCKERFILE.read_text(encoding="utf-8"),
    )
    if match is None:
        raise SystemExit(f"could not find the pinned python:3.12-slim digest in {DOCKERFILE}")
    return match.group(0)


def pinned_directs(path: Path) -> dict[str, str]:
    return pinned_from_text(path.read_text(encoding="utf-8"))


def freeze_after_installing(direct: Path, image: str) -> Freeze:
    relative = direct.relative_to(REPO_ROOT).as_posix()
    dump = "python - <<'PY'\n" + REQUIRES_DUMP.lstrip() + "PY"
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{REPO_ROOT}:/repo:ro",
            image,
            "sh",
            "-c",
            "python -m pip install --quiet --no-cache-dir --root-user-action=ignore "
            f"-r /repo/{relative} >/dev/null && python -m pip freeze && "
            "python -m pip install --quiet --no-cache-dir --root-user-action=ignore packaging "
            f">/dev/null && {dump}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"clean install of {relative} failed:\n{result.stderr or result.stdout}"
        )

    stdout = result.stdout
    if REQUIRES_MARKER not in stdout:
        raise SystemExit(f"clean install of {relative} did not dump Requires-Dist")
    freeze_text, requires_text = stdout.split(REQUIRES_MARKER, 1)

    installed: dict[str, str] = {}
    for line in freeze_text.splitlines():
        match = PIN_PATTERN.match(line.strip())
        if match is None:
            continue
        name = canonical(match["name"])
        if name in INSTALLER_PACKAGES:
            continue
        installed[name] = match["version"]

    payload = json.loads(requires_text.strip())
    if not isinstance(payload, dict) or "requires" not in payload:
        raise SystemExit(f"clean install of {relative} dumped an unexpected Requires-Dist payload")
    machine_markers = payload.get("machine_markers") or []
    if machine_markers:
        raise MachineSpecificMarker("; ".join(str(item) for item in machine_markers))
    requires = {
        canonical(package): {canonical(dep) for dep in deps}
        for package, deps in payload["requires"].items()
    }
    return Freeze(versions=installed, requires=requires)


def compute_lock_updates() -> dict[Path, str]:
    image = build_image()
    planned: dict[Path, str] = {}
    for direct, lock in LOCK_PAIRS:
        freeze = freeze_after_installing(direct, image)
        rewritten = apply_freeze_to_lock(
            lock.read_text(encoding="utf-8"),
            freeze.versions,
            protected=set(pinned_directs(direct)),
        )
        assert_via_notes(rewritten, freeze.requires, set(pinned_directs(direct)))
        planned[lock] = rewritten
    assert_overlapping_versions(*planned.values())
    return planned


def refresh_locks() -> list[Path]:
    planned = compute_lock_updates()
    updated: list[Path] = []
    for lock, rewritten in planned.items():
        if rewritten != lock.read_text(encoding="utf-8"):
            lock.write_text(rewritten, encoding="utf-8")
            updated.append(lock)
    return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        updated = refresh_locks()
    except (
        PackageSetChanged,
        DirectPinDrift,
        LockOverlapConflict,
        ViaNotesStale,
        MachineSpecificMarker,
    ) as error:
        print(error, file=sys.stderr)
        return 1
    if updated:
        print("updated: " + ", ".join(path.relative_to(REPO_ROOT).as_posix() for path in updated))
    else:
        print("locks already match a clean install of the direct pins")
    return 0


if __name__ == "__main__":
    sys.exit(main())
