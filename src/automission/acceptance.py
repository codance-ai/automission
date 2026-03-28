"""Parse ACCEPTANCE.md markdown into AcceptanceGroup objects."""

from __future__ import annotations

import re

from automission.models import AcceptanceGroup, Criterion


def parse_acceptance_md(text: str) -> list[AcceptanceGroup]:
    """Parse ACCEPTANCE.md format into structured groups.

    Format:
        ## group_id
        Optional description text.

        Depends on: group_a, group_b

        - criterion text
        - another criterion
    """
    groups: list[AcceptanceGroup] = []
    current_group: AcceptanceGroup | None = None
    criterion_counter = 0
    seen_group_ids: set[str] = set()

    for line in text.splitlines():
        line_stripped = line.strip()

        # New group: ## heading
        heading_match = re.match(r"^##\s+(.+)$", line_stripped)
        if heading_match:
            group_name = heading_match.group(1).strip()
            group_id = _to_snake_case(group_name)
            if group_id in seen_group_ids:
                raise ValueError(
                    f"Duplicate acceptance group ID '{group_id}' "
                    f"(from heading '{group_name}')"
                )
            seen_group_ids.add(group_id)
            criterion_counter = 0
            current_group = AcceptanceGroup(id=group_id, name=group_name)
            groups.append(current_group)
            continue

        if current_group is None:
            continue

        # Depends on: a, b
        deps_match = re.match(r"^[Dd]epends\s+on:\s*(.+)$", line_stripped)
        if deps_match:
            deps = [d.strip() for d in deps_match.group(1).split(",") if d.strip()]
            current_group.depends_on = deps
            continue

        # Criterion: - text
        criterion_match = re.match(r"^-\s+(.+)$", line_stripped)
        if criterion_match:
            criterion_counter += 1
            criterion_text = criterion_match.group(1).strip()
            criterion = Criterion(
                id=f"{current_group.id}_c{criterion_counter}",
                group_id=current_group.id,
                text=criterion_text,
            )
            current_group.criteria.append(criterion)
            continue

    return groups


def _to_snake_case(text: str) -> str:
    """Convert heading text to snake_case ID."""
    if re.match(r"^[a-z][a-z0-9_]*$", text):
        return text
    result = re.sub(r"[\s\-]+", "_", text.lower())
    result = re.sub(r"[^a-z0-9_]", "", result)
    return result
