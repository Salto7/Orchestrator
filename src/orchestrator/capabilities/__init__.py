"""Capability registry — LangChain tools bound per Job.

Naming: ``provision_*`` (never ensure_*), ``run_skill_script``.
"""

from orchestrator.capabilities.registry import (
    REGISTRY,
    CapabilityGroup,
    capability,
    default_allowed_tools,
    ensure_registered,
    get_tools_for_names,
)

__all__ = [
    "CapabilityGroup",
    "REGISTRY",
    "capability",
    "default_allowed_tools",
    "ensure_registered",
    "get_tools_for_names",
]
