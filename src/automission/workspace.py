"""Mission creation and workspace initialization."""

from __future__ import annotations

import shutil
import stat
import subprocess
import uuid
from pathlib import Path

from automission.acceptance import parse_acceptance_md
from automission.backend.protocol import AgentBackend
from automission.db import Ledger
from automission.models import StableContext
from automission.skills import load_skill_contents, vendor_skills

DEFAULT_BASE_DIR = Path.home() / ".automission" / "missions"


def create_mission(
    mission_id: str | None = None,
    goal: str = "",
    acceptance_path: Path | None = None,
    acceptance_content: str | None = None,
    verify_path: Path | None = None,
    verify_content: str | None = None,
    mission_content: str | None = None,
    backend: AgentBackend | None = None,
    workspace_dir: Path | None = None,
    skill_sources: list[str] | None = None,
    init_files_dir: Path | None = None,
    agents: int = 1,
    max_iterations: int = 20,
    max_cost: float = 10.0,
    timeout: int = 3600,
    backend_name: str = "claude",
    docker_image: str = "ghcr.io/codance-ai/automission:latest",
    model: str = "claude-sonnet-4-6",
) -> Path:
    """Create and initialize a mission workspace.

    Returns the workspace directory path.
    """
    if mission_id is None:
        mission_id = uuid.uuid4().hex[:12]

    if workspace_dir is None:
        workspace_dir = DEFAULT_BASE_DIR / mission_id

    workspace_dir.mkdir(parents=True, exist_ok=True)

    # 1. Init git repo
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=workspace_dir,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "automission@local"],
        cwd=workspace_dir,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "automission"],
        cwd=workspace_dir,
        capture_output=True,
    )

    # 2. Copy initial files from fixture/template
    if init_files_dir and init_files_dir.exists():
        _copy_tree(init_files_dir, workspace_dir)

    # 3. Write MISSION.md (content wins over default)
    if mission_content:
        (workspace_dir / "MISSION.md").write_text(mission_content)
    else:
        (workspace_dir / "MISSION.md").write_text(f"# Mission\n\n{goal}\n")

    # 4. Write ACCEPTANCE.md (content wins over path)
    if acceptance_content:
        (workspace_dir / "ACCEPTANCE.md").write_text(acceptance_content)
    elif acceptance_path and acceptance_path.exists():
        shutil.copy2(acceptance_path, workspace_dir / "ACCEPTANCE.md")

    # 5. Write verify.sh (content wins over path)
    if verify_content:
        dest = workspace_dir / "verify.sh"
        dest.write_text(verify_content)
        dest.chmod(dest.stat().st_mode | stat.S_IEXEC)
    elif verify_path and verify_path.exists():
        dest = workspace_dir / "verify.sh"
        shutil.copy2(verify_path, dest)
        dest.chmod(dest.stat().st_mode | stat.S_IEXEC)

    # 6. Vendor skills
    skill_contents: list[str] = []
    if skill_sources:
        vendor_skills(skill_sources, workspace_dir / "skills")
        skill_contents = load_skill_contents(workspace_dir / "skills")

    # 7. Create SQLite DB and populate
    ledger = Ledger(workspace_dir / "mission.db")
    ledger.create_mission(
        mission_id=mission_id,
        goal=goal,
        backend=backend_name,
        model=model,
        agents=agents,
        max_iterations=max_iterations,
        max_cost=max_cost,
        timeout=timeout,
        docker_image=docker_image,
    )

    # 8. Parse acceptance and store groups
    if (workspace_dir / "ACCEPTANCE.md").exists():
        acceptance_text = (workspace_dir / "ACCEPTANCE.md").read_text()
        groups = parse_acceptance_md(acceptance_text)
        ledger.store_acceptance_groups(mission_id, groups)

    ledger.close()

    # 9. Backend: write AUTOMISSION.md + native instruction file
    if backend is not None:
        stable = StableContext(
            goal=goal,
            skills=skill_contents,
            rules=["Do not modify verify.sh"],
        )
        backend.prepare_workspace(workspace_dir, stable)

    # 10. Baseline commit
    subprocess.run(["git", "add", "-A"], cwd=workspace_dir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "automission: baseline"],
        cwd=workspace_dir,
        capture_output=True,
    )

    return workspace_dir


def _copy_tree(src: Path, dst: Path) -> None:
    """Recursively copy directory contents (not the directory itself)."""
    for item in src.rglob("*"):
        if item.is_file():
            rel = item.relative_to(src)
            dest = dst / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest)
