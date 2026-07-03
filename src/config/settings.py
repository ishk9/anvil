"""Typed application configuration.

Priority: environment variables > `.env` file > defaults declared here.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMVendor(StrEnum):
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MECHFORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM ---
    llm_vendor: LLMVendor = LLMVendor.ANTHROPIC
    anthropic_api_key: str = Field(default="", description="Anthropic API key")
    anthropic_model: str = "claude-sonnet-4-5"
    openai_api_key: str = Field(default="", description="OpenAI API key")
    openai_model: str = "gpt-4o"
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.2

    # --- Agent loop ---
    max_agent_steps: int = 24
    """Hard ceiling on tool-call iterations per user turn (prevents runaway loops)."""

    # --- CAD sandbox ---
    cad_timeout_seconds: int = 300
    """Wall-clock budget per build. Heavy fillet/boolean chains are legitimately slow."""

    cad_cpu_seconds: int = 600
    """CPU-time rlimit for the sandbox process (guards genuinely runaway code)."""

    cad_memory_limit_mb: int = 0
    """Virtual address-space (RLIMIT_AS) cap in MB. 0 disables it.

    OCCT/numpy/matplotlib reserve large *virtual* address space that far exceeds real
    RSS, so a low RLIMIT_AS causes spurious MemoryError crashes. Prefer the container's
    cgroup memory limit (docker --memory) + the wall-clock timeout for real protection.
    """

    # --- Rendering ---
    render_image_size: int = 768
    render_views: int = 4

    # --- Printability defaults (a common desktop FDM bed, mm) ---
    build_volume_x_mm: float = 220.0
    build_volume_y_mm: float = 220.0
    build_volume_z_mm: float = 250.0
    min_wall_thickness_mm: float = 0.8

    # --- FEA (CalculiX + gmsh; degrades gracefully when the stack is absent) ---
    fea_solver_cmd: str = "ccx"
    """CalculiX executable name/path. Missing binary -> FEA reports a warning, never fails."""
    fea_mesh_size_mm: float = 2.0
    fea_warn_safety_factor: float = 2.0
    fea_timeout_seconds: int = 600

    # --- Slicer-backed cost/printability (PrusaSlicer/CuraEngine; geometric fallback) ---
    slicer_cmd: str = "prusa-slicer"
    slicer_config_path: Path | None = None
    machine_rate_usd_per_hour: float = 3.0
    slicer_timeout_seconds: int = 120

    # --- Drone balance ---
    drone_com_tolerance_mm: float = 2.0
    """Max CoM offset from the geometric centre in the XY plane before the airframe is
    flagged as imbalanced (flight controllers trim against XY drift in level flight)."""

    # --- Build cache + observability ---
    build_cache_enabled: bool = True
    """Hash generated code+params and skip re-executing identical builds."""
    metrics_backend: str = "structlog"
    """One of: null, structlog, prometheus (prometheus falls back to structlog if the
    client library is not installed)."""

    # --- Storage ---
    workspace_dir: Path = Path("/data/workspace")
    """Root for all generated artifacts (mounted volume in Docker)."""

    # --- Live viewer ---
    viewer_url: str = "http://localhost:8091"
    """Base URL where the live 3D viewer is reachable from the user's machine."""

    # --- Logging ---
    log_level: str = "INFO"
    log_json: bool = True

    def active_api_key(self) -> str:
        return (
            self.anthropic_api_key
            if self.llm_vendor is LLMVendor.ANTHROPIC
            else self.openai_api_key
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
