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

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    curl \
    bash \
    build-essential \
    gnupg \
    unzip \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

# Install GitHub CLI for PR operations
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /usr/share/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update \
    && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js v24 LTS from NodeSource
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Bun JavaScript runtime
RUN curl -fsSL https://bun.sh/install | bash \
    && mv /root/.bun /opt/bun \
    && ln -s /opt/bun/bin/bun /usr/local/bin/bun \
    && ln -s /opt/bun/bin/bunx /usr/local/bin/bunx

# Install Claude Code CLI globally
RUN npm install -g @anthropic-ai/claude-code

# Create non-root user for security
RUN groupadd -g 1000 gluon && \
    useradd -m -u 1000 -g gluon -s /bin/bash gluon

# Create necessary directories
RUN mkdir -p /home/gluon/.claude \
             /home/gluon/workspaces \
             /home/gluon/.cache/gluon \
             /home/gluon/.ssh \
             /app && \
    chown -R gluon:gluon /home/gluon /app

# Pre-populate GitHub's SSH host keys to avoid verification prompts
RUN ssh-keyscan -t ed25519,rsa github.com >> /home/gluon/.ssh/known_hosts && \
    chown gluon:gluon /home/gluon/.ssh/known_hosts && \
    chmod 644 /home/gluon/.ssh/known_hosts

WORKDIR /app

# Copy version info from builder stage
COPY --from=web-builder /app/version.env /tmp/version.env

# Set version environment variables
RUN . /tmp/version.env && \
    echo "GLUON_VERSION=${VITE_APP_VERSION}" >> /etc/environment && \
    echo "GLUON_FULL_VERSION=${VITE_APP_FULL_VERSION}" >> /etc/environment && \
    echo "GLUON_BUILD_TIME=${VITE_APP_BUILD_TIME}" >> /etc/environment

# Copy gluon-agent source (as root first for proper permissions)
COPY --chown=gluon:gluon pyproject.toml README.md LICENSE* ./
COPY --chown=gluon:gluon src/ src/
COPY --chown=gluon:gluon docker-entrypoint.sh /usr/local/bin/

# Copy built web-ui from stage 1 (overwrites any pre-built dist)
COPY --from=web-builder --chown=gluon:gluon /app/src/gluon/web/dist/ src/gluon/web/dist/

# Install Python dependencies as root first
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Make entrypoint executable
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Switch to non-root user
USER gluon

# Install gluon-agent package with all features (web, telegram, discord)
RUN pip install --no-cache-dir -e '.[all]'

# Set version env vars for runtime (using shell form to expand)
# These get set from /tmp/version.env
ARG GLUON_VERSION_ARG
ARG GLUON_FULL_VERSION_ARG
ARG GLUON_BUILD_TIME_ARG

# Add to PATH and set version env vars
ENV PATH="/home/gluon/.local/bin:$PATH"
ENV HOME=/home/gluon
ENV GLUON_DATA_DIR=$HOME/.gluon

# Entrypoint registers MCP servers from mounted .mcp.json on startup
ENTRYPOINT ["docker-entrypoint.sh"]

# Default command (can be overridden by docker-compose or docker run)
CMD ["gluon", "--help"]
