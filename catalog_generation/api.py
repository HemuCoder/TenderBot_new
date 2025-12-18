# -*- coding: utf-8 -*-
"""
@File    : api.py
@Description: This file contains the FastAPI application for the catalog generation module.
@Author  : <<your name>>
@Date    : <<date>>
@Version : 1.0
"""

import uvicorn
from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import json
from .utils.mcp_utils import sse, mcp_read_file, mcp_smart_write
from fastmcp import Client as MCPClient
import os

from .data_preprocessing.format_extractor import (
    extract_format_framework_event_generator,
    enrich_catalog_descriptions_event_generator
)
from .business_catalog.business_catalog_generator import generate_business_catalog_v2_event_generator
from .technical_catalog.technical_catalog_generator import generate_technical_catalog_event_generator
from .pricing_catalog.pricing_catalog_generator import generate_pricing_catalog_event_generator
from .config import settings

async def run_full_catalog_pipeline(model: str, language: str):
    """
    运行完整的目录生成流水线，包括所有子流程和最终合并。
    """
    # 1. 生成框架
    async for event in run_framework_pipeline(model=model, language=language):
        yield event

    # 2. 生成商务目录（包含模板链接）
    async for event in generate_business_catalog_v2_event_generator(model_name=model, language=language):
        yield event

    # 3. 生成报价目录（包含模板链接）
    async for event in generate_pricing_catalog_event_generator(language=language):
        yield event

    # 4. 生成技术目录（包含模板链接）
    async for event in generate_technical_catalog_event_generator(
        final_checklist_path=settings.INPUT_PATHS["final_checklist"],
        model_name=model,
        language=language
    ):
        yield event
    
    # 5. 合并所有最终目录
    yield sse("phase_start", {"name": "合并最终目录"})
    try:
        full_catalog = []
        paths_to_merge = [
            settings.OUTPUT_PATHS["business_catalog_linked"],
            settings.OUTPUT_PATHS["pricing_catalog_linked"],
            settings.OUTPUT_PATHS["technical_catalog"],
        ]
        
        async with MCPClient(settings.MCP_SERVER_URL) as mcp_client:
            for path in paths_to_merge:
                content = await mcp_read_file(mcp_client, path)
                if content:
                    full_catalog.extend(json.loads(content))
        
        # 定义最终合并文件的输出路径
        full_catalog_path = os.path.join(settings._OUTPUT_DIR, "full_catalog_linked.json")
        
        async with MCPClient(settings.MCP_SERVER_URL) as mcp_client:
            await mcp_smart_write(
                mcp_client,
                full_catalog_path,
                json.dumps(full_catalog, ensure_ascii=False, indent=2)
            )
        
        yield sse("artifact", {"type": "file", "filename": full_catalog_path})
        yield sse("note", {"phase": "合并最终目录", "text": "所有目录已成功合并。"})
    except Exception as e:
        yield sse("error", {"message": f"合并最终目录失败: {e}"})

    yield sse("phase_end", {"name": "合并最终目录"})
    yield sse("complete", {"final_output": "完整目录已成功生成！"})


app = FastAPI()

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有来源
    allow_credentials=True,
    allow_methods=["*"],  # 允许所有方法
    allow_headers=["*"],  # 允许所有头
)

@app.get("/")
async def serve_homepage():
    return FileResponse("catalog_generation/catalog_debug.html")

async def run_framework_pipeline(model: str, language: str):
    """
    一个将“提取框架”和“丰富描述”两个阶段串联起来的工作流。
    """
    format_framework = None
    source_chunk = None

    # 阶段 1: 提取框架
    gen1 = extract_format_framework_event_generator(
        model_name=model, language=language, return_source_chunk=True
    )

    async for event_str in gen1:
        # 监听第一阶段的完成事件，以捕获其产出，用于下一阶段
        if 'event: complete' in event_str:
            try:
                data_line = next(line for line in event_str.split('\n') if line.startswith('data: '))
                data_json = data_line[len('data: '):]
                data = json.loads(data_json)
                format_framework = data.get('framework')
                source_chunk = data.get('source_chunk')
            except (StopIteration, json.JSONDecodeError):
                pass  # 如果解析失败，后续阶段将不会运行
        else:
            # 将非完成事件直接透传给前端
            yield event_str

    # 阶段 1.5: 丰富描述
    if format_framework and source_chunk:
        gen2 = enrich_catalog_descriptions_event_generator(
            format_framework=format_framework,
            source_chunk_text=source_chunk,
            model_name=model,
            language=language
        )
        async for event_str in gen2:
            yield event_str
    else:
        # 如果第一阶段失败，发送警告并正常结束流程
        yield sse("warning", {"phase": "添加目录内容描述", "text": "未能从格式提取阶段获取有效框架，跳过描述生成。"})
        yield sse("complete", {"final_output": "流程因第一阶段未产出有效结果而中止。"})

class CatalogRequest(BaseModel):
    model: str
    language: str

@app.post("/api/catalog/extract_framework")
async def api_extract_framework(request: CatalogRequest):
    return StreamingResponse(
        run_framework_pipeline(model=request.model, language=request.language),
        media_type="text/event-stream"
    )

@app.post("/api/catalog/generate_business_catalog")
async def api_generate_business_catalog(request: CatalogRequest):
    return StreamingResponse(
        generate_business_catalog_v2_event_generator(model_name=request.model, language=request.language),
        media_type="text/event-stream"
    )

@app.post("/api/catalog/generate_full_catalog")
async def api_generate_full_catalog(request: CatalogRequest):
    return StreamingResponse(
        run_full_catalog_pipeline(model=request.model, language=request.language),
        media_type="text/event-stream"
    )

@app.post("/api/catalog/generate_pricing_catalog")
async def api_generate_pricing_catalog(request: CatalogRequest):
    return StreamingResponse(
        generate_pricing_catalog_event_generator(language=request.language),
        media_type="text/event-stream"
    )

@app.post("/api/catalog/generate_technical_catalog")
async def api_generate_technical_catalog(request: CatalogRequest):
    return StreamingResponse(
        generate_technical_catalog_event_generator(
            final_checklist_path=settings.INPUT_PATHS["final_checklist"],
            model_name=request.model, 
            language=request.language
        ),
        media_type="text/event-stream"
    )

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
