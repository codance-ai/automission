"""Tests for ACCEPTANCE.md parser."""

from automission.acceptance import parse_acceptance_md


SIMPLE_MD = """\
# Acceptance Criteria

## basic_operations
All 4 basic arithmetic operations return correct results.

- add(a, b) returns the sum of a and b
- subtract(a, b) returns the difference of a and b
- multiply(a, b) returns the product of a and b
- divide(a, b) returns the quotient of a divided by b

## edge_cases
Edge cases are handled correctly.

- divide(a, 0) raises ValueError
- all operations handle negative numbers correctly
"""

WITH_DEPS_MD = """\
# Acceptance Criteria

## auth_schema
User table and auth DB schema.

- Users table exists with email and password_hash columns
- Auth tokens table exists

## api_endpoints
CRUD endpoints with auth.

Depends on: auth_schema

- POST /todos creates a todo
- GET /todos returns user's todos
"""

EMPTY_MD = """\
# Acceptance Criteria
"""


def test_parse_simple():
    groups = parse_acceptance_md(SIMPLE_MD)
    assert len(groups) == 2

    basic = groups[0]
    assert basic.id == "basic_operations"
    assert basic.name == "basic_operations"
    assert basic.depends_on == []
    assert len(basic.criteria) == 4
    assert basic.criteria[0].text == "add(a, b) returns the sum of a and b"
    assert basic.criteria[0].group_id == "basic_operations"
    assert basic.criteria[0].required is True

    edge = groups[1]
    assert edge.id == "edge_cases"
    assert len(edge.criteria) == 2


def test_parse_with_depends_on():
    groups = parse_acceptance_md(WITH_DEPS_MD)
    assert len(groups) == 2

    auth = groups[0]
    assert auth.depends_on == []
    assert len(auth.criteria) == 2

    api = groups[1]
    assert api.depends_on == ["auth_schema"]
    assert len(api.criteria) == 2


def test_parse_empty():
    groups = parse_acceptance_md(EMPTY_MD)
    assert groups == []


def test_criterion_ids_are_unique():
    groups = parse_acceptance_md(SIMPLE_MD)
    all_ids = [c.id for g in groups for c in g.criteria]
    assert len(all_ids) == len(set(all_ids))
