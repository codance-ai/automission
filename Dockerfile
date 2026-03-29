FROM python:3.12-slim

# System deps
RUN apt-get update && \
    apt-get install -y --no-install-recommends git bash curl && \
    rm -rf /var/lib/apt/lists/*

# Node.js (required for Codex and Gemini CLIs)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user (claude --dangerously-skip-permissions refuses root)
RUN useradd -m -s /bin/bash agent
USER agent
ENV HOME=/home/agent
ENV PATH="/home/agent/.local/bin:/home/agent/.npm-global/bin:$PATH"

# Configure npm global prefix for non-root user
RUN mkdir -p /home/agent/.npm-global && \
    npm config set prefix /home/agent/.npm-global

# Install Claude Code CLI (native binary — script requires bash)
RUN curl -fsSL https://claude.ai/install.sh | bash

# Install Codex CLI
RUN npm install -g @openai/codex

# Install Gemini CLI
RUN npm install -g @google/gemini-cli

# Pre-create CLI config dirs so tools don't fail on first write
RUN mkdir -p $HOME/.gemini $HOME/.codex

# Install pytest (for verify.sh that runs tests)
RUN pip install --no-cache-dir --user pytest

# Trust any mounted directory (agent uses /workspace, verifier uses random paths)
RUN git config --global --add safe.directory '*'

WORKDIR /workspace
