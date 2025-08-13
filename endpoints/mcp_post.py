import json
import logging
import re
import random
import uuid

from typing import Mapping, cast, Any
from .auth import validate_bearer_token

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pydantic import BaseModel
from werkzeug import Request, Response

from maintainer.ai.model.nacos_mcp_info import *
from maintainer.ai.nacos_mcp_service import NacosAIMaintainerService
from maintainer.common.ai_maintainer_client_config_builder import AIMaintainerClientConfigBuilder

from dify_plugin import Endpoint
from dify_plugin.entities import I18nObject
from dify_plugin.entities.tool import ToolParameter, ToolProviderType, ToolInvokeMessage, ToolDescription
from dify_plugin.interfaces.agent import ToolEntity, AgentToolIdentity

from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

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
        session_id = r.args.get('session_id')
        data = r.json
        method = data.get("method")
        
        error = validate_bearer_token(r, settings)
        if error:
            return error
            
        if method == "tools/list" or method == "tools/call":
            response = run_async_in_thread(self._invoke0(r, settings))
            return Response(json.dumps(response), status=200, content_type="application/json")
        else:
            session_id = str(uuid.uuid4()).replace("-", "")
            headers = {"mcp-session-id": session_id}
            response = {
                "jsonrpc": "2.0",
                "id": data.get("id"),
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {}
                    },
                    "serverInfo": {
                        "name": "Nacos MCP Dify Plugin",
                        "version": "1.0.0"
                    }
                }
            }
            return Response(json.dumps(response), status=200, content_type="application/json", headers=headers)

    
    async def _invoke0(self, r: Request, settings: Mapping) -> dict:        
        mcp_server_list = await self.list_mcp_tools(settings)
        
        if r.json.get("method") == "tools/list":
            mcp_tools = []
            for mcp_server in mcp_server_list:
                for mcp_tool in mcp_server.toolSpec.tools:
                    mcp_tools.append({
                        "name": mcp_server.name + "___" + mcp_tool.name,
                        "description": mcp_server.description + ". " + mcp_tool.description,
                        "inputSchema": mcp_tool.inputSchema
                    })
            
            return {
                "jsonrpc": "2.0",
                "id": r.json.get("id"),
                "result": {
                    "tools": mcp_tools
                }
            }
        elif r.json.get("method") == "tools/call":            
            mcp_name  = r.json.get("params", {}).get("name").split("___")[0]
            tool_name = r.json.get("params", {}).get("name").split("___")[1]
            arguments = r.json.get("params", {}).get("arguments", {})
            
            mcp_server_detail_info = next((tool for tool in mcp_server_list if tool.name == mcp_name), None)
            call_result = await self.call_mcp_tools(mcp_server_detail_info, tool_name, arguments)
            
            text = " ".join(c.model_dump().get("text", "") for c in call_result.content)
            return {
                "jsonrpc": "2.0",
                "id": r.json.get("id"),
                "result": {
                    "content": [{"text": text}],
                    "isError": call_result.isError
                }
            }
            
        return {}
    
    async def list_mcp_tools(self, settings: Mapping):
        nacos_addr = settings.get("nacos_addr") or "127.0.0.1:8848"
        nacos_username = settings.get("nacos_username") or ""
        nacos_password = settings.get("nacos_password") or ""
        nacos_namespace_id = settings.get("nacos_namespace_id") or "public"
        mcp_name_pattern = settings.get("mcp_name_pattern") or ""
        tool_name_pattern = settings.get("tool_name_pattern") or ""
        
        ai_client_config = (AIMaintainerClientConfigBuilder().server_address(nacos_addr).username(nacos_username).password(nacos_password).access_key(nacos_username).secret_key(nacos_password).build())
        mcp_service = await NacosAIMaintainerService.create_mcp_service(ai_client_config)
        total_count, page_num, page_available,mcp_servers = await mcp_service.list_mcp_servers(nacos_namespace_id,"",1,65535)
        
        filtered_servers = [
            mcp_server for mcp_server in mcp_servers if (mcp_server.protocol == "mcp-sse" or mcp_server.protocol == "mcp-streamable") and (not mcp_name_pattern or re.search(mcp_name_pattern, mcp_server.name))
        ]
        
        mcp_server_list = await asyncio.gather(*[
            mcp_service.get_mcp_server_detail(nacos_namespace_id, mcp_server.name, "") for mcp_server in filtered_servers
        ])
        
        for mcp_server in mcp_server_list:
            to_removes = [ tool for tool in mcp_server.toolSpec.tools if tool_name_pattern and not re.search(tool_name_pattern, tool.name)]
            
            for remove in to_removes:
                mcp_server.toolSpec.tools.remove(remove)
                mcp_server.toolSpec.toolsMeta.pop(remove.name, None)
        
        return mcp_server_list
    
    async def call_mcp_tools(self, mcp_server_detail_info: McpServerDetailInfo, tool_name: str, arguments: dict):
        if not mcp_server_detail_info.backendEndpoints:
            return None
            
        endpoint = random.choice(mcp_server_detail_info.backendEndpoints)
        schema = "https" if endpoint.port == 443 else "http"
        export_path = mcp_server_detail_info.remoteServerConfig.exportPath
        _url = f"{schema}://{endpoint.address}:{endpoint.port}/{export_path.lstrip('/')}"
        
        client_ctx = None
        if mcp_server_detail_info.protocol == "mcp-sse":
            client_ctx = sse_client(url=_url)
        elif mcp_server_detail_info.protocol == "mcp-streamable":
            client_ctx = streamablehttp_client(url=_url)
        else:
            raise ValueError(f"Unsupported protocol: {mcp_server_detail_info.protocol}")
            
        async with client_ctx as (_read, _write):
            async with ClientSession(_read, _write) as _session:
                await _session.initialize()
                return await _session.call_tool(tool_name, arguments)
        