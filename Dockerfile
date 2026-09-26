# PanWatch Dockerfile
# Multi-stage build to keep the final image small

# ===== Stage 1: frontend build =====
FROM node:24.14.0-alpine AS frontend-builder

WORKDIR /app/frontend

# Enable and pin pnpm so image builds don't drift with a global npm install
RUN corepack enable && corepack prepare pnpm@9.15.9 --activate

# Copy dependency files
COPY frontend/package.json frontend/pnpm-lock.yaml ./

# Install dependencies
RUN pnpm install --frozen-lockfile

# Copy the source and build
COPY frontend/ ./
RUN pnpm build


# ===== Stage 2: Python runtime =====
FROM python:3.11-slim

# Version (passed at build time)
ARG VERSION=dev

WORKDIR /app

# System dependencies
# - tzdata: zoneinfo time zones
# - git: requirements.txt installs tradingagents from a git+https URL
# - fonts-dejavu-core: a font with the rupee sign (₹) for PDF export
# - pango/cairo/gdk-pixbuf/ffi/fontconfig: WeasyPrint PDF export
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    git \
    fonts-dejavu-core \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libcairo-gobject2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    libfontconfig1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -fv

# Copy dependency files
COPY requirements.txt ./

# Copy the local packages in this repo (-e ./packages/marketdata in requirements.txt needs them first)
COPY packages/ ./packages/

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the backend code
COPY src/ ./src/
COPY server.py ./
COPY prompts/ ./prompts/

# Write the version
RUN echo "${VERSION}" > VERSION

# Copy the static files from the frontend build stage
COPY --from=frontend-builder /app/frontend/dist ./static/

# Create the data directory
RUN mkdir -p /app/data

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/app/data
ENV DOCKER=1

# Default time zone (override with -e TZ=... on docker run)
ENV TZ=Asia/Kolkata

# Expose the port (kept at 8000 so existing users' upgrades aren't affected)
EXPOSE 8000

# Health check (using Python)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

# Start command
CMD ["python", "server.py"]
