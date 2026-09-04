"""Guards on the server's runtime dependency lock.

The runtime image is rebuilt from `server/requirements.txt` on every deploy, so
anything unpinned there means two builds of the same commit can ship different
security-relevant code -- and anything *absent* is unpinned, whatever the file
claims. These tests keep the pins exact, keep the lock equal to the closure of
`server/requirements.in` rather than a selection from it, keep the test
environment on the versions the image ships, and keep the Dockerfile, CI and
README installing that one file without an unpinned installer.
"""

from __future__ import annotations

import importlib.metadata as metadata
import re
import shutil
import subprocess
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


def requirement_names(path: Path) -> list[str]:
    """The requirement on each line, with pip's inline ` #` comments removed."""
    return [
        requirement
        for line in path.read_text(encoding="utf-8").splitlines()
        if (stripped := line.strip())
        and not stripped.startswith("#")
        and (requirement := stripped.split(" #", 1)[0].strip())
    ]


def requirement_lines() -> list[str]:
    return requirement_names(REQUIREMENTS)


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


# --- The lock is the whole graph, not a curated part of it ---------------------
#
# Pinning the direct dependencies and a few interesting transitives still lets
# the resolver choose `anyio`, `click`, `cffi`, `pycparser` and
# `typing_extensions` afresh on every build, which is most of the code the
# server actually imports. So the layout is the usual two files: `requirements.in`
# holds the five direct dependencies at reviewed versions, `requirements.txt` is
# the complete closure resolved from it, and the image installs only the latter.
#
# "Complete" is checked against a real dependency graph rather than a list of
# names, in two directions: nothing a locked package needs may be missing, and
# nothing unreachable from the direct set may be present.

DIRECT_REQUIREMENTS = REPO_ROOT / "server" / "requirements.in"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# The runtime interpreter, from the Dockerfile's base image. Markers are
# evaluated against this rather than against whatever runs the tests, so the
# closure computed here is the closure the image gets.
RUNTIME_PYTHON = "3.12"

# pip's own packages are installed in every environment and are not part of the
# application graph. Pinning them would be pinning the installer, which is a
# separate decision the digest-pinned base image already makes.
INSTALLER_PACKAGES = frozenset({"pip", "setuptools", "wheel", "distribute", "pkg_resources"})


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def direct_pins() -> dict[str, str]:
    pins: dict[str, str] = {}
    for requirement in requirement_names(DIRECT_REQUIREMENTS):
        match = PIN_PATTERN.match(requirement)
        assert match, f"direct requirement {requirement!r} is not an exact pin"
        pins[canonical(match["name"])] = match["version"]
    return pins


def locked_pins() -> dict[str, str]:
    return {canonical(name): version for name, version in pinned_versions().items()}


def runtime_requirements(package: str) -> list[str]:
    """The names `package` needs on the runtime interpreter, extras excluded."""
    from packaging.requirements import Requirement

    environment = {
        "extra": "",
        "python_version": RUNTIME_PYTHON,
        "python_full_version": f"{RUNTIME_PYTHON}.0",
    }
    needed = []
    for raw in metadata.distribution(package).requires or []:
        requirement = Requirement(raw)
        if requirement.marker and not requirement.marker.evaluate(environment):
            continue
        needed.append(canonical(requirement.name))
    return needed


def test_the_direct_file_lists_exactly_the_packages_the_server_imports() -> None:
    """Anything else in there is a transitive that belongs in the lock."""
    assert set(direct_pins()) == {"authlib", "httpx", "itsdangerous", "starlette", "uvicorn"}


def test_every_direct_dependency_is_locked_at_the_version_it_was_reviewed_at() -> None:
    locked = locked_pins()

    for package, version in direct_pins().items():
        assert locked.get(package) == version, (
            f"{package} is {version} in requirements.in and {locked.get(package)} in the lock"
        )


def test_the_lock_is_closed_under_the_runtime_dependency_graph() -> None:
    """No locked package may need something the lock does not pin."""
    locked = locked_pins()

    for package in sorted(locked):
        for needed in runtime_requirements(package):
            assert needed in locked, (
                f"{package} requires {needed}, which the lock does not pin; "
                "regenerate it per the header of server/requirements.txt"
            )


def test_the_lock_holds_nothing_the_direct_set_does_not_reach() -> None:
    """The other direction: a stale entry is drift too, and hides a removal."""
    reachable: set[str] = set()
    frontier = list(direct_pins())
    while frontier:
        package = frontier.pop()
        if package in reachable:
            continue
        reachable.add(package)
        frontier.extend(runtime_requirements(package))

    assert set(locked_pins()) == reachable


def test_the_lock_does_not_pin_the_installer() -> None:
    assert not INSTALLER_PACKAGES & set(locked_pins())


