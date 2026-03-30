"""automission CLI — primary interface."""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
import click
import questionary
from questionary import Style

from automission import __version__
from automission.config import (
    CONFIG_PATH,
    _KEY_MAP,
    generate_default_config,
    load_config,
    resolve_api_key,
    resolve_auth_method,
    resolve_default,
)
from automission.db import Ledger
from automission.models import MissionOutcome, VerificationResult
from automission.workspace import DEFAULT_BASE_DIR

logger = logging.getLogger(__name__)

_SELECT_STYLE = Style(
    [
        ("qmark", "fg:cyan"),
        ("question", "bold"),
        ("pointer", "fg:cyan bold"),
        ("highlighted", "fg:cyan bold noreverse bg:default"),
        ("selected", "noreverse"),
        ("answer", "fg:green bold"),
        ("instruction", "fg:#888888"),
        ("text", ""),
    ]
)

# ── Signal handling ──


def _setup_signal_handler(cancel_event: threading.Event):
    """Install Ctrl+C handler: first press = graceful stop, second = force exit."""

    def handler(signum, frame):
        if cancel_event.is_set():
            raise SystemExit(2)
        click.echo("\nGracefully stopping... (press Ctrl+C again to force)")
        cancel_event.set()

    signal.signal(signal.SIGINT, handler)


# ── Helper: find mission workspace ──


def _find_mission_workspace(mission_id: str) -> Path | None:
    """Scan DEFAULT_BASE_DIR and find the workspace for a given mission_id."""
    base = DEFAULT_BASE_DIR
    if not base.exists():
        return None
    for d in base.iterdir():
        if d.is_dir() and (d / "mission.db").exists():
            with Ledger(d / "mission.db") as ledger:
                if ledger.get_mission(mission_id):
                    return d
    return None


# ── CLI group ──


