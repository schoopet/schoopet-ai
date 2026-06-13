# Patch ADK VertexAiMemoryBankService to fix a bug where the parent vertexai.Client
# is GC'd immediately after _get_api_client() returns. The Client.__del__ schedules
# aclose() on the shared BaseApiClient, which runs before the fire-and-forget
# ingest_events background task, causing it to fail with
# "Cannot send a request, as the client has been closed."
# Fix: cache the Client reference on the instance so __del__ never fires mid-flight.
from google.adk.memory.vertex_ai_memory_bank_service import VertexAiMemoryBankService


def _patched_get_api_client(self):
    if not hasattr(self, '_vertexai_client_ref'):
        import vertexai
        if self._express_mode_api_key:
            self._vertexai_client_ref = vertexai.Client(api_key=self._express_mode_api_key)
        else:
            self._vertexai_client_ref = vertexai.Client(
                project=self._project, location=self._location
            )
    return self._vertexai_client_ref.aio


VertexAiMemoryBankService._get_api_client = _patched_get_api_client
