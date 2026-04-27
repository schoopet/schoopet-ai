"""Gemini model configured to use the global Vertex AI endpoint.

gemini-3.x preview models are only available on the global endpoint
(aiplatform.googleapis.com), not the regional endpoint
(us-central1-aiplatform.googleapis.com).

This subclass overrides api_client to create the genai Client with
location='global', which causes the genai library to use the global
base URL automatically.
"""
import os

from google.adk.models.google_llm import Gemini as _BaseGemini
from google.genai import types


class GlobalGemini(_BaseGemini):
    """Gemini model that routes requests to the global Vertex AI endpoint."""

    @property
    def api_client(self):
        from google.genai import Client

        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        return Client(
            project=project,
            location="global",
            http_options=types.HttpOptions(
                headers=self._tracking_headers(),
                retry_options=self.retry_options,
            ),
        )
