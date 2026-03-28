"""Tests for skill vendoring."""

import json

import pytest

from automission.skills import vendor_skills, load_skill_contents


@pytest.fixture
def skills_dir(tmp_path):
    """Create sample skill files."""
    d = tmp_path / "user_skills"
    d.mkdir()
    (d / "code-quality.md").write_text("# Code Quality\nWrite clean code.\n")
    (d / "testing.md").write_text("# Testing\nTest everything.\n")
    return d


def test_vendor_skills_from_local_files(tmp_path, skills_dir):
    target = tmp_path / "mission" / "skills"
    sources = [
        str(skills_dir / "code-quality.md"),
        str(skills_dir / "testing.md"),
    ]
    manifest = vendor_skills(sources, target)

    assert (target / "code-quality.md").exists()
    assert (target / "testing.md").exists()
    assert (target / "manifest.json").exists()

    assert len(manifest.skills) == 2
    assert manifest.skills[0].name == "code-quality"
    assert manifest.skills[0].source.startswith("local:")
    assert manifest.skills[0].hash.startswith("sha256:")


def test_vendor_skills_empty(tmp_path):
    target = tmp_path / "mission" / "skills"
    manifest = vendor_skills([], target)
    assert manifest.skills == []
    assert not target.exists()


def test_load_skill_contents(tmp_path, skills_dir):
    target = tmp_path / "mission" / "skills"
    sources = [str(skills_dir / "code-quality.md")]
    vendor_skills(sources, target)

    contents = load_skill_contents(target)
    assert len(contents) == 1
    assert "Code Quality" in contents[0]


def test_manifest_json_valid(tmp_path, skills_dir):
    target = tmp_path / "mission" / "skills"
    vendor_skills([str(skills_dir / "code-quality.md")], target)

    raw = (target / "manifest.json").read_text()
    data = json.loads(raw)
    assert "skills" in data
    assert len(data["skills"]) == 1
