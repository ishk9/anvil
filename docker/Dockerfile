# Anvil runtime image.
# Single stage keeps it simple; the heavy dependency is OpenCASCADE (via build123d's
# OCP wheels), which ships as manylinux wheels but needs a few shared X/GL libs present
# at runtime even for headless boolean/meshing operations.
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg \
    MECHFORGE_WORKSPACE_DIR=/data/workspace

# Runtime shared libraries required by OpenCASCADE (OCP) and matplotlib/trimesh, plus the
# CalculiX (`ccx`) solver used by the FEA validator. gmsh (installed via the `fea` extra)
# meshes the geometry; ccx solves it. PrusaSlicer is NOT bundled here (no reliable slim
# package) — the slicer validator falls back to a geometric cost/time estimate when the
# `prusa-slicer` binary is absent; add it to the image if you want toolpath-accurate numbers.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglu1-mesa \
        libx11-6 \
        libxext6 \
        libxrender1 \
        libxi6 \
        libsm6 \
        libice6 \
        libfontconfig1 \
        libgomp1 \
        calculix-ccx \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (better layer caching), then the package.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install ".[dev,fea,metrics]"

COPY tests ./tests
COPY scripts ./scripts

RUN mkdir -p /data/workspace
VOLUME ["/data"]

# Default to help; run the CLI or API explicitly (see docker-compose.yml).
CMD ["anvil", "--help"]