@click.group()
@click.version_option(version=__version__, prog_name="automission")
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output")
def cli(verbose: bool) -> None:
    """automission — Multi-agent autonomous mission execution."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ── init command ──


@cli.command()
@click.option("--force", is_flag=True, help="Overwrite existing config file")
def init(force: bool) -> None:
    """Interactive setup: choose backends, auth, write config, pull Docker image."""

    if CONFIG_PATH.exists() and not force:
        click.echo(f"Config already exists: {CONFIG_PATH}")
        click.echo("Use --force to overwrite.")
        return

    backends = ["claude", "codex", "gemini"]

    # ── Step 1: Agent backend ──
    click.echo("Step 1: Agent backend")
    agent_backend = questionary.select(
        "Choose agent backend:",
        choices=backends,
        qmark="›",
        style=_SELECT_STYLE,
        pointer="›",
        instruction=" ",
    ).ask()
    if agent_backend is None:
        raise SystemExit(0)
    agent_model = _prompt_model(agent_backend)
    agent_auth = _prompt_auth(agent_backend)

    # ── Step 2: Planner backend ──
    click.echo()
    click.echo("Step 2: Planner backend")
    planner_backend = questionary.select(
        "Choose planner backend:",
        choices=backends,
        qmark="›",
        style=_SELECT_STYLE,
        pointer="›",
        instruction=" ",
    ).ask()
    if planner_backend is None:
        raise SystemExit(0)
    planner_model = _prompt_model(planner_backend)
    planner_auth = _prompt_auth(planner_backend)

    # ── Step 3: Verifier ──
    click.echo()
    click.echo("Step 3: Verifier")
    use_planner = questionary.select(
        f"Use same settings as planner ({planner_backend} / {planner_model})?",
        choices=["yes", "no"],
        qmark="›",
        style=_SELECT_STYLE,
        pointer="›",
        instruction=" ",
    ).ask()
    if use_planner is None:
        raise SystemExit(0)

    if use_planner == "yes":
        verifier_backend = planner_backend
        verifier_model = planner_model
        verifier_auth = planner_auth
    else:
        verifier_choices = [planner_backend] + [
            b for b in backends if b != planner_backend
        ]
        verifier_backend = questionary.select(
            "Choose verifier backend:",
            choices=verifier_choices,
            qmark="›",
            style=_SELECT_STYLE,
            pointer="›",
            instruction=" ",
        ).ask()
        if verifier_backend is None:
            raise SystemExit(0)
        verifier_model = _prompt_model(verifier_backend)
        verifier_auth = _prompt_auth(verifier_backend)

    # ── Step 4: Write config ──
    click.echo()
    already_existed = CONFIG_PATH.exists()
    generate_default_config(
        CONFIG_PATH,
        agent_backend=agent_backend,
        agent_auth=agent_auth,
        agent_model=agent_model,
        planner_backend=planner_backend,
        planner_auth=planner_auth,
        planner_model=planner_model,
        verifier_backend=verifier_backend,
        verifier_auth=verifier_auth,
        verifier_model=verifier_model,
    )
    action = "Overwrote" if force and already_existed else "Created"
    click.echo(f"{action} config: {CONFIG_PATH} (mode 600)")
    click.echo(
        "Set API keys there or via environment variables (ANTHROPIC_API_KEY, etc.)"
    )

    # ── Step 5: Docker image ──
    click.echo()
    click.echo("Step 5: Docker image")
    docker_ok = False
    try:
        subprocess.run(
            ["docker", "version"],
            capture_output=True,
            check=True,
            timeout=10,
        )
        click.secho("  Docker: available", fg="green")
        docker_ok = True
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
    ):
        click.secho(
            "  Docker: not found (required at runtime by `automission run`)",
            fg="yellow",
        )

    if docker_ok:
        cfg = load_config()
        image = cfg.get("docker", "image", "ghcr.io/codance-ai/automission:latest")
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
        )
        if result.returncode == 0:
            click.echo(f"  Image: {image} (already pulled)")
        else:
            if click.confirm(f"  Pull Docker image '{image}'?", default=True):
                click.echo(f"  Pulling {image}...")
                pull = subprocess.run(["docker", "pull", image], capture_output=False)
                if pull.returncode == 0:
                    click.secho(f"  Pulled: {image}", fg="green")
                else:
                    click.secho(
                        f"  Failed to pull {image} (you can pull it later)", fg="yellow"
                    )
            else:
                click.echo("  Skipped image pull.")

    click.echo()
    click.secho(
        "Setup complete! Run `automission run --goal '...'` to start.", fg="green"
    )


def _prompt_model(backend: str) -> str:
    """Prompt user to choose a model for the given backend."""
    from automission.config import RECOMMENDED_MODELS

    models = RECOMMENDED_MODELS.get(backend, [])
    if not models:
        return ""
    choices = models + ["Other (type manually)"]
    model = questionary.select(
        f"Model for {backend}:",
        choices=choices,
        qmark="›",
        style=_SELECT_STYLE,
        pointer="›",
        instruction=" ",
    ).ask()
    if model is None:
        raise SystemExit(0)
    if model == "Other (type manually)":
        model = questionary.text(
            "Enter model name:", qmark="›", style=_SELECT_STYLE
        ).ask()
        if not model:
            raise SystemExit(0)
    return model


def _prompt_auth(backend: str) -> str:
    """Prompt for authentication method. Runs OAuth login if chosen.

    Claude only supports api_key (returns immediately).
    """
    if backend == "claude":
        return "api_key"

    auth = questionary.select(
        f"Authentication for {backend}:",
        choices=["api_key", "oauth"],
        qmark="›",
        style=_SELECT_STYLE,
        pointer="›",
        instruction=" ",
    ).ask()
    if auth is None:
        raise SystemExit(0)
    if auth == "oauth":
        _run_oauth_login(backend)
    return auth


def _run_oauth_login(backend: str) -> None:
    """Execute the OAuth login command for a backend."""
    from automission.config import _OAUTH_LOGIN_CMDS

    login_cmd = _OAUTH_LOGIN_CMDS.get(backend)
    if not login_cmd:
        return
    click.echo(f"  Running: {' '.join(login_cmd)}")
    try:
        login_result = subprocess.run(login_cmd, timeout=120, stdout=subprocess.DEVNULL)
        if login_result.returncode == 0:
            click.secho(f"  {backend} OAuth: logged in", fg="green")
        else:
            click.secho(
                f"  {backend} OAuth: login failed (exit {login_result.returncode})",
                fg="yellow",
            )
    except FileNotFoundError:
        click.secho(f"  {backend} CLI not found. Install it first.", fg="yellow")
    except subprocess.TimeoutExpired:
        click.secho(f"  {backend} OAuth: login timed out", fg="yellow")


# ── run command ──


@cli.command()
@click.option("--goal", default=None, help="Mission goal text")
@click.option(
    "--goal-file",
    type=click.Path(exists=True),
    default=None,
    help="Path to file containing goal text (mutually exclusive with --goal)",
)
@click.option(
    "--acceptance", type=click.Path(exists=True), help="Path to ACCEPTANCE.md"
)
@click.option("--verify", type=click.Path(exists=True), help="Path to verify.sh")
@click.option("--skill", multiple=True, help="Skill source (repeatable)")
@click.option("--agents", default=2, type=int, help="Number of agents")
@click.option("--max-iterations", default=20, type=int, help="Max attempts per agent")
@click.option("--max-cost", default=10.0, type=float, help="Max total cost (USD)")
@click.option("--timeout", default=3600, type=int, help="Max seconds")
@click.option(
    "--backend", default="claude", type=click.Choice(["claude", "codex", "gemini"])
)
@click.option("--model", default="claude-sonnet-4-6", help="Model for agent execution")
@click.option(
    "--docker-image",
    default="ghcr.io/codance-ai/automission:latest",
    help="Docker image for agent",
)
@click.option(
    "--init-from",
    type=click.Path(exists=True),
    help="Copy initial files from directory",
)
@click.option(
    "--workdir", type=click.Path(), help="Workspace directory (default: auto)"
)
@click.option("--yes", "-y", is_flag=True, help="Skip Planner confirmation")
@click.option(
    "--no-planner", is_flag=True, help="Skip Planner even if --acceptance is omitted"
)
@click.option("--planner-model", default="claude-sonnet-4-6", help="Model for Planner")
@click.option(
    "--planner-backend",
    default="claude",
    type=click.Choice(["claude", "codex", "gemini"]),
    help="Backend for Planner/Critic structured output (default: claude)",
)
@click.option(
    "--verifier-model",
    default=None,
    help="Model for Verifier (default: follows planner)",
)
@click.option(
    "--verifier-backend",
    default=None,
    type=click.Choice(["claude", "codex", "gemini"]),
    help="Backend for Verifier (default: follows planner)",
)
@click.option("--api-key", default=None, help="API key (overrides env var and config)")
@click.option("--json", "json_output", is_flag=True, help="Output result as JSON")
@click.option("--detach", is_flag=True, help="Start mission and return immediately")
def run(
    goal: str | None,
    goal_file: str | None,
    acceptance: str | None,
    verify: str | None,
    skill: tuple[str, ...],
    agents: int,
    max_iterations: int,
    max_cost: float,
    timeout: int,
    backend: str,
    model: str,
    docker_image: str,
    init_from: str | None,
    workdir: str | None,
    yes: bool,
    no_planner: bool,
    planner_model: str,
    planner_backend: str,
    verifier_model: str | None,
    verifier_backend: str | None,
    api_key: str | None,
    json_output: bool,
    detach: bool,
) -> None:
    """Create and start a mission."""
    # ── Resolve config defaults ──
    cfg = load_config()
    agents = resolve_default("agents", agents, cfg, 2)
    max_cost = resolve_default("max_cost", max_cost, cfg, 10.0)
    timeout = resolve_default("timeout", timeout, cfg, 3600)
    backend = resolve_default("backend", backend, cfg, "claude")
    docker_image = cfg.get("docker", "image", docker_image)
    planner_model = cfg.get("planner", "model", planner_model)

    # Resolve model: CLI flag (explicit) > config [defaults].model > CLI default
    ctx = click.get_current_context()
    if ctx.get_parameter_source("model") != click.core.ParameterSource.COMMANDLINE:
        cfg_model = cfg.defaults.get("model")
        if cfg_model:
            model = cfg_model

    # Resolve planner backend: CLI flag (explicit) > config [planner].backend > CLI default
    if (
        ctx.get_parameter_source("planner_backend")
        != click.core.ParameterSource.COMMANDLINE
    ):
        cfg_planner_backend = cfg.get("planner", "backend")
        if cfg_planner_backend:
            planner_backend = cfg_planner_backend

    # ── Resolve auth methods ──
    agent_auth = resolve_auth_method(backend, cfg, section="defaults")
    planner_auth = resolve_auth_method(planner_backend, cfg, section="planner")

    # Resolve verifier: CLI flag > config [verifier] > planner
    if verifier_backend is None:
        verifier_backend = cfg.get("verifier", "backend", planner_backend)
    if verifier_model is None:
        verifier_model = cfg.get("verifier", "model", planner_model)
    verifier_auth = resolve_auth_method(verifier_backend, cfg, section="verifier")

    # ── Resolve API key: CLI flag > env var > config ──
    resolved_key = resolve_api_key(backend, api_key, cfg)
    if resolved_key:
        # Inject into env so backends (claude -p, docker) can find it
        env_var = _KEY_MAP.get(backend, ("", ""))[0]
        if env_var and not os.environ.get(env_var):
            os.environ[env_var] = resolved_key

    # Also resolve planner backend API key if different
    if planner_backend != backend and planner_auth == "api_key":
        planner_key = resolve_api_key(planner_backend, config=cfg)
        if planner_key:
            env_var = _KEY_MAP.get(planner_backend, ("", ""))[0]
            if env_var and not os.environ.get(env_var):
                os.environ[env_var] = planner_key

    # Also resolve verifier backend API key if different from both
    if (
        verifier_backend != backend
        and verifier_backend != planner_backend
        and verifier_auth == "api_key"
    ):
        verifier_key = resolve_api_key(verifier_backend, config=cfg)
        if verifier_key:
            env_var = _KEY_MAP.get(verifier_backend, ("", ""))[0]
            if env_var and not os.environ.get(env_var):
                os.environ[env_var] = verifier_key

    # Validate mutually exclusive --goal / --goal-file
    if goal and goal_file:
        raise click.UsageError("--goal and --goal-file are mutually exclusive.")
    if not goal and not goal_file:
        raise click.UsageError("Either --goal or --goal-file is required.")

    # Read goal from file if --goal-file is provided
    if goal_file:
        goal = Path(goal_file).read_text().strip()
        if not goal:
            raise click.UsageError("--goal-file is empty.")

    # ── Pre-flight: verify Docker is available (needed by Planner and mission) ──
    try:
        from automission.docker import ensure_docker

        ensure_docker(docker_image)
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc))

    # ── Planner flow: auto-generate acceptance if not provided ──
    acceptance_content = None
    verify_content = None
    mission_content = None

    if no_planner and not acceptance:
        raise click.UsageError("Either provide --acceptance or remove --no-planner")

    if not acceptance and not no_planner:
        from automission.planner import Planner, PlanValidationError
        from automission.planner import (
            render_acceptance_md,
            render_mission_md,
        )
        from automission.harness import render_verify_sh
        from automission.structured_output import create_structured_backend

        try:
            click.echo("Planning mission...")
            so_backend = create_structured_backend(
                planner_backend, docker_image=docker_image, auth_method=planner_auth
            )
            planner = Planner(backend=so_backend, model=planner_model)
            draft = planner.plan(goal)
        except PlanValidationError as e:
            raise click.ClickException(f"Planner validation failed: {e}")
        except Exception as e:
            raise click.ClickException(f"Planner API call failed: {e}")

        _display_plan_draft(draft)

        if not yes:
            choice = click.prompt(
                "Accept and start?",
                type=click.Choice(["Y", "n", "edit"], case_sensitive=False),
                default="Y",
            )
            if choice.lower() == "n":
                click.echo("Mission cancelled.")
                sys.exit(0)
            if choice.lower() == "edit":
                draft = _edit_plan_draft(draft)

        acceptance_content = render_acceptance_md(draft)
        mission_content = render_mission_md(draft)
        if not verify:
            verify_content = render_verify_sh(draft.verification_surface)

    mission_id, ws = _create_mission_workspace(
        goal=goal,
        acceptance_path=Path(acceptance) if acceptance else None,
        acceptance_content=acceptance_content,
        verify_path=Path(verify) if verify else None,
        verify_content=verify_content,
        mission_content=mission_content,
        skill_sources=list(skill),
        agents=agents,
        max_iterations=max_iterations,
        max_cost=max_cost,
        timeout=timeout,
        backend_name=backend,
        model=model,
        docker_image=docker_image,
        init_files_dir=Path(init_from) if init_from else None,
        workspace_dir=Path(workdir) if workdir else None,
        planner_backend_name=planner_backend,
        agent_auth=agent_auth,
        verifier_backend_name=verifier_backend,
        verifier_model=verifier_model,
        verifier_auth=verifier_auth,
    )

    from automission.daemon import spawn_executor

    spawn_executor(ws, mission_id)

    if detach:
        click.echo(f"Mission {mission_id} started in background.")
        click.echo(f"Workspace: {ws}")
        click.echo(f"  automission attach {mission_id}   — reconnect live view")
        click.echo(f"  automission stop {mission_id}     — terminate mission")
        sys.exit(0)

    _attach_live_view(ws, mission_id)

    # Read final state from ledger
    outcome = MissionOutcome.FAILED
    mission_stats = {}
    changed_files_summary = []
    try:
        with Ledger(ws / "mission.db") as ledger:
            m = ledger.get_mission(mission_id)
            if m:
                mission_stats = m
                outcome = m["status"]
            changed_files_summary = _collect_changed_files(ledger, mission_id, ws)
    except Exception:
        logger.warning("Could not load mission stats from ledger")

    passed = outcome == MissionOutcome.COMPLETED
    exit_code = MissionOutcome.EXIT_CODES.get(outcome, 1)

    if json_output:
        result = {
            "mission_id": mission_id,
            "status": outcome,
            "total_attempts": mission_stats.get("total_attempts", 0),
            "changed_files": [f["path"] for f in changed_files_summary],
            "workspace": str(ws),
        }
        click.echo(json.dumps(result, indent=2))
    else:
        if passed:
            click.secho(f"\nMission {mission_id} completed successfully!", fg="green")
        elif outcome == MissionOutcome.CANCELLED:
            click.secho(f"\nMission {mission_id} cancelled.", fg="yellow")
        elif outcome == MissionOutcome.RESOURCE_LIMIT:
            click.secho(
                f"\nMission {mission_id} stopped: resource limit exceeded.", fg="yellow"
            )
        else:
            click.secho(
                f"\nMission {mission_id} did not pass verification.", fg="yellow"
            )

        # Show changed files
        if changed_files_summary:
            click.echo("\nChanged files:")
            for f in changed_files_summary:
                marker = "+" if f["status"] == "new" else "M"
                label = "(new)" if f["status"] == "new" else "(modified)"
                click.echo(f"  {marker} {f['path']:40s} {label}")

        click.echo(f"\nWorkspace: {ws}")
        click.echo(f"Export:    automission export {mission_id} --output ./my-project")

    sys.exit(exit_code)


def _display_plan_draft(draft) -> None:
    """Display PlanDraft summary to user."""
    click.echo(f"\nGenerated acceptance checklist ({len(draft.groups)} groups):")
    group_map = {g.id: i + 1 for i, g in enumerate(draft.groups)}
    for i, g in enumerate(draft.groups, 1):
        if g.depends_on:
            dep_refs = ", ".join(str(group_map.get(d, "?")) for d in g.depends_on)
            dep_str = f"(\u2192 {dep_refs})"
        else:
            dep_str = "(no deps)"
        click.echo(
            f"  [{i}] {g.id:20s} {dep_str:15s} \u2014 {len(g.criteria)} criteria"
        )

    if draft.constraints:
        click.echo("\nConstraints:")
        for c in draft.constraints:
            click.echo(f"  - {c}")

    vs = draft.verification_surface
    cmd = f"{vs.runner} {' '.join(vs.targets)} {vs.options}".strip()
    click.echo(f"\nVerification: {cmd}")

    if draft.assumptions:
        click.echo("\nAssumptions:")
        for a in draft.assumptions:
            click.echo(f"  - {a}")

    click.echo()


def _edit_plan_draft(draft):
    """Open ACCEPTANCE.md in $EDITOR, re-parse, update draft."""
    import os
    import tempfile
    from automission.acceptance import parse_acceptance_md
    from automission.planner import render_acceptance_md
    from automission.models import PlanCriterion, PlanGroup

    content = render_acceptance_md(draft)
    editor = os.environ.get("EDITOR", "vi")

    while True:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", prefix="acceptance-", delete=False
        ) as f:
            f.write(content)
            tmp_path = f.name

        os.system(f'{editor} "{tmp_path}"')
        try:
            with open(tmp_path) as fh:
                content = fh.read()
        finally:
            os.unlink(tmp_path)

        try:
            groups = parse_acceptance_md(content)
            if not groups:
                click.echo("Error: no acceptance groups found. Try again.")
                continue
            draft.groups = [
                PlanGroup(
                    id=g.id,
                    name=g.name,
                    depends_on=g.depends_on,
                    criteria=[
                        PlanCriterion(text=c.text, verification_hint="")
                        for c in g.criteria
                    ],
                )
                for g in groups
            ]
            return draft
        except ValueError as e:
            click.echo(f"Parse error: {e}")
            if not click.confirm("Edit again?"):
                click.echo("Using original plan.")
                return draft


def _collect_changed_files(ledger: Ledger, mission_id: str, ws: Path) -> list[dict]:
    """Aggregate changed files from all attempts and classify as new/modified."""
    attempts = ledger.get_attempts(mission_id)
    all_files: set[str] = set()
    for a in attempts:
        try:
            files = json.loads(a.get("changed_files", "[]"))
            all_files.update(files)
        except (json.JSONDecodeError, TypeError):
            pass

    # Classify: check if the file existed in the initial commit (baseline)
    import subprocess

    baseline_files: set[str] = set()
    result = subprocess.run(
        ["git", "log", "--format=", "--name-only", "--diff-filter=A", "HEAD~1..HEAD~1"],
        cwd=ws,
        capture_output=True,
        text=True,
    )
    # Simpler: check git log for the baseline commit's tree
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD~"],
        cwd=ws,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        baseline_files = set(result.stdout.strip().splitlines())

    summary = []
    for f in sorted(all_files):
        if not f or _is_metadata_file(f):
            continue
        status = "modified" if f in baseline_files else "new"
        summary.append({"path": f, "status": status})
    return summary


def _create_backend(
    name: str,
    docker_image: str = "ghcr.io/codance-ai/automission:latest",
    auth_method: str = "api_key",
    model: str | None = None,
):
    """Create the appropriate backend adapter."""
    if name == "claude":
        from automission.backend.claude import ClaudeCodeBackend

        return ClaudeCodeBackend(
            docker_image=docker_image, auth_method="api_key", model=model
        )
    if name == "codex":
        from automission.backend.codex import CodexBackend

        return CodexBackend(
            docker_image=docker_image, auth_method=auth_method, model=model
        )
    if name == "gemini":
        from automission.backend.gemini import GeminiBackend

        return GeminiBackend(
            docker_image=docker_image, auth_method=auth_method, model=model
        )
    raise click.ClickException(f"Unknown backend: '{name}'")


def _create_mission_workspace(
    goal: str,
    acceptance_path: Path | None,
    verify_path: Path | None,
    skill_sources: list[str],
    agents: int,
    max_iterations: int,
    max_cost: float,
    timeout: int,
    backend_name: str,
    model: str = "claude-sonnet-4-6",
    docker_image: str = "ghcr.io/codance-ai/automission:latest",
    init_files_dir: Path | None = None,
    workspace_dir: Path | None = None,
    acceptance_content: str | None = None,
    verify_content: str | None = None,
    mission_content: str | None = None,
    planner_backend_name: str = "claude",
    agent_auth: str = "api_key",
    verifier_backend_name: str = "claude",
    verifier_model: str = "claude-sonnet-4-6",
    verifier_auth: str = "api_key",
) -> tuple[str, Path]:
    """Create workspace and return (mission_id, workspace_path). Does NOT start execution."""
    from automission.workspace import create_mission
    from automission.docker import ensure_docker

    try:
        ensure_docker(docker_image)
    except (RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc))

    agent_backend = _create_backend(
        backend_name, docker_image=docker_image, auth_method=agent_auth, model=model
    )

    import uuid

    mission_id = uuid.uuid4().hex[:12]
    click.echo(f"Creating mission {mission_id}...")

    ws = create_mission(
        mission_id=mission_id,
        goal=goal,
        acceptance_path=acceptance_path,
        acceptance_content=acceptance_content,
        verify_path=verify_path,
        verify_content=verify_content,
        mission_content=mission_content,
        backend=agent_backend,
        workspace_dir=workspace_dir,
        skill_sources=skill_sources,
        init_files_dir=init_files_dir,
        agents=agents,
        max_iterations=max_iterations,
        max_cost=max_cost,
        timeout=timeout,
        backend_name=backend_name,
        docker_image=docker_image,
        model=model,
        agent_auth=agent_auth,
        verifier_backend_name=verifier_backend_name,
        verifier_model=verifier_model,
        verifier_auth=verifier_auth,
    )
    return mission_id, ws


def _attach_live_view(workspace_dir: Path, mission_id: str) -> None:
    """Tail events.jsonl and render live progress. Ctrl+C exits cleanly."""
    from automission.events import EventTailer

    events_file = workspace_dir / "events.jsonl"
    for _ in range(50):
        if events_file.exists():
            break
        time.sleep(0.2)

    if not events_file.exists():
        click.echo("Error: executor did not start (no events file).")
        return

    tailer = EventTailer(events_file)
    stop = threading.Event()

    try:
        for event in tailer.follow(stop_event=stop, poll_interval=0.2):
            _render_event(event)
    except KeyboardInterrupt:
        click.echo(f"\nMission {mission_id} is still running in background.")
        click.echo(f"  automission attach {mission_id}   — reconnect live view")
        click.echo(f"  automission stop {mission_id}     — terminate mission")


def _fmt_tokens(total: int) -> str:
    """Format token count for display: 1234 → '1.2k tokens'."""
    if total >= 1_000_000:
        return f"{total / 1_000_000:.1f}M tokens"
    if total >= 1_000:
        return f"{total / 1_000:.1f}k tokens"
    return f"{total} tokens"


def _fmt_changed_files(files: list[str], max_shown: int = 5) -> str:
    """Format changed files list: 'a.py, b.py (+2 files)'."""
    files = [f for f in files if not _is_metadata_file(f)]
    if not files:
        return ""
    basenames = [Path(f).name for f in files]
    if len(basenames) <= max_shown:
        return f"{', '.join(basenames)} ({len(files)} file{'s' if len(files) != 1 else ''})"
    shown = ", ".join(basenames[:max_shown])
    return f"{shown} +{len(files) - max_shown} more ({len(files)} files)"


def _render_criteria(
    event_or_vr: dict, *, indent: str = "    ", verbose: bool = False
) -> None:
    """Render verification summary from event or verification result dict."""
    summary = event_or_vr.get("summary", "")
    if summary:
        click.echo(f"{indent}{summary}")

    group_analysis = event_or_vr.get("group_analysis", {})
    if group_analysis:
        for gid, completed in group_analysis.items():
            symbol = (
                click.style("\u2713", fg="green")
                if completed
                else click.style("\u2717", fg="red")
            )
            click.echo(f"{indent}{symbol} {gid}")

    next_actions = event_or_vr.get("next_actions", [])
    if verbose and next_actions:
        for action in next_actions:
            click.echo(f"{indent}  \u2192 {action}")


def _render_attempt_log(
    attempt: dict,
    groups: list,
    *,
    prev_attempt: dict | None = None,
    verbose: bool = False,
) -> None:
    """Render a single attempt with full detail for logs output."""
    gate = "PASS" if attempt["verification_passed"] else "FAIL"
    color = "green" if attempt["verification_passed"] else "red"
    tokens = (attempt["token_input"] or 0) + (attempt["token_output"] or 0)

    # Header line — include retry focus for attempt > 1
    header = (
        f"  #{attempt['attempt_number']}  {attempt['agent_id']:10s}  "
        f"{click.style(gate, fg=color):4s}  "
        f"{_fmt_tokens(tokens)}  {attempt['duration_s']:.0f}s"
    )
    if prev_attempt and prev_attempt.get("verification_result"):
        try:
            prev_vr = VerificationResult.from_json(prev_attempt["verification_result"])
            failed_groups = [
                gid for gid, done in prev_vr.group_analysis.items() if not done
            ][:3]
            if failed_groups:
                header += f" \u2014 focus: {', '.join(failed_groups)}"
        except (json.JSONDecodeError, KeyError) as e:
            logger.debug("Could not parse prev verification result: %s", e)
    click.echo(header)

    # Changed files
    try:
        changed = json.loads(attempt.get("changed_files", "[]"))
    except (json.JSONDecodeError, TypeError):
        changed = []
    if changed:
        click.echo(f"    changed: {_fmt_changed_files(changed)}")

    # Criteria breakdown
    if attempt.get("verification_result"):
        try:
            vr = VerificationResult.from_json(attempt["verification_result"])
            criteria_data = {
                "summary": vr.critic.summary,
                "group_analysis": vr.group_analysis,
                "next_actions": vr.critic.next_actions,
            }
            _render_criteria(criteria_data, verbose=verbose)
            if verbose and vr.critic.root_cause:
                click.echo(f"    root cause: {vr.critic.root_cause}")
        except (json.JSONDecodeError, KeyError) as e:
            logger.debug("Could not parse verification result: %s", e)


def _render_event(event: dict) -> None:
    """Render a single event to terminal."""
    etype = event.get("type", "unknown")
    if etype == "mission_started":
        mid = event.get("mission_id", "?")
        agents = event.get("agents", 1)
        click.echo(f"Mission {mid} started ({agents} agent{'s' if agents > 1 else ''})")
    elif etype == "attempt_start":
        agent = event.get("agent_id", "?")
        attempt = event.get("attempt", "?")
        scope = event.get("scope")
        if scope:
            click.echo(f"  [{agent}] attempt #{attempt} — focus: {scope}")
        else:
            click.echo(f"  [{agent}] attempt #{attempt} ...")
    elif etype == "attempt_end":
        status = event.get("status", "?")
        tokens = event.get("token_input", 0) + event.get("token_output", 0)
        click.echo(f"  attempt done ({status}, {_fmt_tokens(tokens)})")
        changed = event.get("changed_files", [])
        if changed:
            click.echo(f"    changed: {_fmt_changed_files(changed)}")
    elif etype == "verification":
        passed = event.get("passed", False)
        color = "green" if passed else "red"
        label = "PASS" if passed else "FAIL"
        click.echo(f"  verify: {click.style(label, fg=color)}")
        _render_criteria(event)
        summary = event.get("summary")
        if summary:
            click.echo(f"    {summary}")
    elif etype == "group_start":
        name = event.get("group_name", event.get("group_id", "?"))
        click.echo(f"\nWorking on: {name}")
    elif etype == "group_completed":
        gid = event.get("group_id", "?")
        click.secho(f"  Group {gid} completed!", fg="green")
    elif etype == "mission_completed":
        attempts = event.get("total_attempts", 0)
        click.secho(f"\nMission completed! ({attempts} attempts)", fg="green")
    elif etype == "mission_failed":
        outcome = event.get("outcome", "failed")
        click.secho(f"\nMission ended: {outcome}", fg="yellow")
    elif etype == "executor_shutdown":
        reason = event.get("reason", "unknown")
        click.echo(f"\nExecutor stopped: {reason}")
    else:
        data = {k: v for k, v in event.items() if k not in ("type", "ts")}
        if data:
            click.echo(f"  [{etype}] {json.dumps(data)}")


# ── status command ──


@cli.command()
@click.argument("mission_id", required=False)
def status(mission_id: str | None) -> None:
    """Show mission status."""
    base = DEFAULT_BASE_DIR
    if not base.exists():
        click.echo("No missions found.")
        return

    if mission_id:
        _show_mission_status(mission_id)
    else:
        _show_latest_mission()


def _show_mission_status(mission_id: str) -> None:
    """Show status for a specific mission."""
    base = DEFAULT_BASE_DIR
    if not base.exists():
        click.echo(f"Mission {mission_id} not found.")
        return

    # Search all mission dirs for this mission_id
    for d in base.iterdir():
        if d.is_dir() and (d / "mission.db").exists():
            with Ledger(d / "mission.db") as ledger:
                m = ledger.get_mission(mission_id)
                if m:
                    _print_mission(m, d, ledger)
                    return

    click.echo(f"Mission {mission_id} not found.")


def _show_latest_mission() -> None:
    """Show status of the most recent mission."""
    base = DEFAULT_BASE_DIR
    if not base.exists():
        click.echo("No missions found.")
        return

    mission_dirs = [
        d for d in base.iterdir() if d.is_dir() and (d / "mission.db").exists()
    ]
    if not mission_dirs:
        click.echo("No missions found.")
        return

    latest = max(mission_dirs, key=lambda d: d.stat().st_mtime)
    with Ledger(latest / "mission.db") as ledger:
        missions = ledger.list_missions()
        if missions:
            _print_mission(missions[0], latest, ledger)
        else:
            click.echo("No missions found in ledger.")


def _print_mission(mission: dict, ws: Path, ledger: Ledger) -> None:
    """Print mission status details."""
    mission_id = mission["id"]
    click.echo(f"Mission:  {mission_id}")
    click.echo(f"Status:   {mission['status']}")
    click.echo(f"Attempts: {mission['total_attempts']}")
    if mission["agents"] > 1:
        click.echo(f"Agents:   {mission['agents']}")
    click.echo(f"Workspace: {ws}")

    groups = ledger.get_acceptance_groups(mission_id)
    if groups:
        # Build per-group criteria counts from latest verification
        group_passed_counts: dict[str, int] = {}
        last_attempt = ledger.get_last_attempt(mission_id)
        if last_attempt and last_attempt.get("verification_result"):
            try:
                vr = VerificationResult.from_json(last_attempt["verification_result"])
                for gid, done in vr.group_analysis.items():
                    # Find group name by id
                    for g in groups:
                        if g.id == gid:
                            group_passed_counts[g.name] = len(g.criteria) if done else 0
                            break
            except (json.JSONDecodeError, KeyError):
                logger.warning(
                    "Could not parse verification_result for criteria counts"
                )

        click.echo("\nAcceptance Checklist:")
        for g in groups:
            row = ledger.conn.execute(
                "SELECT completed FROM acceptance_groups WHERE id = ?", (g.id,)
            ).fetchone()
            completed = bool(row[0]) if row else False
            marker = (
                click.style("✓", fg="green")
                if completed
                else click.style("○", fg="yellow")
            )
            total = len(g.criteria)
            passed = group_passed_counts.get(g.name)
            if passed is not None:
                click.echo(f"  {marker} {g.name} ({passed}/{total})")
            else:
                click.echo(f"  {marker} {g.name} ({total} criteria)")

    attempts = ledger.get_attempts(mission_id)
    if attempts:
        click.echo("\nAttempt History:")
        for a in attempts:
            gate = "PASS" if a["verification_passed"] else "FAIL"
            color = "green" if a["verification_passed"] else "red"
            tokens = (a["token_input"] or 0) + (a["token_output"] or 0)
            click.echo(
                f"  #{a['attempt_number']}  {a['agent_id']:10s}  "
                f"{click.style(gate, fg=color):4s}  "
                f"{_fmt_tokens(tokens)}  {a['duration_s']:.0f}s"
            )


# ── logs command ──


@cli.command()
@click.argument("mission_id", required=False)
@click.option("--last", type=int, help="Show last N attempts")
@click.option(
    "-v", "--verbose", "verbose_logs", is_flag=True, help="Include verification details"
)
@click.option("-f", "--follow", is_flag=True, help="Live follow mode (poll every 2s)")
@click.option("--json", "json_output", is_flag=True, help="JSON output")
def logs(mission_id, last, verbose_logs, follow, json_output):
    """Show mission attempt logs."""
    # Find the workspace
    ws = None
    if mission_id:
        ws = _find_mission_workspace(mission_id)
    else:
        # Find most recent mission
        base = DEFAULT_BASE_DIR
        if base.exists():
            mission_dirs = [
                d for d in base.iterdir() if d.is_dir() and (d / "mission.db").exists()
            ]
            if mission_dirs:
                ws = max(mission_dirs, key=lambda d: d.stat().st_mtime)
                # Get mission_id from the ledger
                with Ledger(ws / "mission.db") as ledger:
                    missions = ledger.list_missions()
                    if missions:
                        mission_id = missions[0]["id"]

    if not ws or not mission_id:
        click.echo("No missions found.")
        return

    def _display_attempts():
        with Ledger(ws / "mission.db") as ledger:
            attempts = ledger.get_attempts(mission_id)
            if not attempts:
                click.echo("No attempts found.")
                return 0

            if last:
                attempts = attempts[-last:]

            groups = ledger.get_acceptance_groups(mission_id)

            if json_output:
                output = []
                for a in attempts:
                    entry = {
                        "attempt_number": a["attempt_number"],
                        "agent_id": a["agent_id"],
                        "status": a["status"],
                        "verification_passed": bool(a["verification_passed"]),
                        "token_input": a["token_input"],
                        "token_output": a["token_output"],
                        "duration_s": a["duration_s"],
                    }
                    try:
                        raw_files = json.loads(a.get("changed_files", "[]"))
                        entry["changed_files"] = [
                            f for f in raw_files if not _is_metadata_file(f)
                        ]
                    except (json.JSONDecodeError, TypeError):
                        entry["changed_files"] = []
                    if verbose_logs and a.get("verification_result"):
                        try:
                            entry["verification_result"] = json.loads(
                                a["verification_result"]
                            )
                        except (json.JSONDecodeError, TypeError):
                            pass
                    output.append(entry)
                click.echo(json.dumps(output, indent=2))
            else:
                for i, a in enumerate(attempts):
                    prev = attempts[i - 1] if i > 0 else None
                    _render_attempt_log(
                        a, groups, prev_attempt=prev, verbose=verbose_logs
                    )

            return len(attempts)

    if follow:
        # Follow mode: tail events.jsonl for all events (planner, groups, attempts)
        from automission.events import EventTailer

        events_file = ws / "events.jsonl"
        if not events_file.exists():
            click.echo("Waiting for events...")
            for _ in range(50):
                if events_file.exists():
                    break
                time.sleep(0.2)

        if not events_file.exists():
            click.echo("No events file found.")
            return

        tailer = EventTailer(events_file)
        try:
            for event in tailer.follow(poll_interval=0.5):
                _render_event(event)
        except KeyboardInterrupt:
            pass
    else:
        _display_attempts()


# ── attach command ──


@cli.command()
@click.argument("mission_id")
def attach(mission_id: str) -> None:
    """Reconnect to a running mission's live view."""
    ws = _find_mission_workspace(mission_id)
    if not ws:
        click.echo(f"Mission {mission_id} not found.")
        return
    from automission.daemon import is_executor_alive

    if not is_executor_alive(ws, mission_id):
        with Ledger(ws / "mission.db") as ledger:
            m = ledger.get_mission(mission_id)
        if m:
            click.echo(f"Mission {mission_id} is not running (status: {m['status']}).")
        else:
            click.echo(f"Mission {mission_id} not found.")
        return
    click.echo(f"Attaching to mission {mission_id}...")
    _attach_live_view(ws, mission_id)


