# -*- coding: utf-8 -*-
"""
@File    : format_extractor.py
@Description: This file contains the logic for extracting and enriching the format framework from tender documents.
@Author  : <<your name>>
@Date    : <<date>>
@Version : 1.0
"""

# Input: Path to intermediate chunks file.
# Output: SSE events indicating the progress and result of the format extraction process.

import json
import re
import traceback
import uuid
from typing import Dict, Any, List, AsyncGenerator

from agents import Agent
from fastmcp import Client as MCPClient

from ..config import settings
from ..utils.mcp_utils import sse, mcp_read_file, mcp_smart_write, call_llm_streaming
from ..utils.file_utils import (
    build_nested_catalog,
    extract_leaf_nodes,
    locate_text_segment,
    extract_json_from_response
)


# ==============================================================================
# Agent Definitions
# ==============================================================================

def bid_format_extractor_agent(language: str = "zh") -> Agent:
    """
    创建"投标文件格式提取"Agent (阶段 1)。
    """
    return Agent(
        name="BidFormatExtractorAgent",
        instructions=(
            """你是一位专业的招标文件结构分析专家。

你的任务是从用户提供的招标文件文本中，精准地定位并提取"投标文件格式要求"相关章节的**内部目录结构**。

# 核心任务：
1.  **定位目标章节**：在文本中寻找标题为"投标文件格式要求"、"投标文件编制要求"、"投标文件组成"或类似含义的章节。
2.  **忽略容器标题**：在开始提取前，你必须忽略掉这个章节本身的标题（例如，忽略“第六章 投标文件格式”）。**你的输出绝对不能包含这个容器标题。**
3.  **设定层级基准**：将该章节**内部**的最高层级标题（如“第一部分 商务部分”）视为 `level: 1`。
4.  **提取内部结构**：提取出所有`level: 1`标题下的所有子目录项。
5.  **计算相对层级**：根据子目录项的缩进或序号格式，正确计算它们相对于`level: 1`的层级（`level: 2`, `level: 3`...）。
6.  **清理名称**：在输出 `name` 字段时，必须彻底清除所有的前导序号和标点。
7.  **输出扁平JSON**：将提取的目录结构，转换为一个带有 `level` 和 `name` 属性的扁平JSON数组。

# 输出格式示例：
```json
[
  {"level": 1, "name": "第一部分 商务部分"},
  {"level": 2, "name": "投标函"},
  {"level": 2, "name": "授权委托书"},
  {"level": 1, "name": "第二部分 技术部分"},
  {"level": 2, "name": "服务方案"}
]
```

# 重要提示：
-   只输出 JSON 数组，不要包含任何额外的解释或说明文字。
-   如果未找到相关章节，返回空数组 `[]`。
-   如遇到"联合体协议书"的目录请直接忽略

# JSON 输出的绝对规则：
- **严格验证**：你的最终输出**必须**是一个能被 `json.loads` 函数成功解析的、语法完全正确的 JSON 数组。
- **逗号**：确保每个 JSON 对象之间都有逗号，但最后一个对象后面绝对不能有。对象内部的 `key: value` 对之间也必须有逗号。
- **引号**：所有的键（如 "level", "name"）和字符串值都必须用双引号 `"` 包裹。
- **无额外字符**：不要在 JSON 结构之外添加任何注释、解释或任何其他文字。"""
            f"\n- 输出语言: {language}"
        ),
    )


