# Dockerfile for gluon-agent
# Multi-stage build: Stage 1 builds web-ui, Stage 2 sets up Python runtime

# ========== Stage 1: Build web-ui ==========
FROM oven/bun:1 AS web-builder

WORKDIR /app

# Accept version info as build arguments (passed from build script)
ARG VITE_APP_VERSION=dev
ARG VITE_APP_FULL_VERSION=development
ARG VITE_APP_BUILD_TIME

# Create version.env file from build arguments
RUN echo "VITE_APP_VERSION=${VITE_APP_VERSION}" > /tmp/version.env && \
    echo "VITE_APP_FULL_VERSION=${VITE_APP_FULL_VERSION}" >> /tmp/version.env && \
    echo "VITE_APP_BUILD_TIME=${VITE_APP_BUILD_TIME:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}" >> /tmp/version.env && \
    cat /tmp/version.env

# Copy web-ui source and create target directory structure
COPY web-ui/package.json web-ui/bun.lock* web-ui/
RUN cd web-ui && bun install --frozen-lockfile

# Copy rest of web-ui source
COPY web-ui/ web-ui/

# Create the output directory (vite outputs to ../src/gluon/web/dist relative to web-ui)
RUN mkdir -p src/gluon/web/dist

# Build the frontend with version info
RUN set -a && . /tmp/version.env && set +a && cd web-ui && bun run build

# Export version for next stage
RUN cp /tmp/version.env /app/version.env

# ========== Stage 2: Python runtime ==========
FROM python:3.12-slim-trixie@sha256:2259debdc6af85626058a15a7bf44ad6a68b8c98c9ae82a02cf5cdb92f096d47

