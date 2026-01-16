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
        return
    
    # 阶段 1.8: 目录补偿与分类（新增）
    yield sse("note", {"phase": "流程控制", "text": "✅ 准备进入补偿逻辑..."})
    yield sse("phase_start", {"name": "目录补偿与分类"})
    
    # 重新读取最新的 format_framework（阶段1.5可能已更新）
    try:
        yield sse("note", {"phase": "目录补偿", "text": f"正在读取文件: {settings.OUTPUT_PATHS['format_framework']}"})
        async with MCPClient(settings.MCP_SERVER_URL) as mcp_client:
            framework_content = await mcp_read_file(mcp_client, settings.OUTPUT_PATHS["format_framework"])
            if framework_content:
                format_framework = json.loads(framework_content)
                yield sse("note", {"phase": "目录补偿", "text": f"✅ 成功读取框架,共 {len(format_framework)} 个顶层节点"})
            else:
                yield sse("warning", {"phase": "目录补偿", "text": "⚠️ 文件内容为空"})
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        yield sse("warning", {"phase": "目录补偿", "text": f"读取最新框架失败: {str(e)}"})
        yield sse("warning", {"phase": "目录补偿", "text": f"详细错误:\n{error_detail}"})
    
    if format_framework:
        yield sse("note", {"phase": "目录补偿", "text": "✅ 框架数据有效,开始补偿流程"})
        try:
            from .compensation.orchestrator import CompensationOrchestrator
            
            orchestrator = CompensationOrchestrator()
            
            # 准备日志文件路径
            log_dir = os.path.join(settings._OUTPUT_DIR, "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, "compensation_log.txt")
            
            yield sse("note", {"phase": "目录补偿", "text": f"开始分析和补偿目录结构,共 {len(format_framework)} 个顶层节点"})
            yield sse("note", {"phase": "目录补偿", "text": "🤖 ReAct Agent 正在分析目录结构..."})
            yield sse("note", {"phase": "目录补偿", "text": "⏳ 这可能需要 30-60 秒,请耐心等待..."})
            
            # 创建日志收集器
            import uuid
            log_id = f"log-{uuid.uuid4()}"
            agent_logs = []
            
            yield sse("debug_log", {"title": "ReAct Agent 推理过程", "log_id": log_id})
            
            def agent_log_callback(msg):
                """Agent 日志回调,收集日志"""
                agent_logs.append(msg)
            
            # 执行补偿
            import time
            start_time = time.time()
            yield sse("note", {"phase": "目录补偿", "text": f"⏱️ 开始时间: {time.strftime('%H:%M:%S')}"})
            
            result = await orchestrator.run(format_framework, log_file=log_file, log_callback=agent_log_callback)
            
            # 输出收集到的日志
            if agent_logs:
                full_log = "\n".join(agent_logs)
                yield sse("debug_token_delta", {"log_id": log_id, "delta": full_log})
            
            elapsed = time.time() - start_time
            yield sse("note", {"phase": "目录补偿", "text": f"⏱️ 完成时间: {time.strftime('%H:%M:%S')} (耗时 {elapsed:.1f}秒)"})
            yield sse("note", {"phase": "目录补偿", "text": "✅ Agent 分析完成!"})
            
            # 更新 format_framework
            format_framework = result["compensated_structure"]
            
            yield sse("note", {"phase": "目录补偿", "text": f"补偿完成,来源: {result['source']}"})
            yield sse("note", {"phase": "目录补偿", "text": f"最终结构: {len(format_framework)} 个顶层节点"})
            
            # 保存补偿后的结构(覆盖原文件,让后续模块自动使用)
            async with MCPClient(settings.MCP_SERVER_URL) as mcp_client:
                # 1. 保存补偿后的完整框架(覆盖原文件)
                await mcp_smart_write(
                    mcp_client,
                    settings.OUTPUT_PATHS["format_framework"],
                    json.dumps(format_framework, ensure_ascii=False, indent=2)
                )
                
                # 2. 同时保存一份备份
                compensated_path = os.path.join(settings._OUTPUT_DIR, "format_framework_compensated.json")
                await mcp_smart_write(
                    mcp_client,
                    compensated_path,
                    json.dumps(format_framework, ensure_ascii=False, indent=2)
                )
            
            yield sse("note", {"phase": "目录补偿", "text": "✅ 补偿后的结构已保存(已覆盖原文件)"})
            yield sse("artifact", {"type": "file", "filename": settings.OUTPUT_PATHS["format_framework"]})

            
            # 阶段 1.9: 分类并生成三个视图
            yield sse("note", {"phase": "目录补偿", "text": "📂 开始分类生成商务/技术/报价视图..."})
            
            try:
                from .compensation.classifier import CatalogClassifier
                
                classifier = CatalogClassifier()
                views = classifier.classify_and_split(format_framework)
                
                # 保存三个视图
                view_paths = {
                    "business": os.path.join(settings._OUTPUT_DIR, "business_framework.json"),
                    "technical": os.path.join(settings._OUTPUT_DIR, "technical_framework.json"),
                    "pricing": os.path.join(settings._OUTPUT_DIR, "pricing_framework.json")
                }
                
                async with MCPClient(settings.MCP_SERVER_URL) as mcp_client:
                    for view_type, view_data in views.items():
                        view_path = view_paths[view_type]
                        await mcp_smart_write(
                            mcp_client,
                            view_path,
                            json.dumps(view_data, ensure_ascii=False, indent=2)
                        )
                        
                        node_count = len(view_data)
                        yield sse("artifact", {"type": "file", "filename": view_path})
                        yield sse("note", {"phase": "目录补偿", "text": f"✅ {view_type.upper()} 视图: {node_count} 个顶层节点"})
                
                yield sse("note", {"phase": "目录补偿", "text": "📂 三个分类视图已生成完毕"})
                
            except Exception as e:
                import traceback
                error_detail = traceback.format_exc()
                yield sse("warning", {"phase": "目录补偿", "text": f"分类视图生成失败: {str(e)}"})
                yield sse("warning", {"phase": "目录补偿", "text": f"详细错误: {error_detail}"})
            
        except Exception as e:
            import traceback
            error_detail = traceback.format_exc()
            yield sse("warning", {"phase": "目录补偿", "text": f"补偿过程出错: {str(e)}"})
            yield sse("warning", {"phase": "目录补偿", "text": f"详细错误: {error_detail}"})
    else:
        yield sse("warning", {"phase": "目录补偿", "text": "⚠️ 框架数据为空,跳过补偿流程"})
    
    yield sse("phase_end", {"name": "目录补偿与分类"})

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