def catalog_description_enrichment_agent(language: str = "zh") -> Agent:
    """
    创建"目录内容描述提取"Agent（阶段1.5）。
    """
    return Agent(
        name="CatalogDescriptionEnrichmentAgent",
        instructions=(
            """你是一个招标文件分析专家，负责为后续目录生成提供精确指引。

**任务目标：**
以第一人称视角描述我看到的内容，明确指出该目录项下包含的文字、模板、表格及附件要求。
这种描述将帮助后续 Agent 判断哪些目录项仅为文字模板，哪些需要新增子目录。

**核心原则：**
- 完全基于原文，不要猜测、推断、臆造内容
- 不要使用“通常”“可能”“一般”等推测性词汇
- 只有原文明确说明的，才描述存在

**分析要点（以第一人称描述）：**
1. 我在这个目录项下看到了什么内容（文字、段落、表格、填空模板等）
2. 我看到的内容中，需要填写/提供哪些信息或材料
3. 我注意到的任何特殊要求或注意事项
4. 我看到是否明确要求附加证明材料，如果有，列出原文中的材料清单
5. 如果是文字模板，我会明确说明这是一个模板，是否包含下划线填空、括号说明等

**输出要求：**
- 用条目式描述我看到的内容，每条目简洁清晰
- 对叶子节点（children为空），重点描述材料清单和附件要求
- 对于模板文字，明确说明“这是一个模板，需要填写以下信息”
- 对于需附加材料的节点，列出附件名称或材料清单
- 如果原文没有具体内容描述，content_description 为空字符串
- 输出完整的 JSON 目录结构，保留所有层级，保证 machine-readable

**示例条目描述：**
- 我看到一个文字模板，需要投标人填写公司名称、地址和联系人信息
- 表格下方有备注：需附录营业执照复印件
- 我看到一段说明文字，没有需要填写的内容
- 这是一个模板表格，包含下划线填空，需要填写项目基本信息
- 该目录项要求附加材料：身份证复印件、资质证书

请严格按照JSON格式输出完整的目录结构（包含所有层级），确保每个目录项的 content_description 清晰可读。"""
            f"\n- 输出语言: {language}"
        ),
    )


# ==============================================================================
# Core Logic
# ==============================================================================

