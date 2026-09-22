#!/usr/bin/env python3
"""Rewrite the two Python locks from a clean install of their `.in` files.

Version-only transitive drift is applied in place so `# via` notes stay
accurate. A changed package set or a moved direct pin is a review, not
something this command will invent.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
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


class PackageSetChanged(Exception):
    """The freeze named a different set of packages than the lock."""


class DirectPinDrift(Exception):
    """A reviewed direct pin would change if the freeze were applied."""


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


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
    lock_names: list[str] = []
    for line in lock_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pin = stripped.split(" #", 1)[0].strip()
        match = PIN_PATTERN.match(pin)
        if match:
            lock_names.append(canonical(match["name"]))

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


def build_image() -> str:
    match = re.search(
        r"python:3\.12-slim@sha256:[0-9a-f]{64}",
        DOCKERFILE.read_text(encoding="utf-8"),
    )
    if match is None:
        raise SystemExit(f"could not find the pinned python:3.12-slim digest in {DOCKERFILE}")
    return match.group(0)


def pinned_directs(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        requirement = stripped.split(" #", 1)[0].strip()
        match = PIN_PATTERN.match(requirement)
        if match is None:
            raise SystemExit(f"{path}: {requirement!r} is not an exact pin")
        pins[canonical(match["name"])] = match["version"]
    return pins


def freeze_after_installing(direct: Path, image: str) -> dict[str, str]:
    relative = direct.relative_to(REPO_ROOT).as_posix()
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
            f"-r /repo/{relative} >/dev/null && python -m pip freeze",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(
            f"clean install of {relative} failed:\n{result.stderr or result.stdout}"
        )

    installed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        match = PIN_PATTERN.match(line.strip())
        if match is None:
            continue
        name = canonical(match["name"])
        if name in INSTALLER_PACKAGES:
            continue
        installed[name] = match["version"]
    return installed


def refresh_locks() -> list[Path]:
    image = build_image()
    updated: list[Path] = []
    for direct, lock in LOCK_PAIRS:
        freeze = freeze_after_installing(direct, image)
        rewritten = apply_freeze_to_lock(
            lock.read_text(encoding="utf-8"),
            freeze,
            protected=set(pinned_directs(direct)),
        )
        if rewritten != lock.read_text(encoding="utf-8"):
            lock.write_text(rewritten, encoding="utf-8")
            updated.append(lock)
    return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        updated = refresh_locks()
    except (PackageSetChanged, DirectPinDrift) as error:
        print(error, file=sys.stderr)
        return 1
    if updated:
        print("updated: " + ", ".join(path.relative_to(REPO_ROOT).as_posix() for path in updated))
    else:
        print("locks already match a clean install of the direct pins")
    return 0


if __name__ == "__main__":
    sys.exit(main())
