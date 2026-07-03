"""Composition root.

The one place concrete adapters are constructed and bound to ports. Everything else
receives its collaborators by injection. Swap an implementation here (e.g. OpenAI vs
Anthropic, or a real FEA solver) without touching any other layer.
"""

from __future__ import annotations

from dependency_injector import containers, providers

from application.agents.design_agent import DesignAgent
from application.design_toolkit import DesignToolkit
from application.tools.design_tools import (
    BuildPartTool,
    ExportPartTool,
    SetDesignSpecTool,
    ValidatePartTool,
)
from application.tools.registry import ToolRegistry
from application.use_cases.run_design_session import DesignSessionCoordinator
from config.settings import LLMVendor, Settings, get_settings
from domain.ports.llm import LLMProvider
from infrastructure.cad.build123d_executor import Build123dExecutor
from infrastructure.llm.anthropic_provider import AnthropicProvider
from infrastructure.llm.openai_provider import OpenAIProvider
from infrastructure.persistence.filesystem_artifact_repository import (
    FilesystemArtifactRepository,
)
from infrastructure.rendering.matplotlib_renderer import MatplotlibRenderer
from infrastructure.validation.composite_validator import CompositeValidator
from infrastructure.validation.fea_validator import NullFeaValidator
from infrastructure.validation.mass_properties_validator import MassPropertiesValidator
from infrastructure.validation.printability_validator import PrintabilityValidator


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


def _build_executor(settings: Settings) -> Build123dExecutor:
    return Build123dExecutor(
        timeout_seconds=settings.cad_timeout_seconds,
        memory_limit_mb=settings.cad_memory_limit_mb,
        cpu_seconds=settings.cad_cpu_seconds,
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
            NullFeaValidator(),
        )
    )


def _build_repository(settings: Settings) -> FilesystemArtifactRepository:
    return FilesystemArtifactRepository(workspace_dir=settings.workspace_dir)


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
    executor = providers.Singleton(_build_executor, settings)
    renderer = providers.Singleton(_build_renderer, settings)
    validator = providers.Singleton(_build_validator, settings)
    repository = providers.Singleton(_build_repository, settings)
    registry = providers.Singleton(_build_registry)

    toolkit = providers.Singleton(
        DesignToolkit,
        executor=executor,
        renderer=renderer,
        validator=validator,
        repository=repository,
        settings=settings,
    )

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
