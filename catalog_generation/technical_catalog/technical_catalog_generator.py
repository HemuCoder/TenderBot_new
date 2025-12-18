# -*- coding: utf-8 -*-
"""
@File    : technical_catalog_generator.py
@Description: This file contains the logic for generating the technical catalog.
@Author  : <<your name>>
@Date    : <<date>>
@Version : 1.0
"""

# Input: Path to the final checklist file.
# Output: SSE events indicating the progress and result of the technical catalog generation.

import json
from typing import Dict, Any, List, AsyncGenerator
import uuid

from fastmcp import Client as MCPClient
from ..config import settings
from ..utils.mcp_utils import sse, mcp_read_file, mcp_smart_write, call_llm_streaming
from ..utils.file_utils import (
    extract_section, 
    convert_json_to_markdown, 
    parse_markdown_to_json, 
    extract_technical_section,
    assign_ids_and_levels,
    add_empty_linking_field
)
from .agents import technical_requirement_integration_agent, technical_catalog_standardization_agent

async def generate_technical_catalog_event_generator(
    final_checklist_path: str,
    model_name: str,
    language: str = "zh"
) -> AsyncGenerator[str, None]:
    """
    阶段3：生成技术目录。
    """
    mcp_client = MCPClient(settings.MCP_SERVER_URL)
    
    try:
        yield sse("phase_start", {"name": "生成技术目录"})
        
        # ==============================================================================
        # 步骤1: 整合需求与参考目录
        # ==============================================================================
        yield sse("phase_start", {"name": "步骤1: 整合需求"})

        async with mcp_client:
            final_checklist_content = await mcp_read_file(mcp_client, final_checklist_path)
            reference_catalog_content = await mcp_read_file(mcp_client, settings.INPUT_PATHS["reference_catalog"])

        if not final_checklist_content:
            yield sse("error", {"message": "无法读取评分文件"})
            return
        
        tech_requirements = extract_section(final_checklist_content, "技术部分评分")
        if not tech_requirements:
            yield sse("warning", {"phase": "技术需求整合", "text": "未在评分文件中找到技术部分"})
            return

        tech_reference = extract_section(reference_catalog_content, "第二卷 技术文件") if reference_catalog_content else ""
        if not tech_reference:
            yield sse("warning", {"phase": "技术需求整合", "text": "未找到参考目录技术部分，将从零构建"})

        integration_agent = technical_requirement_integration_agent(language=language)
        integration_prompt = f"# 技术部分需求清单:\n{tech_requirements}\n\n# 基础目录结构（历史优秀目录参考）:\n{tech_reference}"

        integrated_catalog_md = ""
        log_id = f"log-{uuid.uuid4()}"
        yield sse("debug_log", {"title": "整合需求与参考目录", "log_id": log_id})

        async for event in call_llm_streaming(
            system_prompt=integration_agent.instructions,
            user_input=integration_prompt,
            model_name=model_name,
            yield_tokens=True
        ):
            if 'event: token_delta' in event:
                try:
                    data_line = next(line for line in event.split('\n') if line.startswith('data: '))
                    data = json.loads(data_line[len('data: '):])
                    delta = data.get('delta', '')
                    if delta:
                        integrated_catalog_md += delta
                        yield sse("debug_token_delta", {"log_id": log_id, "delta": delta})
                except (StopIteration, json.JSONDecodeError):
                    pass
        yield sse("phase_end", {"name": "步骤1: 整合需求"})

        # ==============================================================================
        # 步骤2: 标准化目录结构
        # ==============================================================================
        yield sse("phase_start", {"name": "步骤2: 标准化目录"})

        async with mcp_client:
            format_framework_content = await mcp_read_file(mcp_client, settings.OUTPUT_PATHS["format_framework"])
        
        standard_framework_text = "- 服务方案\n- 技术人员情况\n- 其他" # 默认框架
        if format_framework_content:
            try:
                format_framework_json = json.loads(format_framework_content)
                tech_framework = extract_technical_section(format_framework_json)
                if tech_framework:
                    standard_framework_text = convert_json_to_markdown(tech_framework, include_descriptions=False)
            except Exception:
                yield sse("warning", {"phase": "技术目录标准化", "text": "解析格式框架失败，使用默认框架"})
        
        standardization_agent = technical_catalog_standardization_agent(language=language)
        standardization_prompt = f"# 技术目录（待标准化）:\n{integrated_catalog_md}\n\n# 标准框架:\n{standard_framework_text}"

        standardized_catalog_md = ""
        log_id = f"log-{uuid.uuid4()}"
        yield sse("debug_log", {"title": "标准化目录结构", "log_id": log_id})

        async for event in call_llm_streaming(
            system_prompt=standardization_agent.instructions,
            user_input=standardization_prompt,
            model_name=model_name,
            yield_tokens=True
        ):
            if 'event: token_delta' in event:
                try:
                    data_line = next(line for line in event.split('\n') if line.startswith('data: '))
                    data = json.loads(data_line[len('data: '):])
                    delta = data.get('delta', '')
                    if delta:
                        standardized_catalog_md += delta
                        yield sse("debug_token_delta", {"log_id": log_id, "delta": delta})
                except (StopIteration, json.JSONDecodeError):
                    pass
        yield sse("phase_end", {"name": "步骤2: 标准化目录"})

        # ==============================================================================
        # 步骤3: 格式转换与保存
        # ==============================================================================
        yield sse("phase_start", {"name": "步骤3: 格式转换"})
        try:
            # 在转换前保存最终的 Markdown 文件
            md_output_path = settings.OUTPUT_PATHS["technical_catalog_standardized_md"]
            async with mcp_client:
                await mcp_smart_write(
                    mcp_client,
                    md_output_path,
                    standardized_catalog_md
                )
            yield sse("artifact", {"type": "file", "filename": md_output_path})

            catalog_json = parse_markdown_to_json(standardized_catalog_md)
            assign_ids_and_levels(catalog_json, prefix="tech")
            
            # 为所有节点添加空的链接字段以保持结构一致
            add_empty_linking_field(catalog_json)

            output_path = settings.OUTPUT_PATHS["technical_catalog"]
            async with mcp_client:
                await mcp_smart_write(
                    mcp_client,
                    output_path,
                    json.dumps(catalog_json, ensure_ascii=False, indent=2)
                )
            
            yield sse("artifact", {"type": "file", "filename": output_path})
            yield sse("note", {"phase": "格式转换", "text": "格式转换完成"})
        except Exception as e:
            yield sse("error", {"message": f"Markdown 解析为 JSON 失败: {e}"})

        yield sse("phase_end", {"name": "步骤3: 格式转换"})

        yield sse("phase_end", {"name": "生成技术目录"})
        yield sse("complete", {"final_output": "技术目录已成功生成！", "catalog": catalog_json})

    except Exception as e:
        error_info = {"type": type(e).__name__, "message": str(e)}
        yield sse("error", error_info)
    finally:
        print("技术目录生成任务已终止或完成。")