async def extract_format_framework_event_generator(
    intermediate_chunks_path: str = settings.INPUT_PATHS["intermediate_chunks"],
    model_name: str = settings.DEFAULT_MODEL_NAME,
    language: str = "zh",
    return_source_chunk: bool = False
) -> AsyncGenerator[str, None]:
    """
    阶段1：提取投标文件格式框架。
    """
    mcp_client = MCPClient(settings.MCP_SERVER_URL)
    
    try:
        yield sse("phase_start", {"name": "提取格式框架"})
        
        async with mcp_client:
            chunks_content = await mcp_read_file(mcp_client, intermediate_chunks_path)

        if not chunks_content:
            yield sse("error", {"message": f"无法读取分块文件: {intermediate_chunks_path}"})
            return
            
        try:
            chunks = json.loads(chunks_content)
            if not isinstance(chunks, list):
                raise json.JSONDecodeError("文件内容不是一个列表", chunks_content, 0)
        except json.JSONDecodeError:
            yield sse("error", {"message": f"解析分块文件失败: {intermediate_chunks_path}"})
            return
            
        format_agent = bid_format_extractor_agent(language=language)
        format_framework_flat = []
        source_chunk_text = ""

        yield sse("note", {"phase": "提取格式框架", "text": f"开始逐块分析 {len(chunks)} 个文本块..."})

        for i, chunk_info in enumerate(chunks):
            chunk_text = chunk_info.get("content", "")
            if not chunk_text:
                continue

            yield sse("update", {"phase": "提取格式框架", "progress": f"{i+1}/{len(chunks)}"})
            
            full_response = ""
            try:
                system_prompt = format_agent.instructions
                user_input = f"请从以下文本中提取投标文件格式要求：\n\n---\n\n{chunk_text}"
                
                log_id = f"log-{uuid.uuid4()}"
                yield sse("debug_log", {"title": f"BidFormatExtractorAgent Output (块 {i+1}/{len(chunks)})", "log_id": log_id})

                result_stream_generator = call_llm_streaming(
                    system_prompt=system_prompt,
                    user_input=user_input,
                    model_name=model_name,
                    yield_tokens=True
                )
                
                full_response = ""
                async for event in result_stream_generator:
                    if 'event: token_delta' in event:
                        try:
                            data_line = next(line for line in event.split('\n') if line.startswith('data: '))
                            data = json.loads(data_line[len('data: '):])
                            delta = data.get('delta', '')
                            if delta:
                                full_response += delta
                                yield sse("debug_token_delta", {"log_id": log_id, "delta": delta})
                        except (StopIteration, json.JSONDecodeError):
                            pass

            except Exception as e:
                yield sse("warning", {"phase": "提取格式框架", "text": f"处理块 {i+1} 时LLM调用失败: {str(e)}"})
                continue
            
            try:
                format_output = extract_json_from_response(full_response)
                current_chunk_result = json.loads(format_output)
                
                if isinstance(current_chunk_result, list) and len(current_chunk_result) > 0:
                    yield sse("note", {"phase": "提取格式框架", "text": f"在第 {i+1} 块中成功识别到目录框架，提取完成。"})
                    format_framework_flat = current_chunk_result
                    source_chunk_text = chunk_text
                    break
            except (json.JSONDecodeError, AttributeError):
                continue
        
        if not format_framework_flat:
             yield sse("warning", {"phase": "提取格式框架", "text": "未能在任何文本块中找到有效的目录框架。"})

        format_framework = build_nested_catalog(format_framework_flat)
        
        async with mcp_client:
            await mcp_smart_write(
                mcp_client,
                settings.OUTPUT_PATHS["format_framework"],
                json.dumps(format_framework, ensure_ascii=False, indent=2)
            )
        
        yield sse("artifact", {"type": "file", "filename": settings.OUTPUT_PATHS["format_framework"]})
        yield sse("note", {"phase": "提取格式框架", "text": f"格式框架提取完成，共识别 {len(format_framework)} 个顶级部分。"})
        yield sse("phase_end", {"name": "提取格式框架"})
        
        complete_data = {
            "final_output": "格式框架提取完成！",
            "framework": format_framework
        }
        if return_source_chunk:
            complete_data["source_chunk"] = source_chunk_text
        yield sse("complete", complete_data)
        
    except Exception as e:
        tb_str = traceback.format_exc()
        error_info = {"type": type(e).__name__, "message": str(e), "traceback": tb_str}
        yield sse("error", error_info)
        print(f"详细错误信息:\n{tb_str}")
    finally:
        print("格式框架提取任务已终止或完成。")


