# Contributing to automission

Thanks for your interest in contributing!

## Development Setup

```bash
git clone https://github.com/codance-ai/automission.git
cd automission
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Running Tests

```bash
# Unit tests
pytest

# With coverage
pytest --cov=automission

# E2E tests (require Docker + API keys)
pytest -m e2e
```

## Code Quality

```bash
ruff check src/ tests/
ruff format src/ tests/
mypy src/
```

## Pull Requests

1. Fork the repo and create a feature branch
2. Make your changes with tests
3. Ensure `ruff check`, `ruff format --check`, and `pytest` all pass
4. Open a PR with a clear description of what and why

## Reporting Issues

Open an issue at https://github.com/codance-ai/automission/issues with:
- What you expected vs what happened
- Steps to reproduce
- automission version (`automission --version`) and Python version
