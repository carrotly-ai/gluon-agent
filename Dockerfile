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
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

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

# Install Python dependencies as root first
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Switch to non-root user
USER gluon

# Install gluon-agent package
RUN pip install --no-cache-dir -e .

# Add to PATH
ENV PATH="/home/gluon/.local/bin:$PATH"
ENV HOME=/home/gluon
ENV GLUON_DATA_DIR=$HOME/.gluon

# Use array syntax for proper signal handling
ENTRYPOINT ["gluon"]
CMD ["--help"]
