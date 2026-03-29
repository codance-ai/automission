"""Tests for workspace initialization."""

import subprocess
from pathlib import Path

import pytest

from automission.workspace import create_mission
from automission.backend.mock import MockBackend
from automission.db import Ledger


@pytest.fixture
def fixture_dir():
    return Path(__file__).parent / "fixtures" / "m1-calculator"


class TestCreateMission:
    def test_creates_workspace_directory(self, tmp_path, fixture_dir):
        ws = tmp_path / "missions" / "test-001"
        result = create_mission(
            mission_id="test-001",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=MockBackend(),
            workspace_dir=ws,
        )
        assert result.exists()
        assert (result / "MISSION.md").exists()
        assert (result / "ACCEPTANCE.md").exists()
        assert (result / "verify.sh").exists()
        assert (result / "mission.db").exists()
        assert (result / "AUTOMISSION.md").exists()

    def test_mission_md_contains_goal(self, tmp_path, fixture_dir):
        ws = tmp_path / "missions" / "test-001"
        result = create_mission(
            mission_id="test-001",
            goal="Build a calculator with four operations",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=MockBackend(),
            workspace_dir=ws,
        )
        content = (result / "MISSION.md").read_text()
        assert "Build a calculator with four operations" in content

    def test_git_repo_initialized(self, tmp_path, fixture_dir):
        ws = tmp_path / "missions" / "test-001"
        result = create_mission(
            mission_id="test-001",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=MockBackend(),
            workspace_dir=ws,
        )
        assert (result / ".git").exists()
        log = subprocess.run(
            ["git", "log", "--oneline"], cwd=result, capture_output=True, text=True
        )
        assert "baseline" in log.stdout.lower() or log.returncode == 0

    def test_acceptance_groups_in_db(self, tmp_path, fixture_dir):
        ws = tmp_path / "missions" / "test-001"
        result = create_mission(
            mission_id="test-001",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=MockBackend(),
            workspace_dir=ws,
        )
        ledger = Ledger(result / "mission.db")
        groups = ledger.get_acceptance_groups("test-001")
        assert len(groups) == 2
        assert groups[0].id == "basic_operations"
        assert groups[1].id == "edge_cases"
        ledger.close()

    def test_mission_recorded_in_db(self, tmp_path, fixture_dir):
        ws = tmp_path / "missions" / "test-001"
        result = create_mission(
            mission_id="test-001",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=MockBackend(),
            workspace_dir=ws,
        )
        ledger = Ledger(result / "mission.db")
        mission = ledger.get_mission("test-001")
        assert mission is not None
        assert mission["goal"] == "Build calculator"
        assert mission["status"] == "running"
        ledger.close()

    def test_verify_sh_is_executable(self, tmp_path, fixture_dir):
        ws = tmp_path / "missions" / "test-001"
        result = create_mission(
            mission_id="test-001",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=MockBackend(),
            workspace_dir=ws,
        )
        import os

        assert os.access(result / "verify.sh", os.X_OK)

    def test_gitignore_created(self, tmp_path, fixture_dir):
        ws = tmp_path / "missions" / "test-001"
        result = create_mission(
            mission_id="test-001",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=MockBackend(),
            workspace_dir=ws,
        )
        gitignore = result / ".gitignore"
        assert gitignore.exists()
        content = gitignore.read_text()
        assert "__pycache__/" in content
        assert "node_modules/" in content
        assert "*.pyc" in content

    def test_gitignore_overridden_by_init_files(self, tmp_path, fixture_dir):
        init_dir = tmp_path / "init"
        init_dir.mkdir()
        custom_gitignore = "# custom\n*.log\n"
        (init_dir / ".gitignore").write_text(custom_gitignore)
        ws = tmp_path / "missions" / "test-override"
        result = create_mission(
            mission_id="test-override",
            goal="Build something",
            backend=MockBackend(),
            workspace_dir=ws,
            init_files_dir=init_dir,
        )
        content = (result / ".gitignore").read_text()
        assert content == custom_gitignore

    def test_copies_fixture_workspace_files(self, tmp_path, fixture_dir):
        ws = tmp_path / "missions" / "test-001"
        result = create_mission(
            mission_id="test-001",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=MockBackend(),
            workspace_dir=ws,
            init_files_dir=fixture_dir / "workspace",
        )
        assert (result / "src" / "tests" / "test_calc.py").exists()

    def test_backend_prepare_workspace_called(self, tmp_path, fixture_dir):
        ws = tmp_path / "missions" / "test-001"
        backend = MockBackend()
        create_mission(
            mission_id="test-001",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=backend,
            workspace_dir=ws,
        )
        assert (ws / "AUTOMISSION.md").exists()

    def test_acceptance_content_creates_file(self, tmp_path):
        ws = tmp_path / "missions" / "test-content"
        acceptance_md = "# Acceptance Criteria\n\n## Setup\n\n- basic setup works\n"
        result = create_mission(
            mission_id="test-content",
            goal="Build something",
            acceptance_content=acceptance_md,
            backend=MockBackend(),
            workspace_dir=ws,
        )
        assert (result / "ACCEPTANCE.md").exists()
        assert (result / "ACCEPTANCE.md").read_text() == acceptance_md

    def test_verify_content_creates_executable(self, tmp_path):
        ws = tmp_path / "missions" / "test-verify"
        verify_sh = "#!/usr/bin/env bash\npytest tests/ -v\n"
        result = create_mission(
            mission_id="test-verify",
            goal="Build something",
            verify_content=verify_sh,
            backend=MockBackend(),
            workspace_dir=ws,
        )
        assert (result / "verify.sh").exists()
        assert (result / "verify.sh").read_text() == verify_sh
        import os

        assert os.access(result / "verify.sh", os.X_OK)

    def test_mission_content_overrides_default(self, tmp_path):
        ws = tmp_path / "missions" / "test-mission"
        custom = (
            "# Mission\n\nCustom expanded goal.\n\n## Constraints\n\n- Must be fast\n"
        )
        result = create_mission(
            mission_id="test-mission",
            goal="Build something",
            mission_content=custom,
            backend=MockBackend(),
            workspace_dir=ws,
        )
        content = (result / "MISSION.md").read_text()
        assert "Custom expanded goal" in content
        assert "Build something" not in content

    def test_content_wins_over_path(self, tmp_path, fixture_dir):
        ws = tmp_path / "missions" / "test-precedence"
        custom_acceptance = "# Acceptance Criteria\n\n## Custom\n\n- custom criterion\n"
        result = create_mission(
            mission_id="test-precedence",
            goal="Build something",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            acceptance_content=custom_acceptance,
            backend=MockBackend(),
            workspace_dir=ws,
        )
        content = (result / "ACCEPTANCE.md").read_text()
        assert "custom criterion" in content

    def test_with_skills(self, tmp_path, fixture_dir):
        skill_file = tmp_path / "my-skill.md"
        skill_file.write_text("# My Skill\nDo awesome things.\n")
        ws = tmp_path / "missions" / "test-001"
        result = create_mission(
            mission_id="test-001",
            goal="Build calculator",
            acceptance_path=fixture_dir / "ACCEPTANCE.md",
            verify_path=fixture_dir / "verify.sh",
            backend=MockBackend(),
            workspace_dir=ws,
            skill_sources=[str(skill_file)],
        )
        assert (result / "skills" / "my-skill.md").exists()
        assert (result / "skills" / "manifest.json").exists()
        content = (result / "AUTOMISSION.md").read_text()
        assert "My Skill" in content
