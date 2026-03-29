# Automission Development Guide

## Pre-Push Checklist

Push 前必须在本地跑完整 CI 管线，和 GitHub Actions 保持一致：

```bash
# Lint (和 CI 完全一致)
ruff check src/ tests/
ruff format --check src/ tests/

# Test (和 CI 完全一致)
pytest --cov=automission --cov-report=term-missing -m "not e2e"
```

如果没有 ruff，用 `uvx ruff` 代替。

## Merge Policy

**PR 不要立即合并，等 CI 通过后自动合并：**

```bash
gh pr merge --squash --auto
```

不要用 `gh pr merge --squash`（立即合并）——CI 没通过就合并会导致连续 fix PR。

## Fallback Policy

Fallback 需要我确认才可以用。不要静默添加 fallback 逻辑。

## Release Process

版本发布必须完成以下三步，缺一不可：

```bash
# 1. Bump version
# 修改 src/automission/__init__.py 中的 __version__

# 2. 发布到 PyPI
uv build && uv publish

# 3. 创建 GitHub Release（不要漏！）
gh release create v{VERSION} --title "v{VERSION}" --generate-notes
```

**发布前检查：**
- `__version__` 和 tag 版本号一致
- CI 在 main 上是绿的
- PyPI 和 GitHub release 版本号同步

## Architecture: Agent Workspace Isolation

Agent 工作空间使用 `git clone --local`（不是 git worktree）：
- 每个 agent 在 `mission_dir/worktrees/{agent_id}/` 有一个独立的 clone
- Clone 有完整的 `.git/` 目录，可以直接挂载进 Docker
- 同步：`git fetch origin main && git rebase origin/main`
- 合并回主干：从 mission_dir `git fetch {clone} HEAD && git merge --ff-only {sha}`
