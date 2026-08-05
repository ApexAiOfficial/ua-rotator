"""Apex User-Agent Rotator public API."""

from .core import (
    DEFAULT_PROFILE_NAME,
    SCHEMA_VERSION,
    RandomChoices,
    RotationPolicy,
    UserAgentEntry,
    UserAgentRotator,
)

__all__ = [
    "DEFAULT_PROFILE_NAME",
    "SCHEMA_VERSION",
    "RandomChoices",
    "RotationPolicy",
    "UserAgentEntry",
    "UserAgentRotator",
    "__version__",
]

__version__ = "1.2.1"
