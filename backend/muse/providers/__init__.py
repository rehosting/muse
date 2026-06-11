"""Provider adapters: normalize each tool's transcript format into muse models."""

from .base import IndexDoc, Provider
from .registry import provider_for, providers

__all__ = ["IndexDoc", "Provider", "provider_for", "providers"]
