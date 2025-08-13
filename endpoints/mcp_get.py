from typing import Mapping

import asyncio
from typing import Mapping, cast, Any
from maintainer.ai.nacos_mcp_service import NacosAIMaintainerService
from maintainer.common.ai_maintainer_client_config_builder import AIMaintainerClientConfigBuilder

from dify_plugin import Endpoint
from werkzeug import Request, Response
from .auth import validate_bearer_token

class McpGetEndpoint(Endpoint):
    def _invoke(self, r: Request, values: Mapping, settings: Mapping) -> Response:
        auth_error = validate_bearer_token(r, settings)
        if auth_error:
            return auth_error
            
        response = {
            "jsonrpc": "2.0",
            "id": None,
            "error": {
                "code": -32000,
                "message": "Not support make use of Server-Sent Events (SSE) to stream multiple server messages."
            },
        }

        return Response(response, status=405, content_type="application/json")
