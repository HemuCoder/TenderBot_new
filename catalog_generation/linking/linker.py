# -*- coding: utf-8 -*-
import json
import copy
import uuid
from typing import Dict, Any, List, AsyncGenerator

from fastmcp import Client as MCPClient

from ..config import settings
from ..utils.mcp_utils import sse, mcp_read_file, mcp_smart_write, call_llm_streaming
from .agents import template_linking_agent

def create_templates_markdown(templates_data: List[Dict]) -> str:
    """将模板数据转换为Markdown字符串。"""
    if not templates_data:
        return ""
    return "\n".join([f"{template.get('id')}: {template.get('name')}" for template in templates_data])

async def _find_and_update_leaf_nodes(
    nodes: List[Dict], 
    available_templates: List[Dict],
    language: str
) -> AsyncGenerator[str, None]:
    """递归查找叶子节点并用模板ID更新它们。"""
    linker_agent = template_linking_agent(language=language)
    system_prompt = linker_agent.instructions

    for node in nodes:
        if not node.get('children'):
            yield sse("update", {"phase": "模板关联", "text": f"处理: {node.get('name')}"})
            
            templates_md = create_templates_markdown(available_templates)
            
            user_content = f"""
模板库:
---
{templates_md}
---

当前目录项:
---
目录名称: {node.get('name', '')}
内容描述: {node.get('content_description', '')}
---
"""
            
            response_text = ""
            log_id = f"log-{uuid.uuid4()}"
            yield sse("debug_log", {"title": f"匹配模板: {node.get('name')}", "log_id": log_id})

            async for event in call_llm_streaming(
                system_prompt=system_prompt,
                user_input=user_content,
                model_name=settings.DEFAULT_MODEL_NAME,
                yield_tokens=True
            ):
                if 'event: token_delta' in event:
                    try:
                        data_line = next(line for line in event.split('\n') if line.startswith('data: '))
                        data = json.loads(data_line[len('data: '):])
                        delta = data.get('delta', '')
                        if delta:
                            response_text += delta.strip()
                            yield sse("debug_token_delta", {"log_id": log_id, "delta": delta})
                    except (StopIteration, json.JSONDecodeError):
                        pass

            matched_ids = []
            try:
                # 尝试解析完整的JSON数组
                parsed_json = json.loads(response_text)
                if isinstance(parsed_json, list):
                    matched_ids = [str(item) for item in parsed_json]
            except json.JSONDecodeError:
                # 兼容旧的、非JSON的单个ID输出
                if "N/A" not in response_text:
                    matched_ids = [response_text.strip()]

            yield sse("note", {"phase": "模板关联", "text": f" -> 匹配结果: {', '.join(matched_ids) or '无'}"})

            if matched_ids:
                node['linked_template_ids'] = matched_ids # 使用复数形式的键
                # 从可用模板中移除所有已匹配的
                available_templates[:] = [t for t in available_templates if t.get('id') not in matched_ids]
            else:
                node['linked_template_ids'] = []
        else:
            async for event in _find_and_update_leaf_nodes(node['children'], available_templates, language):
                yield event

async def run_template_linking_pipeline(
    catalog_input_path: str,
    templates_input_path: str,
    catalog_output_path: str,
    language: str = "zh"
) -> AsyncGenerator[str, None]:
    """通用的模板关联流程。"""
    mcp_client = MCPClient(settings.MCP_SERVER_URL)
    
    try:
        async with mcp_client:
            templates_content = await mcp_read_file(mcp_client, templates_input_path)
            catalog_content = await mcp_read_file(mcp_client, catalog_input_path)

        if not templates_content or not catalog_content:
            yield sse("error", {"message": f"无法加载模板 ({templates_input_path}) 或目录文件 ({catalog_input_path})"})
            return

        templates_data = json.loads(templates_content)
        catalog_data = json.loads(catalog_content)
        
        yield sse("note", {"phase": "模板关联", "text": "已成功加载模板和目录数据。"})

        catalog_data_copy = copy.deepcopy(catalog_data)
        available_templates = list(templates_data)
        
        async for event in _find_and_update_leaf_nodes(catalog_data_copy, available_templates, language):
            yield event

        async with mcp_client:
            await mcp_smart_write(
                mcp_client,
                catalog_output_path,
                json.dumps(catalog_data_copy, ensure_ascii=False, indent=2)
            )
        
        yield sse("artifact", {"type": "file", "filename": catalog_output_path})

    except Exception as e:
        yield sse("error", {"type": type(e).__name__, "message": str(e)})
    finally:
        print("通用模板关联任务已终止或完成。")
