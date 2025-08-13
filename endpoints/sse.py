import json
import time
import uuid
from .auth import validate_bearer_token

import asyncio
from typing import Mapping, cast, Any
from maintainer.ai.nacos_mcp_service import NacosAIMaintainerService
from maintainer.common.ai_maintainer_client_config_builder import AIMaintainerClientConfigBuilder

from dify_plugin import Endpoint
from werkzeug import Request, Response

def create_sse_message(event, data):
    return f"event: {event}\ndata: {json.dumps(data) if isinstance(data, (dict, list)) else data}\n\n"

class SSEEndpoint(Endpoint):
    def _invoke(self, r: Request, values: Mapping, settings: Mapping) -> Response:
        session_id = str(uuid.uuid4()).replace("-", "")

        auth_error = validate_bearer_token(r, settings)
        if auth_error:
            return auth_error

        def generate():
            endpoint = f"messages/?session_id={session_id}"
            yield create_sse_message("endpoint", endpoint)

            while True:
                if self.session.storage.exist(session_id):
                    message = self.session.storage.get(session_id)
                    message = message.decode()
                    self.session.storage.delete(session_id)
                    yield create_sse_message("message", message)
                time.sleep(0.5)

        return Response(generate(), status=200, content_type="text/event-stream")
