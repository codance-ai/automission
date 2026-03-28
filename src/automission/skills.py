"""Skill vendoring — copy skills into mission workspace."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from automission.models import SkillManifest, SkillManifestEntry


def vendor_skills(sources: list[str], target_dir: Path) -> SkillManifest:
    """Copy skill files into target directory with manifest.

    Args:
        sources: List of skill source paths (local files for M1).
        target_dir: Where to copy skills (e.g., workspace/skills/).

    Returns:
        SkillManifest with entries for each vendored skill.
    """
    if not sources:
        return SkillManifest()

    target_dir.mkdir(parents=True, exist_ok=True)
    entries: list[SkillManifestEntry] = []

    for source in sources:
        source_path = Path(source)
        if not source_path.exists():
            raise FileNotFoundError(f"Skill file not found: {source}")

        content = source_path.read_text()
        content_hash = "sha256:" + hashlib.sha256(content.encode()).hexdigest()[:16]

        # Copy to target (detect filename collision)
        dest = target_dir / source_path.name
        if dest.exists():
            raise ValueError(
                f"Skill filename collision: '{source_path.name}' already exists in {target_dir}. "
                f"Two skill sources share the same filename."
            )
        dest.write_text(content)

        entries.append(
            SkillManifestEntry(
                name=source_path.stem,
                source=f"local:{source}",
                hash=content_hash,
            )
        )

    manifest = SkillManifest(skills=entries)
    (target_dir / "manifest.json").write_text(manifest.to_json())
    return manifest


def load_skill_contents(skills_dir: Path) -> list[str]:
    """Load vendored skill file contents (excluding manifest)."""
    if not skills_dir.exists():
        return []

    contents = []
    manifest_path = skills_dir / "manifest.json"
    if manifest_path.exists():
        data = json.loads(manifest_path.read_text())
        for entry in data.get("skills", []):
            skill_file = skills_dir / (entry["name"] + ".md")
            if skill_file.exists():
                contents.append(skill_file.read_text())
    return contents
