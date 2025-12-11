# Multi-stage build for gluon-agent
# Base: python:3.12-slim with Claude Code CLI support

FROM python:3.12-slim-bookworm as builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Copy and install Python dependencies
COPY pyproject.toml pyproject.toml
RUN pip install --no-cache-dir --user -e .

# Final stage
FROM python:3.12-slim-bookworm

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    curl \
    bash \
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

# Copy Python packages from builder
COPY --from=builder --chown=gluon:gluon /root/.local /home/gluon/.local

# Copy gluon-agent source
COPY --chown=gluon:gluon . .

# Add gluon to PATH
ENV PATH="/home/gluon/.local/bin:$PATH"
ENV HOME=/home/gluon
ENV GLUON_DATA_DIR=$HOME/.gluon

# Switch to non-root user
USER gluon

# Use array syntax for proper signal handling
ENTRYPOINT ["gluon"]
CMD ["--help"]
