# -*- coding: utf-8 -*-
"""
@File    : mcp_utils.py
@Description: This file contains utility functions for interacting with the MCP service and LLM APIs.
@Author  : <<your name>>
@Date    : <<date>>
@Version : 1.0
"""

# Input: MCPClient instance, file paths, prompts, etc.
# Output: File content, LLM responses, SSE events.

import json
import httpx
from typing import Dict, AsyncGenerator

from fastmcp import Client as MCPClient
from ..config import settings


def sse(event: str, data_obj: Dict) -> str:
    """
    将事件类型和数据对象，格式化为符合 SSE 规范的字符串。
    
    Args:
        event (str): 事件类型。
        data_obj (Dict): 要发送的数据。
    
    Returns:
        str: SSE 格式的文本块。
    """
    return f"event: {event}\n" + f"data: {json.dumps(data_obj, ensure_ascii=False, default=str)}\n\n"


async def mcp_read_file(mcp_client: MCPClient, file_path: str) -> str | None:
    """
    通过 MCP 服务读取文件内容。
    
    Args:
        mcp_client: MCP 客户端实例。
        file_path: 文件路径。
    
    Returns:
        文件内容，如果失败则返回 None。
    """
    try:
        read_res = await mcp_client.call_tool("read_file", {"path": file_path, "limit": 10000000})
        content = str(getattr(read_res, "data", read_res))
        if "文件未找到" in content or "file not found" in content.lower():
            return None
        return content
    except Exception:
        return None


async def mcp_smart_write(mcp_client: MCPClient, file_path: str, content: str) -> bool:
    """
    通过 MCP 服务写入文件内容。
    如果文件已存在，先读取原内容再替换；如果不存在，直接创建。
    
    Args:
        mcp_client: MCP 客户端实例。
        file_path: 文件路径。
        content: 要写入的内容。
    
    Returns:
        是否写入成功。
    """
    try:
        # 先尝试读取文件
        old_content = await mcp_read_file(mcp_client, file_path)
        
        if old_content is not None:
            # 文件存在，用全部内容替换
            await mcp_client.call_tool("smart_edit", {
                "file_path": file_path,
                "old_string": old_content,
                "new_string": content
            })
        else:
            # 文件不存在，直接创建
            await mcp_client.call_tool("smart_edit", {
                "file_path": file_path,
                "old_string": "",
                "new_string": content
            })
        return True
    except Exception as e:
        print(f"❌ 文件写入失败: {file_path}, 错误: {str(e)}")
        return False


async def call_llm_streaming(
    system_prompt: str,
    user_input: str,
    model_name: str,
    api_url: str = "https://vip.dmxapi.com/v1/chat/completions",
    api_key: str = None,
    yield_tokens: bool = True
) -> AsyncGenerator:
    """
    调用LLM API（支持流式输出） - 使用 httpx 实现真异步
    """
    if api_key is None:
        api_key = settings.OPENAI_API_KEY
    
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {api_key}',
        'User-Agent': 'DMXAPI/1.0.0 (https://www.dmxapi.com)',
        'Content-Type': 'application/json'
    }
    
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input}
        ],
        "stream": True
    }
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("POST", api_url, headers=headers, json=payload) as response:
                response.raise_for_status()
                
                full_response = ""
                buffer = ""
                done = False

                async for text_chunk in response.aiter_text():
                    if done:
                        break
                    
                    buffer += text_chunk
                    
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        
                        if not line.strip():
                            continue
                        
                        if line.startswith("data: "):
                            data_line = line[len("data: "):].strip()
                            if data_line == "[DONE]":
                                done = True
                                break
                            
                            try:
                                data = json.loads(data_line)
                                if "choices" in data and len(data["choices"]) > 0:
                                    delta = data["choices"][0].get("delta", {})
                                    content = delta.get("content")
                                    if content:
                                        full_response += content
                                        if yield_tokens:
                                            yield sse("token_delta", {"delta": content})
                            except json.JSONDecodeError:
                                # Incomplete JSON, put it back and wait for the next chunk
                                buffer = line + "\n" + buffer
                                break
                
                yield {"type": "final", "content": full_response}

    except Exception as e:
        print(f"\n❌ LLM API调用失败: {str(e)}")
        import traceback
        traceback.print_exc()
        raise