async def enrich_catalog_descriptions_event_generator(
    format_framework: List[Dict[str, Any]],
    source_chunk_text: str,
    model_name: str = settings.DEFAULT_MODEL_NAME,
    batch_size: int = 8,
    language: str = "zh"
) -> AsyncGenerator[str, None]:
    """
    阶段2：为格式框架中的叶子节点添加内容描述。
    """
    try:
        yield sse("phase_start", {"name": "添加目录内容描述"})
        
        leaf_nodes = extract_leaf_nodes(format_framework)
        
        if not leaf_nodes:
            yield sse("warning", {"phase": "添加目录内容描述", "text": "未找到叶子节点，跳过描述添加。"})
            yield sse("phase_end", {"name": "添加目录内容描述"})
            return
        
        yield sse("note", {"phase": "添加目录内容描述", "text": f"识别到 {len(leaf_nodes)} 个叶子节点，将分 {(len(leaf_nodes) + batch_size - 1) // batch_size} 批处理"})
        
        for batch_idx in range(0, len(leaf_nodes), batch_size):
            batch = leaf_nodes[batch_idx:batch_idx + batch_size]
            batch_num = batch_idx // batch_size + 1
            total_batches = (len(leaf_nodes) + batch_size - 1) // batch_size
            
            yield sse("update", {"phase": "添加目录内容描述", "progress": f"批次 {batch_num}/{total_batches}"})
            
            batch_catalog = []
            text_segments_info = []
            
            for node_info in batch:
                text_segment = locate_text_segment(node_info['name'], source_chunk_text, context_lines=50)
                
                batch_catalog.append({
                    'name': node_info['name'],
                    'children': []
                })
                
                text_segments_info.append({
                    'catalog_name': node_info['name'],
                    'text_segment': text_segment if text_segment else "（未找到相关文本）"
                })
            
            user_input = f"""需要识别的目录内容：
{json.dumps(batch_catalog, ensure_ascii=False, indent=2)}

原始文本片段：
"""
            
            for seg_info in text_segments_info:
                user_input += f"\n【{seg_info['catalog_name']}】对应的文本：\n{seg_info['text_segment']}\n\n{'='*50}\n"
            
            user_input += "\n**请严格按照json格式输出，包含所有目录项的 content_description 字段**"
            
            description_agent = catalog_description_enrichment_agent(language=language)
            
            response_text = ""
            try:
                system_prompt = description_agent.instructions
                
                log_id = f"log-{uuid.uuid4()}"
                yield sse("debug_log", {"title": f"CatalogDescriptionEnrichmentAgent Output (批次 {batch_num}/{total_batches})", "log_id": log_id})

                result_stream_generator = call_llm_streaming(
                    system_prompt=system_prompt,
                    user_input=user_input,
                    model_name=model_name,
                    yield_tokens=True
                )
                
                response_text = ""
                async for event in result_stream_generator:
                    if 'event: token_delta' in event:
                        try:
                            data_line = next(line for line in event.split('\n') if line.startswith('data: '))
                            data = json.loads(data_line[len('data: '):])
                            delta = data.get('delta', '')
                            if delta:
                                response_text += delta
                                yield sse("debug_token_delta", {"log_id": log_id, "delta": delta})
                        except (StopIteration, json.JSONDecodeError):
                            pass

            except Exception as e:
                yield sse("warning", {"phase": "添加目录内容描述", "text": f"批次 {batch_num} 处理失败: {str(e)}"})
                continue
            
            try:
                json_match = re.search(r'```json\s*(.*?)\s*```', response_text, re.DOTALL)
                if json_match:
                    json_str = json_match.group(1)
                else:
                    json_str = response_text
                
                result = json.loads(json_str)
                if not isinstance(result, list):
                    result = [result]
                
                for i, node_info in enumerate(batch):
                    if i < len(result):
                        description = result[i].get('content_description', '')
                        node_info['node']['content_description'] = description
                        
                        if description:
                            yield sse("note", {"phase": "添加目录内容描述", "text": f"✅ 已填充: {node_info['name'][:30]}..."})
                
            except (json.JSONDecodeError, KeyError) as e:
                yield sse("warning", {"phase": "添加目录内容描述", "text": f"批次 {batch_num} 解析失败: {str(e)}"})
                continue
        
        yield sse("note", {"phase": "添加目录内容描述", "text": "所有节点处理完毕，目录内容描述添加完成。"})
        
        mcp_client = MCPClient(settings.MCP_SERVER_URL)
        json_content = json.dumps(format_framework, ensure_ascii=False, indent=2)
        
        async with mcp_client:
            await mcp_smart_write(
                mcp_client,
                settings.OUTPUT_PATHS["format_framework"],
                json_content
            )
        
        yield sse("artifact", {"type": "file", "filename": settings.OUTPUT_PATHS["format_framework"]})
        yield sse("note", {"phase": "添加目录内容描述", "text": f"已更新 {settings.OUTPUT_PATHS['format_framework']} 文件。"})
        yield sse("phase_end", {"name": "添加目录内容描述"})
        
    except Exception as e:
        tb_str = traceback.format_exc()
        error_info = {"type": type(e).__name__, "message": str(e), "traceback": tb_str}
        yield sse("error", error_info)
        print(f"详细错误信息:\n{tb_str}")
    finally:
        print("目录内容描述添加任务已终止或完成。")