# ── stop command ──


@cli.command()
@click.argument("mission_id", required=False)
@click.option("--yes", "-y", is_flag=True)
def stop(mission_id, yes):
    """Stop a running mission."""
    from automission.daemon import (
        is_executor_alive,
        stop_executor,
        wait_for_executor_exit,
    )

    if not mission_id:
        # Find most recent running mission
        base = DEFAULT_BASE_DIR
        if not base.exists():
            click.echo("Mission not found.")
            return
        mission_dirs = [
            d for d in base.iterdir() if d.is_dir() and (d / "mission.db").exists()
        ]
        if not mission_dirs:
            click.echo("Mission not found.")
            return
        latest = max(mission_dirs, key=lambda d: d.stat().st_mtime)
        with Ledger(latest / "mission.db") as ledger:
            missions = ledger.list_missions()
            running = [m for m in missions if m["status"] == "running"]
            if not running:
                click.echo("No running missions found.")
                return
            mission_id = running[0]["id"]

    ws = _find_mission_workspace(mission_id)
    if not ws:
        click.echo(f"Mission {mission_id} not found.")
        return

    with Ledger(ws / "mission.db") as ledger:
        m = ledger.get_mission(mission_id)
        if not m:
            click.echo(f"Mission {mission_id} not found.")
            return

        if m["status"] != "running":
            click.echo(f"Mission {mission_id} is not running (status: {m['status']}).")
            return

        if not yes:
            if not click.confirm(f"Stop mission {mission_id}?"):
                return

    if is_executor_alive(ws, mission_id):
        stop_executor(ws, mission_id)
        click.echo(f"Stopping mission {mission_id}...")
        if wait_for_executor_exit(ws, mission_id, timeout=30):
            click.echo(f"Mission {mission_id} stopped.")
        else:
            click.echo(
                f"Mission {mission_id} did not stop within 30s — may still be shutting down."
            )
    else:
        # Executor not alive, fallback to DB update
        with Ledger(ws / "mission.db") as ledger:
            ledger.update_mission_status(mission_id, MissionOutcome.CANCELLED)
        click.echo(f"Mission {mission_id} marked as cancelled.")


