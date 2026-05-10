"""Internal service-to-service communication module.

This module handles secure communication between internal services:
- Cloud Tasks -> SMS Gateway (background task execution)
- Cloud Scheduler -> SMS Gateway (scheduled task requeue)

All endpoints require OIDC authentication via GCP service accounts.
"""
from .auth import verify_internal_request

__all__ = [
    "verify_internal_request",
]
