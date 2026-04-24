"""Provider layer — the ONLY place adjoint spawns third-party CLIs.

adjoint never manages vendor API keys; credentials remain owned by the user's
already-installed `claude` / `codex` / etc. CLIs.
"""

from .base import Provider, ProviderNotFoundError, ProviderResult

__all__ = ["Provider", "ProviderNotFoundError", "ProviderResult"]
