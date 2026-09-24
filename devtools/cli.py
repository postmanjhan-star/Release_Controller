"""Local development commands exposed through ``uv run``.

Production continues to use the commands declared in ``Containerfile``. This
module only provides short, cross-platform entry points for local development.
"""

import subprocess
import sys
from collections.abc import Sequence


def _run(module: str, args: Sequence[str]) -> int:
    return subprocess.call([sys.executable, "-m", module, *args])


def _exit_with(module: str, args: Sequence[str]) -> None:
    raise SystemExit(_run(module, args))


def _has_option(args: Sequence[str], option: str) -> bool:
    return option in args or any(arg.startswith(f"{option}=") for arg in args)


def dev() -> None:
    """Start the local API with auto-reload enabled."""
    user_args = sys.argv[1:]
    args = ["app.main:app", "--reload"]
    if not _has_option(user_args, "--host"):
        args.extend(["--host", "127.0.0.1"])
    if not _has_option(user_args, "--port"):
        args.extend(["--port", "8000"])
    _exit_with("uvicorn", [*args, *user_args])


def db_current() -> None:
    """Show the current local database migration revision."""
    _exit_with("alembic", ["current", *sys.argv[1:]])


def db_upgrade() -> None:
    """Upgrade the local database to the latest migration."""
    _exit_with("alembic", ["upgrade", "head", *sys.argv[1:]])


def lint() -> None:
    """Run all Python static and formatting checks."""
    targets = ["app", "tests", "alembic", "devtools"]
    result = _run("ruff", ["check", *targets])
    if result == 0:
        result = _run("ruff", ["format", "--check", *targets])
    raise SystemExit(result)


def test() -> None:
    """Run the Python test suite with the repository coverage gate."""
    _exit_with(
        "pytest",
        ["--cov=app", "--cov-report=term-missing", "-q", *sys.argv[1:]],
    )


def quality() -> None:
    """Run the complete local Python quality gate."""
    targets = ["app", "tests", "alembic", "devtools"]
    commands = [
        ("ruff", ["check", *targets]),
        ("ruff", ["format", "--check", *targets]),
        ("pytest", ["--cov=app", "--cov-report=term-missing", "-q"]),
    ]
    for module, args in commands:
        result = _run(module, args)
        if result != 0:
            raise SystemExit(result)
    raise SystemExit(0)