# Layer 1: System dependencies (rarely change - cached for weeks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    curl \
    bash \
    build-essential \
    gnupg \
    unzip \
    openssh-client \
    bubblewrap \
    sqlite3 \
    redis-server \
    && rm -rf /var/lib/apt/lists/*

# Layer 1b: gosu for dropping root privileges in entrypoint
# Allows PUID/PGID pattern — container starts as root, adjusts UID/GID, then drops to gluon user
RUN apt-get update && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/* \
    && gosu nobody true

# Layer 2: GitHub CLI (rarely changes)
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install -y gh=2.102.1 \
    && rm -rf /var/lib/apt/lists/*

# Layer 3: Node.js v24 LTS (rarely changes)
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Layer 4: Bun JavaScript runtime (rarely changes)
RUN curl -fsSL https://bun.sh/install | bash \
    && mv /root/.bun /opt/bun \
    && ln -s /opt/bun/bin/bun /usr/local/bin/bun \
    && ln -s /usr/local/bin/bun /usr/local/bin/bunx

# Layer 5: Claude Code CLI — no longer needed as a standalone install.
# The claude-agent-sdk bundles its own CLI binary and uses it by default.
# RUN curl -fsSL https://claude.ai/install.sh | bash \
#     && cp -L /root/.local/bin/claude /usr/local/bin/claude \
#     && chmod 755 /usr/local/bin/claude

# Layer 5b: Chromium browser + fonts for agent-browser/Playwright
# Chrome for Testing has no Linux ARM64 builds, so install system Chromium instead.
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    fonts-liberation \
    fonts-noto-color-emoji \
    fonts-noto-cjk \
    fonts-dejavu-core \
    fonts-freefont-ttf \
    fontconfig \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

# Layer 5c: agent-browser CLI (global install only)
# Browser binaries downloaded later as gluon user
RUN npm install -g agent-browser

# Layer 5d: Vercel CLI is installed from its scoped lock after npm below.

# Layer 5e: Biome linter/formatter (global fallback for bind-mounted node_modules)
RUN npm install -g @biomejs/biome

# Layer 6: Create non-root user and directories (rarely changes)
RUN groupadd -g 1000 gluon && \
    useradd -m -u 1000 -g gluon -s /bin/bash gluon && \
    mkdir -p /home/gluon/.claude \
             /home/gluon/.gluon \
             /home/gluon/.gluon/logs \
             /home/gluon/.gluon/images \
             /home/gluon/workspaces \
             /home/gluon/.cache/gluon \
             /home/gluon/.ssh \
             /app && \
    chown -R gluon:gluon /home/gluon /app && \
    ssh-keyscan -t ed25519,rsa github.com >> /home/gluon/.ssh/known_hosts && \
    chown gluon:gluon /home/gluon/.ssh/known_hosts && \
    chmod 644 /home/gluon/.ssh/known_hosts

# Layer 6b: Point agent-browser at system Chromium (no download needed on ARM64)
ENV AGENT_BROWSER_EXECUTABLE_PATH=/usr/bin/chromium

WORKDIR /app

# Layer 7: Python tooling (rarely changes)
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Layer 7b: Patch vulnerable dependencies bundled with Node 24's npm (same major)
RUN npm install -g npm@11.21.1

# Keep Vercel's reviewed transitive patches isolated from other global tools.
# Preserve both CLI aliases and all builders/platform optional dependencies.
COPY tooling/vercel/package.json tooling/vercel/package-lock.json /opt/gluon-vercel/
RUN npm ci --prefix /opt/gluon-vercel --include=optional --registry=https://registry.npmjs.org/ --userconfig=/dev/null --cache=/tmp/gluon-vercel-npm-cache && \
    ln -s /opt/gluon-vercel/node_modules/.bin/vercel /usr/bin/vercel && \
    ln -s /opt/gluon-vercel/node_modules/.bin/vc /usr/bin/vc

# Layer 7c: Use the same pinned dependency installer as CI
COPY --from=ghcr.io/astral-sh/uv:0.13.3 /uv /uvx /usr/local/bin/
ENV UV_PROJECT_ENVIRONMENT=/home/gluon/.venv
ENV UV_PYTHON_DOWNLOADS=never
ENV PATH="/home/gluon/.venv/bin:/home/gluon/.local/bin:$PATH"

# Layer 8: Copy dependency metadata first for dependency caching
# Dependencies change less frequently than source code
COPY --chown=gluon:gluon pyproject.toml uv.lock README.md LICENSE* ./

# Layer 9: Install Python dependencies BEFORE source copy
# This layer is cached as long as the manifest and lock don't change.
# Keep all runtime extras, exclude dev tooling, and fail on stale lock metadata.
USER gluon
RUN uv sync --locked --extra all --no-dev --no-install-project --no-cache

# Layer 10: Copy Python source (changes with backend code)
USER root
COPY --chown=gluon:gluon src/ src/
COPY --chown=gluon:gluon docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Layer 11: Re-install in editable mode (fast - deps already cached)
USER gluon
RUN uv sync --locked --extra all --no-dev --no-cache

# Layer 11b: Symlink bundled Claude CLI onto PATH
# claude-agent-sdk bundles its own CLI binary but doesn't add it to PATH
USER root
RUN CLAUDE_BIN=$(find /home/gluon -path "*/claude_agent_sdk/_bundled/claude" -type f 2>/dev/null | head -1) && \
    if [ -n "$CLAUDE_BIN" ]; then \
        ln -sf "$CLAUDE_BIN" /usr/local/bin/claude && \
        chmod 755 /usr/local/bin/claude; \
    fi

# Layer 12: Copy built web-ui LAST (changes most frequently with frontend work)
# Using --link to avoid invalidating subsequent layers
COPY --from=web-builder --chown=gluon:gluon /app/src/gluon/web/dist/ src/gluon/web/dist/

# Layer 13: Version info LAST (changes every build but doesn't invalidate anything important)
USER root
COPY --from=web-builder /app/version.env /tmp/version.env
RUN . /tmp/version.env && \
    echo "GLUON_VERSION=${VITE_APP_VERSION}" >> /etc/environment && \
    echo "GLUON_FULL_VERSION=${VITE_APP_FULL_VERSION}" >> /etc/environment && \
    echo "GLUON_BUILD_TIME=${VITE_APP_BUILD_TIME}" >> /etc/environment

# Final setup — entrypoint runs as root to adjust UID/GID, then drops to gluon via gosu
USER root
ENV HOME=/home/gluon
ENV GLUON_DATA_DIR=$HOME/.gluon

# Entrypoint adjusts UID/GID from PUID/PGID env vars, then drops privileges
ENTRYPOINT ["docker-entrypoint.sh"]

# Default command (can be overridden by docker-compose or docker run)
CMD ["gluon", "--help"]
