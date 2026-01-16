# -*- coding: utf-8 -*-
"""
@File    : main.py
@Description: This file is the main entry point for the catalog generation process.
@Author  : <<your name>>
@Date    : <<date>>
@Version : 1.0
"""

# Input: File paths and model configurations.
# Output: SSE events for the entire catalog generation pipeline.

import json
from typing import AsyncGenerator

from .config import settings
from .data_preprocessing.format_extractor import (
    extract_format_framework_event_generator,
    enrich_catalog_descriptions_event_generator,
)
from .compensation.orchestrator import CompensationOrchestrator
from .utils.mcp_utils import sse
from .business_catalog.business_catalog_generator import generate_business_catalog_v2_event_generator
from .technical_catalog.technical_catalog_generator import generate_technical_catalog_event_generator


async def event_generator(
    intermediate_chunks_path: str = settings.INPUT_PATHS["intermediate_chunks"],
    final_checklist_path: str = settings.INPUT_PATHS["final_checklist"],
    model_name: str = settings.DEFAULT_MODEL_NAME,
    language: str = "zh"
) -> AsyncGenerator[str, None]:
    """
    目录生成流水线的总入口。
    """
    format_framework = None
    source_chunk = None
    
    # 阶段1：提取格式框架
    async for event in extract_format_framework_event_generator(
        intermediate_chunks_path=intermediate_chunks_path,
        model_name=model_name,
        language=language,
        return_source_chunk=True
    ):
        if 'event: complete' in event:
            try:
                lines = event.strip().split('\n')
                for line in lines:
                    if line.startswith('data: '):
                        data = json.loads(line[6:])
                        if 'framework' in data:
                            format_framework = data['framework']
                        if 'source_chunk' in data:
                            source_chunk = data['source_chunk']
            except:
                pass
        yield event
    
    # 阶段1.5：添加目录内容描述
    if format_framework and source_chunk:
        async for event in enrich_catalog_descriptions_event_generator(
            format_framework=format_framework,
            source_chunk_text=source_chunk,
            model_name=model_name,
            language=language
        ):
            yield event
    
    # 阶段1.8：目录补偿（新增）
    yield sse("phase_start", {"name": "目录补偿与分类"})
    
    if format_framework:
        try:
            orchestrator = CompensationOrchestrator()
            
            # 准备日志文件路径
            import os
            log_dir = os.path.join(settings._OUTPUT_DIR, "logs")
            os.makedirs(log_dir, exist_ok=True)
            log_file = os.path.join(log_dir, "compensation_log.txt")
            
            yield sse("note", {"phase": "目录补偿", "text": f"开始分析和补偿目录结构,共 {len(format_framework)} 个顶层节点"})
            
            # 执行补偿
            result = await orchestrator.run(format_framework, log_file=log_file)
            
            # 更新 format_framework
            format_framework = result["compensated_structure"]
            
            yield sse("note", {"phase": "目录补偿", "text": f"补偿完成,来源: {result['source']}"})
            yield sse("note", {"phase": "目录补偿", "text": f"最终结构: {len(format_framework)} 个顶层节点"})
            
            # 保存补偿后的结构
            compensated_path = os.path.join(settings._OUTPUT_DIR, "format_framework_compensated.json")
            with open(compensated_path, 'w', encoding='utf-8') as f:
                json.dump(format_framework, f, ensure_ascii=False, indent=2)
            
            yield sse("artifact", {"type": "file", "filename": compensated_path})
            
        except Exception as e:
            yield sse("warning", {"phase": "目录补偿", "text": f"补偿过程出错: {str(e)}"})
    
    yield sse("phase_end", {"name": "目录补偿与分类"})
    
    # 阶段2：生成商务目录
    async for event in generate_business_catalog_v2_event_generator(
        format_framework=format_framework,
        model_name=model_name,
        language=language
    ):
        yield event

    # 阶段3：生成技术目录
    async for event in generate_technical_catalog_event_generator(
        final_checklist_path=final_checklist_path,
        model_name=model_name,
        language=language
    ):
        yield event