# ── list command ──


@cli.command(name="list")
@click.option("--json", "json_output", is_flag=True)
def list_missions(json_output):
    """List all missions."""
    base = DEFAULT_BASE_DIR
    if not base.exists():
        if json_output:
            click.echo("[]")
        else:
            click.echo("No missions found.")
        return

    mission_dirs = [
        d for d in base.iterdir() if d.is_dir() and (d / "mission.db").exists()
    ]
    if not mission_dirs:
        if json_output:
            click.echo("[]")
        else:
            click.echo("No missions found.")
        return

    all_missions = []
    for d in sorted(mission_dirs, key=lambda d: d.stat().st_mtime, reverse=True):
        try:
            with Ledger(d / "mission.db") as ledger:
                for m in ledger.list_missions():
                    all_missions.append(
                        {
                            "id": m["id"],
                            "status": m["status"],
                            "goal": m["goal"][:80] if m["goal"] else "",
                            "total_attempts": m["total_attempts"],
                            "workspace": str(d),
                        }
                    )
        except Exception:
            logger.warning("Could not read ledger in %s", d)

    if not all_missions:
        if json_output:
            click.echo("[]")
        else:
            click.echo("No missions found.")
        return

    if json_output:
        click.echo(json.dumps(all_missions, indent=2))
    else:
        for m in all_missions:
            status_color = {
                "completed": "green",
                "failed": "red",
                "cancelled": "yellow",
                "running": "blue",
                "resource_limit": "yellow",
            }.get(m["status"], "white")
            status_padded = m["status"].ljust(16)
            styled_status = click.style(status_padded, fg=status_color)
            goal_short = m["goal"][:50]
            click.echo(
                f"  {m['id']}  {styled_status}  "
                f"{m['total_attempts']} attempts  "
                f"{goal_short}"
            )


