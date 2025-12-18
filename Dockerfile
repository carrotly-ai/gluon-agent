# Dockerfile for gluon-agent
# Single-stage build using python:3.12-slim with Claude Code CLI support

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
             /app && \
    chown -R gluon:gluon /home/gluon /app

WORKDIR /app

# Copy gluon-agent source (as root first for proper permissions)
COPY --chown=gluon:gluon pyproject.toml README.md LICENSE* ./
COPY --chown=gluon:gluon src/ src/
COPY --chown=gluon:gluon docker-entrypoint.sh /usr/local/bin/

# Install Python dependencies as root first
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Make entrypoint executable
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Switch to non-root user
USER gluon

# Install gluon-agent package with all features (web, telegram, discord)
RUN pip install --no-cache-dir -e '.[all]'

# Add to PATH
ENV PATH="/home/gluon/.local/bin:$PATH"
ENV HOME=/home/gluon
ENV GLUON_DATA_DIR=$HOME/.gluon

# Entrypoint registers MCP servers from mounted .mcp.json on startup
ENTRYPOINT ["docker-entrypoint.sh"]

# Default command (can be overridden by docker-compose or docker run)
CMD ["gluon", "--help"]
