"""Guards on the two dependency locks: the server runtime and the site build.

Both are rebuilt from a requirements file inside a digest-pinned base image, so
anything unpinned in either means two builds of the same commit can ship
different code -- and anything *absent* from a lock is unpinned, whatever the
comment above it says. The site build is not exempt: it decides the HTML that
gets served, and a floating Jinja or Pygments is a floating build.

Each lock is therefore a pair of files. `requirements.in` holds the direct
dependencies at reviewed versions and is the file to edit; `requirements.txt`
is the complete closure resolved from it in the pinned image, and is the only
one the Dockerfile and CI install. These tests keep the pins exact, keep each
lock equal to the closure of its own direct file rather than a selection from
it, keep the two locks agreeing wherever they overlap, keep the test
environment on the versions the images ship, and keep the Dockerfile, CI and
README installing those files without an unpinned installer.
"""

from __future__ import annotations

import importlib.metadata as metadata
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
README = REPO_ROOT / "README.md"
EXTRACTED_RUNTIME_LOCK = (REPO_ROOT / "dist" / "expanded" / "requirements.txt").resolve()

PIN_PATTERN = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>[A-Za-z0-9._+!-]+)$")

# pip's own packages are installed in every environment and are not part of an
# application graph. Pinning them would be pinning the installer, which is a
# separate decision the digest-pinned base image already makes.
INSTALLER_PACKAGES = frozenset({"pip", "setuptools", "wheel", "distribute", "pkg_resources"})


@dataclass(frozen=True)
class Lock:
    label: str
    direct: Path
    lock: Path
    direct_packages: frozenset[str]


SERVER_LOCK = Lock(
    label="server runtime",
    direct=REPO_ROOT / "server" / "requirements.in",
    lock=REPO_ROOT / "server" / "requirements.txt",
    direct_packages=frozenset(
        {"authlib", "boto3", "httpx", "itsdangerous", "starlette", "uvicorn"}
    ),
)
SITE_LOCK = Lock(
    label="site build",
    direct=REPO_ROOT / "requirements.in",
    lock=REPO_ROOT / "requirements.txt",
    direct_packages=frozenset({"mkdocs", "mkdocs-material", "mkdocs-awesome-nav"}),
)
LOCKS = (SERVER_LOCK, SITE_LOCK)

