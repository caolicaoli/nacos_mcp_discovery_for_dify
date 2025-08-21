import json
import re
import random
import hashlib
import uuid
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor

from typing import Mapping, cast, Any
from pydantic import BaseModel
from werkzeug import Request, Response

from maintainer.ai.model.nacos_mcp_info import *
from maintainer.ai.nacos_mcp_service import NacosAIMaintainerService
from maintainer.common.ai_maintainer_client_config_builder import AIMaintainerClientConfigBuilder
from maintainer.ai.model.nacos_mcp_info import McpServerDetailInfo, McpToolSpecification, McpTool, McpToolMeta

from dify_plugin import Endpoint
from dify_plugin.entities import I18nObject
from dify_plugin.entities.tool import ToolParameter, ToolProviderType, ToolInvokeMessage, ToolDescription
from dify_plugin.interfaces.agent import ToolEntity, AgentToolIdentity

from mcp import ClientSession, types
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

import logging
from dify_plugin.config.logger_format import plugin_logger_handler

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(plugin_logger_handler)
            
def is_debug(settings: Mapping) -> bool:    
    if "parameter" not in settings or not settings["parameter"]:
        return False
        
    parameter = json.loads(settings.get("parameter"))
    return bool(parameter.get("debug") == "debug")

async def _invoke0(r: Request, values: Mapping, settings: Mapping) -> dict:    
    if is_debug(settings):
        logger.info(f"start list_mcp_tools")
    mcp_server_list = await list_mcp_tools(settings)
    if is_debug(settings):
        logger.info(f"end   list_mcp_tools")
    
    if r.json.get("method") == "tools/list":
        mcp_tools = []
        for mcp_server in mcp_server_list:
            tools = getattr(getattr(mcp_server, "toolSpec", None), "tools", None)
            if not tools:
                continue
                
            for mcp_tool in tools:
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
        
        if is_debug(settings):
            logger.info(f"start call_mcp_tools")
        call_result = await call_mcp_tools(mcp_server_detail_info, tool_name, arguments, settings)
        if is_debug(settings):
            logger.info(f"end   call_mcp_tools type is {type(call_result).__name__}")
            
        if is_debug(settings):
            logger.info(f"start content_dump_text")
        content = [{"type": "text", "text": c.model_dump().get("text", "")} for c in call_result.content]
        if is_debug(settings):
            logger.info(f"end   content_dump_text: {json.dumps(content, ensure_ascii=False)}")
        
        return {
            "jsonrpc": "2.0",
            "id": r.json.get("id"),
            "result": {
                "content": content,
                "isError": False
            }
        }
        
    return {}

_cache_mcp_tools = {}
_cache_mcp_tools_lock = asyncio.Lock()

async def list_mcp_tools(settings: Mapping):
    nacos_addr = settings.get("nacos_addr") or "127.0.0.1:8848"
    nacos_namespace_id = settings.get("nacos_namespace_id") or "public"
    mcp_name_pattern = settings.get("mcp_name_pattern") or ""
    tool_name_pattern = settings.get("tool_name_pattern") or ""
    
    combined = f"{nacos_addr}|{nacos_namespace_id}|{mcp_name_pattern}|{tool_name_pattern}"
    combined = hashlib.md5(combined.encode("utf-8")).hexdigest()
    
    timeout = 60
    if "parameter" in settings and settings["parameter"]:
        parameter = json.loads(settings.get("parameter") or "{}")
        timeout = parameter.get("expire") or 60
        
    now = time.time()
    
    cached = _cache_mcp_tools.get(combined)
    if cached is not None and now - cached["timestamp"] < timeout:
        if is_debug(settings):
            logger.info(f"list_mcp_tools read from cache for key: {combined}")
        return cached["data"]

    async with _cache_mcp_tools_lock:
        cached = _cache_mcp_tools.get(combined)
        now = time.time()
        if cached is not None and now - cached["timestamp"] < timeout:
            if is_debug(settings):
                logger.info(f"list_mcp_tools read from cache for key: {combined}")
            return cached["data"]

        if is_debug(settings):
            logger.info(f"list_mcp_tools read from remote for key: {combined}")
            
        result = await list_mcp_tools_native(settings)
        _cache_mcp_tools[combined] = {"data": result, "timestamp": time.time()}
        return result