def test_the_lock_says_how_it_was_generated() -> None:
    """It is a generated file; the next person needs the command, not a guess."""
    header = REQUIREMENTS.read_text(encoding="utf-8").split("\n\n", 1)[0]
    digest = re.search(r"python:3\.12-slim@sha256:[0-9a-f]{64}", DOCKERFILE.read_text("utf-8"))

    assert digest
    assert "requirements.in" in header
    assert digest.group(0) in header, "the lock must name the image it was resolved in"


def test_the_dockerfile_runtime_stage_installs_the_lock_and_not_the_direct_file() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert "COPY server/requirements.txt" in text
    assert "requirements.in" not in text


# --- CI installs the same lock, the same way ----------------------------------


def ci_pip_commands() -> list[str]:
    """The workflow's `pip` command lines, with comments removed.

    Comments are stripped so that a note *about* a flag is not read as the flag
    -- the install step explains why it does not upgrade pip.
    """
    commands = []
    for line in CI_WORKFLOW.read_text(encoding="utf-8").splitlines():
        command = line.strip().split("#", 1)[0].strip()
        if "pip" in command:
            commands.append(command)
    return commands


def test_ci_installs_python_dependencies_with_pip() -> None:
    """Guard the guard, as above."""
    assert any("pip install" in command for command in ci_pip_commands())


def test_ci_does_not_upgrade_pip() -> None:
    """The same reasoning as the Dockerfile: an unpinned pip is not a pin.

    Validation that resolves a different installer than the day before is not
    validation of this commit.
    """
    for command in ci_pip_commands():
        assert "--upgrade pip" not in command, f"CI upgrades pip: {command!r}"


def test_ci_installs_the_complete_lock() -> None:
    installs = [command for command in ci_pip_commands() if "pip install" in command]

    assert installs
    for command in installs:
        assert "server/requirements-dev.txt" in command or "server/requirements.txt" in command


def test_ci_pins_everything_it_installs() -> None:
    """A bare `pytest` on the install line is an unpinned dependency too."""
    for command in (command for command in ci_pip_commands() if "pip install" in command):
        arguments = command.removeprefix("python -m pip install").split()
        for index, argument in enumerate(arguments):
            if argument.startswith("-"):
                continue
            assert index and arguments[index - 1] == "-r", (
                f"CI installs {argument!r} without a requirements file: {command!r}"
            )


# --- What a clean environment actually resolves --------------------------------
#
# Everything above reads files and the local metadata graph. These two build the
# real thing in the image the runtime uses, which is the only way to know the
# lock is the closure rather than a plausible-looking list.

requires_docker = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker CLI is not available"
)

RUNTIME_IMAGE = re.search(
    r"python:3\.12-slim@sha256:[0-9a-f]{64}", DOCKERFILE.read_text(encoding="utf-8")
).group(0)


def freeze_after_installing(requirements_filename: str) -> dict[str, str]:
    """Install one requirements file in a clean runtime image and freeze it."""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{REPO_ROOT / 'server'}:/in:ro",
            RUNTIME_IMAGE,
            "sh",
            "-c",
            "python -m pip install --quiet --no-cache-dir --root-user-action=ignore "
            f"-r /in/{requirements_filename} >/dev/null && python -m pip freeze",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    installed = {}
    for line in result.stdout.splitlines():
        match = PIN_PATTERN.match(line.strip())
        assert match, f"unexpected `pip freeze` line: {line!r}"
        installed[canonical(match["name"])] = match["version"]
    return installed


@requires_docker
def test_a_clean_install_of_the_direct_file_resolves_to_exactly_the_lock() -> None:
    """The lock is derived, and this is the derivation.

    A resolver that now picks a different version, or pulls a package the lock
    has never heard of, fails here rather than at deploy time.
    """
    assert freeze_after_installing("requirements.in") == locked_pins()


@requires_docker
def test_a_clean_install_of_the_lock_needs_nothing_beyond_it() -> None:
    """The image installs only this file, so it has to be self-sufficient."""
    assert freeze_after_installing("requirements.txt") == locked_pins()


# --- The setup instructions say the same thing --------------------------------


def readme_pip_commands() -> list[str]:
    return [
        stripped
        for line in (REPO_ROOT / "README.md").read_text(encoding="utf-8").splitlines()
        if "pip install" in (stripped := line.strip())
    ]


def test_the_readme_sets_up_the_same_environment_ci_validates() -> None:
    """A developer who follows it must not get different versions than CI."""
    commands = readme_pip_commands()

    assert commands
    for command in commands:
        assert "--upgrade pip" not in command, f"README upgrades pip: {command!r}"
        assert "server/requirements-dev.txt" in command