# Dropping any of these would either unpin a direct dependency of the server or
# hand a security-critical transitive back to the resolver.
REQUIRED_SERVER_PACKAGES = frozenset(
    {
        "authlib",  # OIDC client
        "boto3",  # encrypted Parameter Store client
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

# Both stages of the Dockerfile run this image, so both closures are resolved
# against it. Markers are evaluated here rather than against whatever runs the
# tests, so what is checked is what the images get -- a macOS 3.14 development
# machine asks a different question than a Linux 3.12 build.
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

# Deliberately not in BUILD_ENVIRONMENT: CI builds on x86_64 and a developer on
# Apple silicon builds on arm64, so a closure that varied by these would not be
# one lock. A test below asserts no edge in either graph depends on them.
UNPINNED_MARKER_VARIABLES = ("platform_machine", "platform_release", "platform_version")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_names(path: Path) -> list[str]:
    """The requirement on each line, with pip's inline ` #` comments removed."""
    return [
        requirement
        for line in path.read_text(encoding="utf-8").splitlines()
        if (stripped := line.strip())
        and not stripped.startswith("#")
        and (requirement := stripped.split(" #", 1)[0].strip())
    ]


def pinned_versions(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for requirement in requirement_names(path):
        match = PIN_PATTERN.match(requirement)
        assert match, f"{path.name}: {requirement!r} is not an exact `name==version` pin"
        pins[canonical(match["name"])] = match["version"]
    return pins


def requirements_of(package: str) -> list[str]:
    """What `package` needs in the build environment, extras excluded."""
    from packaging.requirements import Requirement

    try:
        declared = metadata.distribution(package).requires or []
    except metadata.PackageNotFoundError:
        raise AssertionError(
            f"{package} is locked but not installed, so its dependencies cannot be "
            "read; run `pip install -r requirements.txt -r server/requirements-dev.txt`"
        ) from None

    needed = []
    for raw in declared:
        requirement = Requirement(raw)
        if requirement.marker and not requirement.marker.evaluate(BUILD_ENVIRONMENT):
            continue
        needed.append(canonical(requirement.name))
    return needed


def reachable_from(roots: dict[str, str]) -> set[str]:
    reached: set[str] = set()
    frontier = list(roots)
    while frontier:
        package = frontier.pop()
        if package in reached:
            continue
        reached.add(package)
        frontier.extend(requirements_of(package))
    return reached


def lock_id(lock: Lock) -> str:
    return lock.label


# --- Each lock is exact, and is the whole graph -------------------------------


@pytest.mark.parametrize("lock", LOCKS, ids=lock_id)
def test_every_requirement_is_pinned_exactly(lock: Lock) -> None:
    assert pinned_versions(lock.lock)


@pytest.mark.parametrize("lock", LOCKS, ids=lock_id)
def test_no_requirement_is_listed_twice(lock: Lock) -> None:
    names = [canonical(PIN_PATTERN.match(line)["name"]) for line in requirement_names(lock.lock)]

    assert len(names) == len(set(names))


@pytest.mark.parametrize("lock", LOCKS, ids=lock_id)
def test_the_direct_file_lists_exactly_the_packages_that_are_asked_for(lock: Lock) -> None:
    """Anything else in there is a transitive that belongs in the lock."""
    assert set(pinned_versions(lock.direct)) == lock.direct_packages


@pytest.mark.parametrize("lock", LOCKS, ids=lock_id)
def test_every_direct_dependency_is_locked_at_the_version_it_was_reviewed_at(lock: Lock) -> None:
    """Regenerating a lock must not quietly bump the versions above it.

    Left unconstrained, the resolver would take Starlette to 1.x and Authlib to
    1.8 the next time anyone ran the command; a major bump is a review.
    """
    locked = pinned_versions(lock.lock)

    for package, version in pinned_versions(lock.direct).items():
        assert locked.get(package) == version, (
            f"{package} is {version} in {lock.direct.name} and {locked.get(package)} in the lock"
        )


@pytest.mark.parametrize("lock", LOCKS, ids=lock_id)
def test_the_lock_is_closed_under_the_build_dependency_graph(lock: Lock) -> None:
    """No locked package may need something the lock does not pin."""
    locked = pinned_versions(lock.lock)

    for package in sorted(locked):
        for needed in requirements_of(package):
            assert needed in locked, (
                f"{package} requires {needed}, which {lock.lock.name} does not pin; "
                f"regenerate it per the header of {lock.lock}"
            )


@pytest.mark.parametrize("lock", LOCKS, ids=lock_id)
def test_the_lock_holds_nothing_the_direct_set_does_not_reach(lock: Lock) -> None:
    """The other direction: a stale entry is drift too, and hides a removal."""
    assert set(pinned_versions(lock.lock)) == reachable_from(pinned_versions(lock.direct))


def annotated_edges(lock: Lock) -> dict[str, list[str]]:
    """Each transitive's `# via a, b` note, as the packages it claims need it."""
    edges: dict[str, list[str]] = {}
    direct = pinned_versions(lock.direct)
    for line in lock.lock.read_text(encoding="utf-8").splitlines():
        pin, _, comment = line.strip().partition(" #")
        match = PIN_PATTERN.match(pin.strip())
        if not match or canonical(match["name"]) in direct:
            continue
        note = comment.split(" -- ", 1)[0].strip()
        assert note.startswith("via "), f"{pin} has no `# via` note saying why it is here"
        edges[canonical(match["name"])] = [
            canonical(name.strip()) for name in note.removeprefix("via ").split(",")
        ]
    return edges


@pytest.mark.parametrize("lock", LOCKS, ids=lock_id)
def test_every_transitive_says_which_package_pulled_it_in(lock: Lock) -> None:
    """A `via` note is documentation, so it is checked rather than trusted.

    Without this the notes rot into a plausible story about an older graph,
    which is worse than no story.
    """
    edges = annotated_edges(lock)

    assert set(edges) == set(pinned_versions(lock.lock)) - set(pinned_versions(lock.direct))
    for package, claimed_parents in edges.items():
        for parent in claimed_parents:
            assert package in requirements_of(parent), (
                f"{lock.lock.name} says {package} comes via {parent}, "
                f"which requires {sorted(requirements_of(parent))}"
            )


@pytest.mark.parametrize("lock", LOCKS, ids=lock_id)
def test_no_edge_in_the_graph_depends_on_the_machine_it_builds_on(lock: Lock) -> None:
    """Otherwise one lock could not describe both an x86_64 and an arm64 build.

    If this ever fails, the closure genuinely differs by architecture and the
    layout needs a per-platform answer rather than a wider marker environment.
    """
    from packaging.requirements import Requirement

    for package in sorted(pinned_versions(lock.lock)):
        for raw in metadata.distribution(package).requires or []:
            marker = str(Requirement(raw).marker or "")
            for variable in UNPINNED_MARKER_VARIABLES:
                assert variable not in marker, f"{package} requires {raw!r}"


@pytest.mark.parametrize("lock", LOCKS, ids=lock_id)
def test_the_lock_does_not_pin_the_installer(lock: Lock) -> None:
    assert not INSTALLER_PACKAGES & set(pinned_versions(lock.lock))


@pytest.mark.parametrize("lock", LOCKS, ids=lock_id)
def test_the_lock_says_how_it_was_generated(lock: Lock) -> None:
    """It is a generated file; the next person needs the command, not a guess."""
    header = lock.lock.read_text(encoding="utf-8").split("\n\n", 1)[0]

    assert lock.direct.name in header
    assert BUILD_IMAGE in header, "the lock must name the image it was resolved in"


@pytest.mark.parametrize("package", sorted(REQUIRED_SERVER_PACKAGES))
def test_security_critical_packages_stay_pinned(package: str) -> None:
    assert package in pinned_versions(SERVER_LOCK.lock)


# --- The two locks have to coexist --------------------------------------------
#
# CI installs both into one environment, and so does a developer following the
# README. Where they overlap -- click, certifi, idna, typing_extensions -- they
# have to name the same version, or whichever pip reads second silently wins and
# one of the two environments is not the one that was locked.

OVERLAPPING_PACKAGES = sorted(
    set(pinned_versions(SERVER_LOCK.lock)) & set(pinned_versions(SITE_LOCK.lock))
)


def test_the_locks_actually_overlap() -> None:
    """Guard the guard: the agreement check below is vacuous if they do not."""
    assert OVERLAPPING_PACKAGES


@pytest.mark.parametrize("package", OVERLAPPING_PACKAGES)
def test_both_locks_agree_on_a_shared_package(package: str) -> None:
    assert pinned_versions(SERVER_LOCK.lock)[package] == pinned_versions(SITE_LOCK.lock)[package]


ALL_PINS = sorted(
    {(package, version) for lock in LOCKS for package, version in pinned_versions(lock.lock).items()}
)


@pytest.mark.parametrize(("package", "pinned"), ALL_PINS, ids=[package for package, _ in ALL_PINS])
def test_the_test_environment_runs_the_pinned_versions(package: str, pinned: str) -> None:
    """Otherwise the suite vouches for code the images do not ship."""
    installed = metadata.version(package)

    assert installed == pinned, (
        f"{package} is pinned to {pinned} but the environment has {installed}; "
        "run `pip install -r requirements.txt -r server/requirements-dev.txt`"
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


def test_each_stage_installs_a_lock_and_never_a_direct_file() -> None:
    """The site-builder takes the site lock, the runtime stage the server lock."""
    text = DOCKERFILE.read_text(encoding="utf-8")

    assert "COPY requirements.txt" in text
    assert "COPY server/requirements.txt" in text
    assert "requirements.in" not in text


# --- CI and the README install the same locks, the same way -------------------


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


def executable_lines(run: str) -> list[str]:
    lines: list[str] = []
    pending = ""

    for raw_line in run.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        pending = f"{pending} {stripped}".strip() if pending else stripped
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue

        lines.append(pending)
        pending = ""

    if pending:
        lines.append(pending)
    return lines


def ci_step(name: str) -> dict[str, object]:
    steps = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["validate"]["steps"]
    return next(candidate for candidate in steps if candidate.get("name") == name)


def ci_generated_lock_aliases(run: str) -> dict[Path, Path]:
    aliases: dict[Path, Path] = {}
    if 'cmp --silent dist/expanded/requirements.txt server/requirements.txt' in executable_lines(run):
        aliases[EXTRACTED_RUNTIME_LOCK] = SERVER_LOCK.lock.resolve()
    return aliases


def requirement_files_installed_by(
    commands: list[str], generated_lock_aliases: dict[Path, Path] | None = None
) -> set[Path]:
    """Every requirements file the commands reach, following `-r` includes.

    `server/requirements-dev.txt` is how CI gets the server lock, so a check
    that only looked at the command line would miss it.
    """
    aliases = {path.resolve(): target.resolve() for path, target in (generated_lock_aliases or {}).items()}
    frontier: list[Path] = []
    for command in commands:
        if "pip install" not in command:
            continue
        arguments = command.split()
        frontier += [
            aliases.get((REPO_ROOT / argument).resolve(), (REPO_ROOT / argument).resolve())
            for flag, argument in zip(arguments, arguments[1:])
            if flag == "-r"
        ]

    reached: set[Path] = set()
    while frontier:
        path = frontier.pop().resolve()
        if path in reached:
            continue
        reached.add(path)
        for requirement in requirement_names(path):
            if requirement.startswith("-r "):
                frontier.append(path.parent / requirement.removeprefix("-r ").strip())
    return reached


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


@pytest.mark.parametrize("lock", LOCKS, ids=lock_id)
def test_ci_installs_the_complete_lock(lock: Lock) -> None:
    installed = requirement_files_installed_by(
        ci_pip_commands(),
        ci_generated_lock_aliases(str(ci_step("Verify offline release installation").get("run", ""))),
    )

    assert lock.lock.resolve() in installed, f"CI does not install {lock.lock}"
    assert lock.direct.resolve() not in installed, f"CI installs {lock.direct} directly"


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


def test_ci_accepts_the_extracted_runtime_lock_only_with_a_real_cmp_guard() -> None:
    commands = [
        "python -m pip install --no-index --find-links=dist/expanded/wheelhouse -r dist/expanded/requirements.txt"
    ]

    assert requirement_files_installed_by(
        commands, ci_generated_lock_aliases("cmp --silent dist/expanded/requirements.txt server/requirements.txt")
    ) == {SERVER_LOCK.lock.resolve()}


def test_ci_does_not_accept_a_commented_cmp_as_generated_lock_proof() -> None:
    assert not ci_generated_lock_aliases(
        "# cmp --silent dist/expanded/requirements.txt server/requirements.txt"
    )


def test_ci_does_not_accept_a_noop_cmp_mention_as_generated_lock_proof() -> None:
    assert not ci_generated_lock_aliases(
        ': "cmp --silent dist/expanded/requirements.txt server/requirements.txt"'
    )


def readme_pip_commands() -> list[str]:
    return [
        stripped
        for line in README.read_text(encoding="utf-8").splitlines()
        if "pip install" in (stripped := line.strip())
    ]


def test_the_readme_sets_up_the_same_environment_ci_validates() -> None:
    """A developer who follows it must not get different versions than CI."""
    commands = readme_pip_commands()

    assert commands
    assert requirement_files_installed_by(commands) == requirement_files_installed_by(
        ci_pip_commands(),
        ci_generated_lock_aliases(str(ci_step("Verify offline release installation").get("run", ""))),
    )
    for command in commands:
        assert "--upgrade pip" not in command, f"README upgrades pip: {command!r}"


# --- What a clean environment actually resolves --------------------------------
#
# Everything above reads files and the local metadata graph. These build the real
# thing in the image both stages run, which is the only way to know a lock is
# the closure rather than a plausible-looking list.

requires_docker = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker CLI is not available"
)

BUILD_IMAGE = re.search(
    r"python:3\.12-slim@sha256:[0-9a-f]{64}", DOCKERFILE.read_text(encoding="utf-8")
).group(0)


def freeze_after_installing(*requirements: Path) -> dict[str, str]:
    """Install requirements files in a clean build image and freeze the result."""
    arguments = " ".join(f"-r /repo/{path.relative_to(REPO_ROOT)}" for path in requirements)
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--volume",
            f"{REPO_ROOT}:/repo:ro",
            BUILD_IMAGE,
            "sh",
            "-c",
            "python -m pip install --quiet --no-cache-dir --root-user-action=ignore "
            f"{arguments} >/dev/null && python -m pip freeze",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"clean install of {arguments} failed:\n{result.stderr}"

    installed = {}
    for line in result.stdout.splitlines():
        match = PIN_PATTERN.match(line.strip())
        assert match, f"unexpected `pip freeze` line: {line!r}"
        installed[canonical(match["name"])] = match["version"]
    return installed


@requires_docker
@pytest.mark.parametrize("lock", LOCKS, ids=lock_id)
def test_a_clean_install_of_the_direct_file_resolves_to_exactly_the_lock(lock: Lock) -> None:
    """The lock is derived, and this is the derivation.

    A resolver that now picks a different version, or pulls a package the lock
    has never heard of, fails here rather than at deploy time.
    """
    assert freeze_after_installing(lock.direct) == pinned_versions(lock.lock)


@requires_docker
@pytest.mark.parametrize("lock", LOCKS, ids=lock_id)
def test_a_clean_install_of_the_lock_needs_nothing_beyond_it(lock: Lock) -> None:
    """Each image installs only its lock, so each has to be self-sufficient."""
    assert freeze_after_installing(lock.lock) == pinned_versions(lock.lock)


@requires_docker
def test_the_two_locks_install_together_without_a_resolver_conflict() -> None:
    """The environment CI and the README build, made for real.

    Two locks that each resolve alone can still be unsatisfiable together, and
    pip would report that as an error rather than pick a winner.
    """
    combined = pinned_versions(SERVER_LOCK.lock) | pinned_versions(SITE_LOCK.lock)

    assert freeze_after_installing(SERVER_LOCK.lock, SITE_LOCK.lock) == combined
