"""Internal service-to-service communication module.

This module handles secure communication between internal services:
- Task Worker -> SMS Gateway (task completion notifications)
- Cloud Tasks -> Task Worker (scheduled task execution)

All endpoints require OIDC authentication via GCP service accounts.
"""
from .auth import verify_internal_request

__all__ = [
    "verify_internal_request",
]