# ── export command ──

# Internal files that should not be exported
_EXPORT_EXCLUDE = {
    ".git",
    "mission.db",
    "mission.db-journal",
    "mission.db-wal",
    "mission.db-shm",
    "__pycache__",
    "MISSION.md",
    "ACCEPTANCE.md",
    "AUTOMISSION.md",
    "verify.sh",
    "skills",
    "CLAUDE.md",
    "AGENTS.md",
    "GEMINI.md",
    "worktrees",
    "events.jsonl",
    "mission.pid",
}


def _is_metadata_file(path: str) -> bool:
    """Check if a file path is automission internal metadata."""
    top_level = path.split("/")[0]
    return top_level in _EXPORT_EXCLUDE


@cli.command()
@click.argument("mission_id")
@click.option(
    "--output",
    "-o",
    required=True,
    type=click.Path(),
    help="Target directory to export to",
)
@click.option("--force", is_flag=True, help="Overwrite existing target directory")
def export(mission_id: str, output: str, force: bool) -> None:
    """Export mission workspace to a directory."""
    ws = _find_mission_workspace(mission_id)
    if not ws:
        click.echo(f"Mission {mission_id} not found.")
        sys.exit(1)

    target = Path(output)
    if target.exists():
        if not force:
            click.echo(
                f"Target directory already exists: {target}\nUse --force to overwrite."
            )
            sys.exit(1)
        shutil.rmtree(target)

    target.mkdir(parents=True, exist_ok=True)

    copied = 0
    for item in ws.rglob("*"):
        # Skip excluded top-level entries and their children
        rel = item.relative_to(ws)
        if rel.parts[0] in _EXPORT_EXCLUDE:
            continue
        if item.is_file():
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
            copied += 1

    click.echo(f"Exported {copied} files to {target}")