async def list_mcp_tools_native(settings: Mapping):
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
    
    if is_debug(settings):
        logger.info(f"list tools call get_mcp_server_detail for all namespace [{nacos_namespace_id}]")
    
    mcp_server_list = await asyncio.gather(*[
        mcp_service.get_mcp_server_detail(nacos_namespace_id, mcp_server.name, "") for mcp_server in filtered_servers
    ])
        
    with_tools_mcp_server = []
    without_tools_mcp_server = []

    for mcp_server in mcp_server_list:
        tools = getattr(getattr(mcp_server, "toolSpec", None), "tools", None)
        if tools:
            with_tools_mcp_server.append(mcp_server)
        else:
            without_tools_mcp_server.append(mcp_server)
    
    for mcp_server in with_tools_mcp_server:
        to_removes = [ tool for tool in mcp_server.toolSpec.tools if tool_name_pattern and not re.search(tool_name_pattern, tool.name)]
        if not to_removes:
            continue
            
        for remove in to_removes:
            mcp_server.toolSpec.tools.remove(remove)
            mcp_server.toolSpec.toolsMeta.pop(remove.name, None) 
    
    for mcp_server in without_tools_mcp_server:
        tools = await fetch_mcp_tools(mcp_server, settings)
        if not tools or not tools.tools:
            continue
            
        mcp_server.toolSpec = McpToolSpecification(tools=[], toolsMeta={})
        for tool in tools.tools:
            if not tool_name_pattern or re.search(tool_name_pattern, tool.name):
                mcp_server.toolSpec.tools.append(McpTool(name=tool.name, description=tool.description, inputSchema=tool.inputSchema))
        
    return mcp_server_list

async def fetch_mcp_tools(mcp_server_detail_info: McpServerDetailInfo, settings: Mapping):
    if not mcp_server_detail_info.backendEndpoints:
        if is_debug(settings):
            logger.info(f"call tool backendEndpoints is empty.")
        return None
    
    endpoint = random.choice(mcp_server_detail_info.backendEndpoints)
    schema = "https" if endpoint.port == 443 else "http"
    export_path = mcp_server_detail_info.remoteServerConfig.exportPath
    _url = f"{schema}://{endpoint.address}:{endpoint.port}/{export_path.lstrip('/')}"
    
    if is_debug(settings):
        logger.info(f"fetch tools build client [{mcp_server_detail_info.protocol}] [{_url}]")
    
    client_ctx = None
    if mcp_server_detail_info.protocol == "mcp-sse":
        client_ctx = sse_client(url=_url)
    elif mcp_server_detail_info.protocol == "mcp-streamable":
        client_ctx = streamablehttp_client(url=_url)
    else:
        return None
    
    try:
        async with client_ctx as values:
            _read, _write, *rest = values 
            async with ClientSession(_read, _write) as _session:
                await _session.initialize()
                result = await _session.list_tools()
                return result
    except* Exception as e:
        for sub_exc in e.exceptions:
            logger.error(f"Subtask exception: {sub_exc}", exc_info=True)

async def call_mcp_tools(mcp_server_detail_info: McpServerDetailInfo, tool_name: str, arguments: dict, settings: Mapping):
    if not mcp_server_detail_info.backendEndpoints:
        if is_debug(settings):
            logger.info(f"call tool backendEndpoints is empty.")
        return None
    
    endpoint = random.choice(mcp_server_detail_info.backendEndpoints)
    schema = "https" if endpoint.port == 443 else "http"
    export_path = mcp_server_detail_info.remoteServerConfig.exportPath
    _url = f"{schema}://{endpoint.address}:{endpoint.port}/{export_path.lstrip('/')}"
    
    if is_debug(settings):
        logger.info(f"call tool build client [{mcp_server_detail_info.protocol}] [{_url}]")
    
    client_ctx = None
    if mcp_server_detail_info.protocol == "mcp-sse":
        client_ctx = sse_client(url=_url)
    elif mcp_server_detail_info.protocol == "mcp-streamable":
        client_ctx = streamablehttp_client(url=_url)
    else:
        raise ValueError(f"Unsupported protocol: {mcp_server_detail_info.protocol}")
    
    if is_debug(settings):
        logger.info(f"call tool param [{tool_name}] [{json.dumps(arguments, ensure_ascii=False)}]")
    
    try:
        async with client_ctx as values:
            _read, _write, *rest = values 
            async with ClientSession(_read, _write) as _session:
                await _session.initialize()
                result = await _session.call_tool(tool_name, arguments)
                return result
    except* Exception as e:
        for sub_exc in e.exceptions:
            logger.error(f"Subtask exception: {sub_exc}", exc_info=True)
