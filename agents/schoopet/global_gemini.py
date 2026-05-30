"""Gemini model configured to use the global Vertex AI endpoint.

gemini-3.x preview models are only available on the global endpoint
(aiplatform.googleapis.com), not the regional endpoint
(us-central1-aiplatform.googleapis.com).

This subclass follows the ADK 2.x documented pattern for customizing the
underlying Client: use @cached_property and pass location='global' with
vertexai=True so the genai library routes to aiplatform.googleapis.com
automatically.
"""
from functools import cached_property

from google.adk.models.google_llm import Gemini as _BaseGemini


class GlobalGemini(_BaseGemini):
    """Gemini model that routes requests to the global Vertex AI endpoint."""

    @cached_property
    def api_client(self):
        from google.genai import Client

        return Client(vertexai=True, location="global")