# ── resume command ──


@cli.command()
@click.argument("mission_id")
@click.option("--detach", is_flag=True, help="Start mission and return immediately")
def resume(mission_id, detach):
    """Resume a stopped or failed mission."""
    from automission.daemon import is_executor_alive, spawn_executor
    from automission.executor import reconcile_stale_state

    ws = _find_mission_workspace(mission_id)
    if not ws:
        click.echo(f"Mission {mission_id} not found.")
        return

    with Ledger(ws / "mission.db") as ledger:
        m = ledger.get_mission(mission_id)
        if not m:
            click.echo(f"Mission {mission_id} not found.")
            return

        if m["status"] == MissionOutcome.COMPLETED:
            click.echo(f"Mission {mission_id} is already completed.")
            return

    if is_executor_alive(ws, mission_id):
        click.echo(f"Mission {mission_id} is already running.")
        click.echo(f"  automission attach {mission_id}   — reconnect live view")
        return

    reconcile_stale_state(ws, mission_id)
    click.echo(f"Resuming mission {mission_id}...")
    click.echo(f"Workspace: {ws}")

    spawn_executor(ws, mission_id)

    if detach:
        click.echo(f"Mission {mission_id} resumed in background.")
        click.echo(f"  automission attach {mission_id}   — reconnect live view")
        click.echo(f"  automission stop {mission_id}     — terminate mission")
        return

    _attach_live_view(ws, mission_id)

    # Read final state from ledger
    outcome = MissionOutcome.FAILED
    try:
        with Ledger(ws / "mission.db") as ledger:
            m = ledger.get_mission(mission_id)
            if m:
                outcome = m["status"]
    except Exception:
        logger.warning("Could not load mission status from ledger")

    exit_code = MissionOutcome.EXIT_CODES.get(outcome, 1)
    passed = outcome == MissionOutcome.COMPLETED

    if passed:
        click.secho(f"\nMission {mission_id} completed successfully!", fg="green")
    elif outcome == MissionOutcome.CANCELLED:
        click.secho(f"\nMission {mission_id} cancelled.", fg="yellow")
    elif outcome == MissionOutcome.RESOURCE_LIMIT:
        click.secho(
            f"\nMission {mission_id} stopped: resource limit exceeded.", fg="yellow"
        )
    else:
        click.secho(f"\nMission {mission_id} did not pass verification.", fg="yellow")

    sys.exit(exit_code)
