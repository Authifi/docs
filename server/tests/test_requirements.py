"""Guards on the server's runtime dependency pins.

The runtime image is rebuilt from `server/requirements.txt` on every deploy, so
a version range there means two builds of the same commit can ship different
security-relevant code. These tests keep the pins exact and keep the test
environment on the same versions the image ships.
"""

from __future__ import annotations

import importlib.metadata as metadata
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
REQUIREMENTS = REPO_ROOT / "server" / "requirements.txt"
DOCKERFILE = REPO_ROOT / "Dockerfile"

PIN_PATTERN = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>[A-Za-z0-9._+!-]+)$")

# Dropping any of these would either unpin a direct dependency or hand a
# security-critical transitive back to the resolver.
REQUIRED_PACKAGES = frozenset(
    {
        "authlib",  # OIDC client
        "starlette",  # ASGI framework and session cookie handling
        "uvicorn",  # HTTP server
        "httpx",  # outbound issuer calls
        "itsdangerous",  # session cookie signing
        "certifi",  # TLS trust store
        "cryptography",  # JWS/JWT verification behind Authlib
        "h11",  # HTTP/1.1 parsing, shared by uvicorn and httpx
        "httpcore",  # connection pooling and TLS handshake under httpx
        "idna",  # hostname parsing for issuer and redirect URLs
    }
)


def requirement_lines() -> list[str]:
    return [
        stripped
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]


def pinned_versions() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in requirement_lines():
        match = PIN_PATTERN.match(line)
        assert match, f"requirement {line!r} is not an exact `name==version` pin"
        pins[match["name"].lower()] = match["version"]
    return pins


def test_every_requirement_is_pinned_exactly() -> None:
    assert pinned_versions()


def test_no_requirement_is_listed_twice() -> None:
    names = [PIN_PATTERN.match(line)["name"].lower() for line in requirement_lines()]

    assert len(names) == len(set(names))


@pytest.mark.parametrize("package", sorted(REQUIRED_PACKAGES))
def test_security_critical_packages_stay_pinned(package: str) -> None:
    assert package in pinned_versions()


@pytest.mark.parametrize("package", sorted(pinned_versions()))
def test_the_test_environment_runs_the_pinned_versions(package: str) -> None:
    """Otherwise the suite vouches for code the image does not ship."""
    pinned = pinned_versions()[package]

    installed = metadata.version(package)

    assert installed == pinned, (
        f"{package} is pinned to {pinned} but the environment has {installed}; "
        "run `pip install -r server/requirements.txt -r server/requirements-dev.txt`"
    )


# --- Dockerfile build determinism ---------------------------------------------


def dockerfile_pip_commands() -> list[str]:
    """Every `python -m pip` invocation in the Dockerfile, joined per command."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    # RUN lines continue with a trailing backslash, so rejoin before splitting
    # on `&&`; otherwise a flag on the second line looks like its own command.
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    return [
        part.strip()
        for line in joined.splitlines()
        if line.startswith("RUN ")
        for part in line.removeprefix("RUN ").split("&&")
        if "pip" in part
    ]


def test_the_dockerfile_installs_with_pip_in_both_stages() -> None:
    """Guard the guard: the checks below are vacuous if this stops matching."""
    assert len(dockerfile_pip_commands()) == 2


def test_no_stage_upgrades_pip_before_installing() -> None:
    """A floating pip makes the digest-pinned base image only half a pin.

    `pip install --upgrade pip` resolves whatever is newest on PyPI at build
    time, so two builds of the same commit can install with different resolver
    behaviour. The pip bundled in the pinned base image is already fixed.
    """
    for command in dockerfile_pip_commands():
        assert "--upgrade pip" not in command, f"Dockerfile upgrades pip: {command!r}"
        assert not re.search(r"\bpip\s+install\b[^&]*\bpip\b", command), (
            f"Dockerfile installs pip itself: {command!r}"
        )


def test_both_stages_install_from_a_pinned_requirements_file() -> None:
    for command in dockerfile_pip_commands():
        assert "--no-cache-dir" in command
        assert "-r " in command, f"pip install is not requirements-driven: {command!r}"
