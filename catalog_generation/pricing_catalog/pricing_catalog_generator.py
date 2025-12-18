# -*- coding: utf-8 -*-
import json
from typing import AsyncGenerator, List, Dict

from fastmcp import Client as MCPClient
from ..config import settings
from ..linking.linker import run_template_linking_pipeline
from ..utils.mcp_utils import sse, mcp_read_file, mcp_smart_write
from ..utils.file_utils import extract_section_as_json

async def generate_pricing_catalog_event_generator(language: str = "zh") -> AsyncGenerator[str, None]:
    """
    生成报价目录并关联模板。
    """
    yield sse("phase_start", {"name": "生成报价目录"})

    # 1. 从 format_framework.json 中提取报价部分
    pricing_section = []
    try:
        async with MCPClient(settings.MCP_SERVER_URL) as mcp_client:
            framework_content = await mcp_read_file(mcp_client, settings.OUTPUT_PATHS["format_framework"])
        
        if framework_content:
            full_framework = json.loads(framework_content)
            # 使用一个辅助函数来提取特定部分
            pricing_section = extract_section_as_json(full_framework, "价格") or extract_section_as_json(full_framework, "报价")
        
        if not pricing_section:
            yield sse("warning", {"phase": "生成报价目录", "text": "未在格式框架中找到报价/价格部分。"})
            return

    except Exception as e:
        yield sse("error", {"message": f"提取报价部分失败: {e}"})
        return

    # 2. 将提取出的部分保存为一个临时的 catalog 文件，以便链接器使用
    temp_pricing_catalog_path = "temp_pricing_catalog.json"
    try:
        async with MCPClient(settings.MCP_SERVER_URL) as mcp_client:
            await mcp_smart_write(
                mcp_client,
                temp_pricing_catalog_path,
                json.dumps(pricing_section, ensure_ascii=False, indent=2)
            )
    except Exception as e:
        yield sse("error", {"message": f"创建临时报价目录文件失败: {e}"})
        return

    # 3. 调用通用的模板关联流程
    async for event in run_template_linking_pipeline(
        catalog_input_path=temp_pricing_catalog_path,
        templates_input_path=settings.INPUT_PATHS["templates"],
        catalog_output_path=settings.OUTPUT_PATHS["pricing_catalog_linked"],
        language=language
    ):
        yield event

    # 4. 清理临时文件 (可选，但推荐)
    try:
        async with MCPClient(settings.MCP_SERVER_URL) as mcp_client:
            await mcp_client.call_tool("delete_file", {"path": temp_pricing_catalog_path})
    except Exception:
        pass # 清理失败不影响主流程

    yield sse("phase_end", {"name": "生成报价目录"})
    yield sse("complete", {"final_output": "报价目录模板关联完成！"})
