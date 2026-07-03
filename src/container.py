"""Composition root.

The one place concrete adapters are constructed and bound to ports. Everything else
receives its collaborators by injection. Swap an implementation here (e.g. OpenAI vs
Anthropic, or a real FEA solver) without touching any other layer.
"""

from __future__ import annotations

import structlog
from dependency_injector import containers, providers

from application.agents.design_agent import DesignAgent
from application.assembly_toolkit import AssemblyToolkit
from application.catalog_service import CatalogService
from application.design_toolkit import DesignToolkit
from application.revision_service import RevisionService
from application.tools.design_tools import (
    BuildPartTool,
    ExportPartTool,
    SetDesignSpecTool,
    ValidatePartTool,
)
from application.tools.registry import ToolRegistry
from application.use_cases.run_design_session import DesignSessionCoordinator
from config.settings import LLMVendor, Settings, get_settings
from domain.ports.build_cache import BuildCache
from domain.ports.llm import LLMProvider
from domain.ports.metrics import MetricsSink
from infrastructure.cad.build123d_executor import Build123dExecutor
from infrastructure.llm.anthropic_provider import AnthropicProvider
from infrastructure.llm.openai_provider import OpenAIProvider
from infrastructure.observability.prometheus_metrics import build_prometheus_sink
from infrastructure.observability.structlog_metrics import (
    NullMetricsSink,
    StructlogMetricsSink,
)
from infrastructure.persistence.filesystem_artifact_repository import (
    FilesystemArtifactRepository,
)
from infrastructure.persistence.filesystem_build_cache import FilesystemBuildCache
from infrastructure.persistence.filesystem_design_catalog import FilesystemDesignCatalog
from infrastructure.persistence.filesystem_history_repository import (
    FilesystemHistoryRepository,
)
from infrastructure.rendering.matplotlib_renderer import MatplotlibRenderer
from infrastructure.validation.calculix_fea_validator import CalculiXFeaValidator
from infrastructure.validation.composite_validator import CompositeValidator
from infrastructure.validation.drone_balance_validator import DroneBalanceValidator
from infrastructure.validation.mass_properties_validator import MassPropertiesValidator
from infrastructure.validation.printability_validator import PrintabilityValidator
from infrastructure.validation.slicer_validator import SlicerValidator

log = structlog.get_logger(__name__)


def _build_llm(settings: Settings) -> LLMProvider:
    if settings.llm_vendor is LLMVendor.OPENAI:
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        )
    return AnthropicProvider(
        api_key=settings.anthropic_api_key,
        model=settings.anthropic_model,
        max_tokens=settings.llm_max_tokens,
        temperature=settings.llm_temperature,
    )


def _build_metrics(settings: Settings) -> MetricsSink:
    backend = settings.metrics_backend.lower()
    if backend == "null":
        return NullMetricsSink()
    if backend == "prometheus":
        # Falls back to the structlog sink internally if prometheus_client is absent.
        return build_prometheus_sink()
    return StructlogMetricsSink()


def _build_executor(settings: Settings, metrics: MetricsSink) -> Build123dExecutor:
    return Build123dExecutor(
        timeout_seconds=settings.cad_timeout_seconds,
        memory_limit_mb=settings.cad_memory_limit_mb,
        cpu_seconds=settings.cad_cpu_seconds,
        metrics=metrics,
    )


def _build_renderer(settings: Settings) -> MatplotlibRenderer:
    return MatplotlibRenderer(image_size=settings.render_image_size)


def _build_validator(settings: Settings) -> CompositeValidator:
    return CompositeValidator(
        validators=(
            MassPropertiesValidator(),
            PrintabilityValidator(
                build_volume_mm=(
                    settings.build_volume_x_mm,
                    settings.build_volume_y_mm,
                    settings.build_volume_z_mm,
                ),
                min_wall_thickness_mm=settings.min_wall_thickness_mm,
            ),
            DroneBalanceValidator(com_tolerance_mm=settings.drone_com_tolerance_mm),
            SlicerValidator(
                slicer_cmd=settings.slicer_cmd,
                slicer_config_path=settings.slicer_config_path,
                machine_rate_usd_per_hour=settings.machine_rate_usd_per_hour,
                timeout_seconds=settings.slicer_timeout_seconds,
            ),
            CalculiXFeaValidator(
                solver_cmd=settings.fea_solver_cmd,
                mesh_size_mm=settings.fea_mesh_size_mm,
                warn_safety_factor=settings.fea_warn_safety_factor,
                timeout_seconds=settings.fea_timeout_seconds,
            ),
        )
    )


def _build_repository(settings: Settings) -> FilesystemArtifactRepository:
    return FilesystemArtifactRepository(workspace_dir=settings.workspace_dir)


def _build_history_repository(settings: Settings) -> FilesystemHistoryRepository:
    return FilesystemHistoryRepository(workspace_dir=settings.workspace_dir)


def _build_cache(settings: Settings) -> BuildCache | None:
    if not settings.build_cache_enabled:
        return None
    return FilesystemBuildCache(workspace_dir=settings.workspace_dir)


def _build_catalog(settings: Settings) -> FilesystemDesignCatalog:
    return FilesystemDesignCatalog(workspace_dir=settings.workspace_dir)


def _build_registry() -> ToolRegistry:
    return ToolRegistry(
        tools=(
            SetDesignSpecTool(),
            BuildPartTool(),
            ValidatePartTool(),
            ExportPartTool(),
        )
    )


class Container(containers.DeclarativeContainer):
    settings = providers.Singleton(get_settings)

    llm = providers.Singleton(_build_llm, settings)
    metrics = providers.Singleton(_build_metrics, settings)
    executor = providers.Singleton(_build_executor, settings, metrics)
    renderer = providers.Singleton(_build_renderer, settings)
    validator = providers.Singleton(_build_validator, settings)
    repository = providers.Singleton(_build_repository, settings)
    cache = providers.Singleton(_build_cache, settings)
    registry = providers.Singleton(_build_registry)

    history_repository = providers.Singleton(_build_history_repository, settings)
    revision_service = providers.Singleton(RevisionService, history=history_repository)

    catalog = providers.Singleton(_build_catalog, settings)
    catalog_service = providers.Singleton(CatalogService, catalog=catalog)

    toolkit = providers.Singleton(
        DesignToolkit,
        executor=executor,
        renderer=renderer,
        validator=validator,
        repository=repository,
        settings=settings,
        cache=cache,
        metrics=metrics,
    )

    assembly_toolkit = providers.Singleton(AssemblyToolkit, toolkit=toolkit)

    agent = providers.Singleton(
        DesignAgent,
        provider=llm,
        registry=registry,
        max_steps=settings.provided.max_agent_steps,
    )

    coordinator = providers.Singleton(
        DesignSessionCoordinator,
        agent=agent,
        toolkit=toolkit,
        settings=settings,
    )
