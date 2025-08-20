import json
import uuid
import asyncio
from typing import Mapping
from werkzeug import Request, Response

from .auth import validate_bearer_token
from .invoker import _invoke0, is_debug
from dify_plugin import Endpoint
from concurrent.futures import ThreadPoolExecutor

import logging
from dify_plugin.config.logger_format import plugin_logger_handler

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)

executor = ThreadPoolExecutor(max_workers=10)

def run_async_in_thread(coro):
    def thread_target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)

    future = executor.submit(thread_target)
    return future.result()

class McpPostEndpoint(Endpoint):
    def _invoke(self, r: Request, values: Mapping, settings: Mapping) -> Response:
        if is_debug(settings):
            logger.info(f"**********************************************************")
            json_str = json.dumps(r.json, ensure_ascii=False, indent=2)
            logger.info(f"r.json is {json_str}")
            args_str = json.dumps(r.args, ensure_ascii=False, indent=2)
            logger.info(f"r.args is {args_str}")
            
        method = r.json.get("method")
        session_id = r.args.get('session_id', str(uuid.uuid4()).replace("-", ""))
        headers = {"mcp-session-id": session_id}
        
        error = validate_bearer_token(r, settings)
        if error:
            return error
            
        response = {"jsonrpc": "2.0", "id": r.json.get("id"), "result": {}}
        
        if method == "tools/list" or method == "tools/call":
            response = run_async_in_thread(_invoke0(r, values, settings))
        elif method == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": r.json.get("id"),
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": { "tools": {} },
                    "serverInfo": { "name": "Nacos MCP Dify Plugin", "version": "1.0.0" }
                }
            }
        
        if is_debug(settings):
            response_str = json.dumps(response, ensure_ascii=False, indent=2)
            logger.info(f"response is {response_str}")
            
        return Response(json.dumps(response, ensure_ascii=False, indent=2), status=200, content_type="application/json", headers=headers)
