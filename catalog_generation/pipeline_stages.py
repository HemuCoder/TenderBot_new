# -*- coding: utf-8 -*-
"""
============================================================
PIPELINE STAGES
流水线各阶段实现
============================================================
"""

import json
import os
import time
import uuid
from typing import Any, AsyncGenerator
from fastmcp import Client as MCPClient

from .pipeline_core import PipelineStage, StageResult
from .utils.mcp_utils import sse, mcp_read_file, mcp_smart_write
from .config import settings
from .data_preprocessing.format_extractor import (
    extract_format_framework_event_generator,
    enrich_catalog_descriptions_event_generator
)
from .compensation.orchestrator import CompensationOrchestrator
from .compensation.classifier import CatalogClassifier


class FrameworkExtractionStage(PipelineStage):
    """阶段1: 提取框架"""
    
    def __init__(self, model: str, language: str):
        super().__init__("提取格式框架")
        self.model = model
        self.language = language
    
    async def _run(self, input_data: Any) -> AsyncGenerator:
        format_framework = None
        source_chunk = None
        
        # 调用原有的提取逻辑
        gen = extract_format_framework_event_generator(
            model_name=self.model,
            language=self.language,
            return_source_chunk=True
        )
        
        async for event_str in gen:
            # 解析完成事件,提取数据
            if 'event: complete' in event_str:
                try:
                    data_line = next(line for line in event_str.split('\n') 
                                   if line.startswith('data: '))
                    data_json = data_line[len('data: '):]
                    data = json.loads(data_json)
                    format_framework = data.get('framework')
                    source_chunk = data.get('source_chunk')
                except (StopIteration, json.JSONDecodeError):
                    pass
            else:
                yield event_str
        
        # 返回结果
        yield StageResult(
            data={
                "framework": format_framework,
                "source_chunk": source_chunk
            },
            metadata={"model": self.model, "language": self.language}
        )


class DescriptionEnrichmentStage(PipelineStage):
    """阶段2: 丰富描述"""
    
    def __init__(self, model: str, language: str):
        super().__init__("添加目录内容描述")
        self.model = model
        self.language = language
    
    async def _run(self, input_data: Any) -> AsyncGenerator:
        framework = input_data.get("framework")
        source_chunk = input_data.get("source_chunk")
        
        if not framework or not source_chunk:
            yield self.warning("未能从上一阶段获取有效框架,跳过描述生成")
            yield StageResult(data=input_data)
            return
        
        # 调用原有的丰富描述逻辑
        gen = enrich_catalog_descriptions_event_generator(
            format_framework=framework,
            source_chunk_text=source_chunk,
            model_name=self.model,
            language=self.language
        )
        
        enriched_framework = None
        async for event_str in gen:
            # 解析完成事件
            if 'event: complete' in event_str:
                try:
                    data_line = next(line for line in event_str.split('\n') 
                                   if line.startswith('data: '))
                    data_json = data_line[len('data: '):]
                    data = json.loads(data_json)
                    enriched_framework = data.get('framework', framework)
                except (StopIteration, json.JSONDecodeError):
                    enriched_framework = framework
            else:
                yield event_str
        
        # 返回丰富后的框架
        yield StageResult(
            data={
                "framework": enriched_framework or framework,
                "source_chunk": source_chunk
            }
        )


class CompensationStage(PipelineStage):
    """阶段3: 补偿与分类"""
    
    def __init__(self):
        super().__init__("目录补偿与分类")
    
    async def _run(self, input_data: Any) -> AsyncGenerator:
        framework = input_data.get("framework")
        
        if not framework:
            yield self.warning("框架数据为空,跳过补偿流程")
            yield StageResult(data=input_data)
            return
        
        yield self.note(f"开始分析和补偿目录结构,共 {len(framework)} 个顶层节点")
        yield self.note("🤖 ReAct Agent 正在分析目录结构...")
        yield self.note("⏳ 这可能需要 30-60 秒,请耐心等待...")
        
        # 准备日志
        log_dir = os.path.join(settings._OUTPUT_DIR, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "compensation_log.txt")
        
        log_id = f"log-{uuid.uuid4()}"
        agent_logs = []
        
        yield sse("debug_log", {"title": "ReAct Agent 推理过程", "log_id": log_id})
        
        def agent_log_callback(msg):
            agent_logs.append(msg)
        
        # 执行补偿
        orchestrator = CompensationOrchestrator()
        start_time = time.time()
        
        yield self.note(f"⏱️ 开始时间: {time.strftime('%H:%M:%S')}")
        
        result = await orchestrator.run(
            framework,
            log_file=log_file,
            log_callback=agent_log_callback
        )
        
        # 输出日志
        if agent_logs:
            full_log = "\n".join(agent_logs)
            yield sse("debug_token_delta", {"log_id": log_id, "delta": full_log})
        
        elapsed = time.time() - start_time
        yield self.note(f"⏱️ 完成时间: {time.strftime('%H:%M:%S')} (耗时 {elapsed:.1f}秒)")
        yield self.note("✅ Agent 分析完成!")
        
        compensated_framework = result["compensated_structure"]
        
        yield self.note(f"补偿完成,来源: {result['source']}")
        yield self.note(f"最终结构: {len(compensated_framework)} 个顶层节点")
        
        # 保存补偿后的结构
        compensated_path = os.path.join(settings._OUTPUT_DIR, "format_framework_compensated.json")
        
        async with MCPClient(settings.MCP_SERVER_URL) as mcp_client:
            await mcp_smart_write(
                mcp_client,
                compensated_path,
                json.dumps(compensated_framework, ensure_ascii=False, indent=2)
            )
        
        yield self.artifact(compensated_path)
        yield self.note("✅ 补偿后的结构已保存")
        
        # 分类生成三个视图
        yield self.note("📂 开始分类生成商务/技术/报价视图...")
        
        classifier = CatalogClassifier()
        views = classifier.classify_and_split(compensated_framework)
        
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
                yield self.artifact(view_path)
                yield self.note(f"✅ {view_type.upper()} 视图: {node_count} 个顶层节点")
        
        yield self.note("📂 三个分类视图已生成完毕")
        
        # 返回补偿后的框架
        yield StageResult(
            data={
                "framework": compensated_framework,
                "views": views
            }
        )
