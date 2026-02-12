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
FROM python:3.12-slim-bookworm

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
    && rm -rf /var/lib/apt/lists/*

# Layer 2: GitHub CLI (rarely changes)
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

# Layer 3: Node.js v24 LTS (rarely changes)
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Layer 4: Bun JavaScript runtime (rarely changes)
RUN curl -fsSL https://bun.sh/install | bash \
    && mv /root/.bun /opt/bun \
    && ln -s /opt/bun/bin/bun /usr/local/bin/bun \
    && ln -s /opt/bun/bin/bunx /usr/local/bin/bunx

# Layer 5: Claude Code CLI (install as root, copy to shared PATH)
RUN curl -fsSL https://claude.ai/install.sh | bash \
    && cp -L /root/.local/bin/claude /usr/local/bin/claude \
    && chmod 755 /usr/local/bin/claude

# Layer 5b: Chromium system dependencies for agent-browser/Playwright
# Required for headless browser automation in Docker
RUN apt-get update && apt-get install -y --no-install-recommends \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    libxshmfence1 \
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

# Layer 6: Create non-root user and directories (rarely changes)
RUN groupadd -g 1000 gluon && \
    useradd -m -u 1000 -g gluon -s /bin/bash gluon && \
    mkdir -p /home/gluon/.claude \
             /home/gluon/workspaces \
             /home/gluon/.cache/gluon \
             /home/gluon/.ssh \
             /app && \
    chown -R gluon:gluon /home/gluon /app && \
    ssh-keyscan -t ed25519,rsa github.com >> /home/gluon/.ssh/known_hosts && \
    chown gluon:gluon /home/gluon/.ssh/known_hosts && \
    chmod 644 /home/gluon/.ssh/known_hosts

# Layer 6b: Download Chromium browser for agent-browser (as gluon user)
# Placed early to cache browser download - only re-runs if user/npm layers change
USER gluon
RUN agent-browser install
USER root

WORKDIR /app

# Layer 7: Python tooling (rarely changes)
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Layer 8: Copy ONLY pyproject.toml first for dependency caching
# Dependencies change less frequently than source code
COPY --chown=gluon:gluon pyproject.toml README.md LICENSE* ./

# Layer 9: Install Python dependencies BEFORE source copy
# This layer is cached as long as pyproject.toml doesn't change
USER gluon
RUN pip install --no-cache-dir -e '.[all]' --no-deps && \
    pip install --no-cache-dir $(pip show gluon-agent 2>/dev/null | grep Requires | cut -d: -f2 | tr ',' '\n' | xargs) 2>/dev/null || \
    pip install --no-cache-dir anyio pydantic python-dotenv redis rich typer fastapi uvicorn python-telegram-bot discord.py claude-agent-sdk

# Layer 10: Copy Python source (changes with backend code)
USER root
COPY --chown=gluon:gluon src/ src/
COPY --chown=gluon:gluon docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Layer 11: Re-install in editable mode (fast - deps already cached)
USER gluon
RUN pip install --no-cache-dir -e '.[all]'

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

# Final setup
USER gluon
ENV PATH="/home/gluon/.local/bin:$PATH"
ENV HOME=/home/gluon
ENV GLUON_DATA_DIR=$HOME/.gluon

# Entrypoint registers MCP servers from mounted .mcp.json on startup
ENTRYPOINT ["docker-entrypoint.sh"]

# Default command (can be overridden by docker-compose or docker run)
CMD ["gluon", "--help"]
