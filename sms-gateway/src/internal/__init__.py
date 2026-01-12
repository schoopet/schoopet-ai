"""Internal service-to-service communication module.

This module handles secure communication between internal services:
- Task Worker -> SMS Gateway (task completion notifications)
- Cloud Tasks -> Task Worker (scheduled task execution)

All endpoints require authentication via OIDC tokens or HMAC signatures.
"""
from .auth import verify_internal_request, get_internal_hmac_secret

__all__ = [
    "verify_internal_request",
    "get_internal_hmac_secret",
]
