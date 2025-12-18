import asyncio
import re
import os
import json
import random
from typing import List, Dict, Any, Callable
import tiktoken
import uuid
import docx
from docx.oxml.text.paragraph import CT_P
from docx.oxml.table import CT_Tbl

from agents import Runner, RunConfig, ModelSettings, Agent
from openai.types.responses import ResponseTextDeltaEvent
from agents.extensions.models.litellm_model import LitellmModel
from fastmcp import Client as MCPClient

from tender_analysis.core.config import settings
from tender_analysis.analysis_agents import (
    business_requirement_extractor_agent,
    technical_requirement_extractor_agent,
    pricing_requirement_extractor_agent,
    scoring_requirement_extractor_agent,
    standard_template_extractor_agent,
    non_standard_template_extractor_agent,
    checklist_outline_agent,
    checklist_enrichment_agent,
    project_summary_agent,
)

# ==============================================================================
# 全局配置区
# ==============================================================================

# ========================================
# 🔥 核心输入文件配置
# ========================================
# DEFAULT_DOCX_PATH: 招标文件Word文档的绝对路径
#   - 这是整个标书解析流程的起点
#   - 修改这里可以切换要分析的招标文件
#   - 也可以通过API参数动态传入
# ========================================
DEFAULT_DOCX_PATH = "/Users/cris/Documents/JR/Agent_py/TenderBot_New/jr_tenderbot_mcp/mcp-file/data/【中国上市公司协会】标书251127.converted.docx"
DEFAULT_MODEL_NAME = "gpt-4.1-mini"
MAX_TOKENS_PER_CHUNK = 45000  # 用于文本分块的 Token 阈值
SUMMARY_INPUT_CHAR_LIMIT = 20000  # 项目概要阶段输入截断阈值

# 定义了所有中间及最终产出文件的标准文件名
OUTPUT_PATHS = {
    "business": "business_summary.md",
    "technical": "technical_summary.md",
    "pricing": "pricing_summary.md",
    "scoring": "scoring_summary.md",
    "template": "templates.json",
    "intermediate_md": "intermediate_full.md",
    "intermediate_chunks": "intermediate_chunks.json",
    "checklist_outline": "checklist_outline.md",
    "final_checklist": "final_checklist.md",
    "project_summary": "project_summary.md",
}

# ==============================================================================
# 核心业务编排逻辑
# ==============================================================================
# `event_generator` 是本模块的核心，它定义了一个包含七大阶段的标书解析流水线：
#
#   阶段 1: 文档预处理 (Document Preprocessing)
#       - 将输入的 .docx 文件转换为 Markdown 格式。
#       - 对 Markdown 文本进行结构分析和智能分块。
#
#   阶段 2-5: 并行/串行分析 (Parallel/Serial Analysis)
#       - 并行或串行地调用四个独立的 Agent（商务、技术、报价、评分）。
#       - 每个 Agent 负责从文本块中提取其专业领域的内容。
#
#   阶段 6: 模版提取 (Template Extraction)
#       - 采用一个复杂的“识别 -> 分诊 -> 攻坚 -> 汇总”四步流程。
#       - 先由“标准 Agent”快速识别所有模版，再由“非标 Agent”对疑难模版进行精确提取。
#
#   阶段 7: 最终清单整合 (Final Checklist Integration)
#       - 整个流水线的收官之作，旨在生成一份以“满分”为导向的行动清单。
#       - 先由“大纲 Agent”根据评分标准，构建出清单的骨架。
#       - 再由“富化 Agent”分三次，将商务、技术、报价的细节填充进去。
#
# 整个过程通过 Server-Sent Events (SSE) 协议，实时地将进度、日志、产物等
# 事件推送给前端，实现了高度的透明度和实时反馈。
# ==============================================================================


# ----------------- 文档处理逻辑 (升级分块能力 + 本地 Docx 解析) -----------------

# --- 核心改动：从 document_processor.py 搬运并整合 Docx 解析逻辑 ---
def _table_to_markdown(table: docx.table.Table) -> str:
    """将 docx.table.Table 对象转换为 Markdown 格式的字符串。"""
    md_table = []
    for i, row in enumerate(table.rows):
        cell_texts = [" ".join(cell.text.split()).strip() for cell in row.cells]
        md_table.append("| " + " | ".join(cell_texts) + " |")
        if i == 0:
            separator = ["---" for _ in row.cells]
            md_table.append("| " + " | ".join(separator) + " |")
    return "\n".join(md_table)

def convert_docx_to_markdown(docx_path: str) -> str:
    """
    将 DOCX 文件转换为单个 Markdown 文本流，能够正确处理段落和表格。
    """
    document = docx.Document(docx_path)
    text_lines = []

    for element in document.element.body:
        if isinstance(element, CT_P):
            para = docx.text.paragraph.Paragraph(element, document)
            text_lines.append(para.text)
        elif isinstance(element, CT_Tbl):
            table = docx.table.Table(element, document)
            md_table_str = _table_to_markdown(table)
            text_lines.append("\n" + md_table_str + "\n")

    return "\n".join(text_lines)


def get_token_count(text: str) -> int:
    """使用 tiktoken 计算文本的 token 数量。"""
    encoder = tiktoken.get_encoding("cl100k_base")
    return len(encoder.encode(text))

def analyze_structure(text: str) -> List[Dict[str, Any]]:
    # (内容与之前一致, 为了简洁性在此折叠)
    lines = text.split('\n')
    title_pattern = re.compile(r"^\s*#*\s*第[一二三四五六七八九十百]+章\s+[^\t\.]*$")
    found_titles = [{"title": line.strip('# ').strip(), "line_num": i} for i, line in enumerate(lines) if title_pattern.match(line.strip())]
    structure = []
    for i, title_info in enumerate(found_titles):
        start_line = title_info["line_num"]
        end_line = found_titles[i+1]["line_num"] if i + 1 < len(found_titles) else len(lines)
        content_preview = "".join(lines[start_line+1:end_line]).strip()
        if content_preview:
            structure.append({"title": title_info["title"], "text": "\n".join(lines[start_line:end_line]).strip()})
    if not structure: return [{"title": "完整文档", "text": text}]
    return structure

def chunk_content(sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    将章节文本分割成适合模型处理的块。
    现在支持基于 Token 的二次分块，并能处理超长块。
    """
    final_chunks = []
    OVERLAP_PARA_COUNT = 2 # 定义重叠的段落数量

    for section in sections:
        section_text = section['text']
        token_count = get_token_count(section_text)

        if token_count <= MAX_TOKENS_PER_CHUNK:
            # 块大小合适，直接添加
            final_chunks.append({
                "source_title": section['title'],
                "content": section_text
            })
        else:
            # 块太大，需要按段落进行子分块
            print(f"   - 检测到超长块 (标题: '{section['title']}', Tokens: {token_count})，正在进行子分块...")
            
            sub_chunks_text = []
            current_sub_chunk_paras = []
            current_token_count = 0
            
            paragraphs = [p for p in section_text.split('\n') if p.strip()]

            for para in paragraphs:
                para_token_count = get_token_count(para) + 1 # +1 for newline token
                
                if current_token_count + para_token_count > MAX_TOKENS_PER_CHUNK and current_sub_chunk_paras:
                    sub_chunks_text.append("\n".join(current_sub_chunk_paras))
                    overlap_paras = current_sub_chunk_paras[-OVERLAP_PARA_COUNT:]
                    current_sub_chunk_paras = overlap_paras
                    current_token_count = get_token_count("\n".join(current_sub_chunk_paras))

                current_sub_chunk_paras.append(para)
                current_token_count += para_token_count
            
            if current_sub_chunk_paras:
                sub_chunks_text.append("\n".join(current_sub_chunk_paras))

            # 将子块列表转换为最终的 chunk 字典
            for sub_chunk_text in sub_chunks_text:
                final_chunks.append({
                    "source_title": section['title'],
                    "content": sub_chunk_text
                })
    
    return final_chunks

# --- 核心改动：创建一个新的“包装器”协程 ---
async def run_phase_and_collect_artifacts(phase_stream):
    """
    一个专门为并行执行模式设计的“异步生成器消费器”。

    `asyncio.gather` 无法直接处理异步生成器。此函数的作用是，
    完整地、静默地遍历完一个分析阶段（`run_extraction_phase`）的所有事件，
    然后只收集并返回最终的“产物”(artifact) 事件。

    这使得我们可以在后台并行运行多个分析阶段，并在最后统一获取它们的产出。

    Args:
        phase_stream: 一个 `run_extraction_phase` 函数返回的异步生成器。

    Returns:
        List[str]: 一个只包含最终产物事件的列表。
    """
    final_events = []
    async for event in phase_stream:
        # 我们必须遍历整个流，以确保 run_extraction_phase 的代码被完整执行
        if event.startswith("event: artifact"):
            final_events.append(event)
    return final_events


# ----------------- 核心业务编排逻辑 (重构成多阶段) -----------------

def sse(event: str, data_obj: Dict) -> str:
    """
    将事件类型和数据对象，格式化为符合 Server-Sent Events (SSE) 规范的字符串。

    Args:
        event (str): 事件的类型 (e.g., "log", "artifact", "phase_start")。
        data_obj (Dict): 要发送的数据，将被序列化为 JSON 字符串。

    Returns:
        str: 一个可以直接发送给客户端的 SSE 格式的文本块。
    """
    return f"event: {event}\n" + f"data: {json.dumps(data_obj, ensure_ascii=False, default=str)}\n\n"

async def mcp_smart_write(mcp_client: MCPClient, file_path: str, content: str) -> bool:
    """
    通过 MCP 服务，以一种健壮的方式写入或覆盖文件内容。

    它会自动处理文件是否存在的情况，确保内容能被正确写入。

    Args:
        mcp_client (MCPClient): 已初始化的 MCP 客户端实例。
        file_path (str): 目标文件的路径（相对于 MCP 服务的工作目录）。
        content (str): 要写入的完整文件内容。

    Returns:
        bool: 写入操作是否成功。
    """
    try:
        old_text = ""
        file_exists = False
        try:
            read_res = await mcp_client.call_tool("read_file", {"path": file_path, "limit": 10000000})
            text_from_read = str(getattr(read_res, "data", read_res))
            if "文件未找到" not in text_from_read and "file not found" not in text_from_read.lower():
                file_exists = True
                old_text = text_from_read
        except Exception:
            file_exists = False

        if not file_exists or old_text == "":
            # --- 核心改动：使用 smart_edit 来实现 write_file 的功能 ---
            await mcp_client.call_tool("smart_edit", {
                "file_path": file_path, 
                "old_string": "", 
                "new_string": content
            })
        else:
            await mcp_client.call_tool("smart_edit", {
                "file_path": file_path, 
                "old_string": old_text, 
                "new_string": content
            })
        
        # 写入后校验
        for i in range(3): # 简化校验次数
            await asyncio.sleep(0.1 * (i + 1))
            val_res = await mcp_client.call_tool("read_file", {"path": file_path, "limit": 1})
            if "文件未找到" not in str(getattr(val_res, "data", val_res)):
                return True
        return False
    except Exception:
        return False

async def run_extraction_phase(
    phase_name: str,
    phase_key: str, # <-- 核心改动：增加一个专门用于查找的 key
    agent_factory: Callable[..., Agent],
    text_chunks_with_meta: List[Dict[str, Any]],
    run_config: RunConfig,
    mcp_client: MCPClient,
    language: str = "zh",
):
    """
    一个通用的辅助函数，用于执行单个提取阶段（例如商务、技术等）。
    它会遍历文本块，流式调用 Agent，并实时推送事件，最终将结果写入文件。
    """
    yield sse("phase_start", {"name": phase_name})
    
    agent = agent_factory(language=language)
    full_extracted_content = ""
    
    for i, chunk_info in enumerate(text_chunks_with_meta):
        chunk_text = chunk_info['content']
        yield sse("update", {"phase": phase_name, "progress": f"{i+1}/{len(text_chunks_with_meta)}"})
        
        yield sse("stream_start", {"chunk": i + 1, "phase": phase_name})
        
        result_stream = Runner.run_streamed(
            agent, 
            f"请从以下文本中提取 {phase_name}：\n\n---\n\n{chunk_text}", 
            run_config=run_config
        )
        
        extracted_content_chunk = ""
        async for event in result_stream.stream_events():
            if event.type == "raw_response_event" and isinstance(event.data, ResponseTextDeltaEvent):
                delta = event.data.delta
                extracted_content_chunk += delta
                yield sse("token_delta", {"delta": delta})

        yield sse("stream_end", {"chunk": i + 1, "phase": phase_name})
        
        if extracted_content_chunk and "未找到" not in extracted_content_chunk:
            source_title = chunk_info['source_title']
            new_section = f"## 来自章节: {source_title}\n\n{extracted_content_chunk}\n\n---\n\n"
            full_extracted_content += new_section
            yield sse("note", {"phase": phase_name, "text": f"块 {i+1} 分析完成，提取到内容。"})
        else:
            yield sse("note", {"phase": phase_name, "text": f"块 {i+1} 分析完成，未找到相关内容。"})
    
    # --- 核心改动：使用新的 phase_key 来查找文件名 ---
    output_filename = OUTPUT_PATHS[phase_key]
    doc_title = os.path.basename(DEFAULT_DOCX_PATH) #
    final_md_content = f"# {doc_title} - {phase_name}分析报告\n\n" + full_extracted_content
    await mcp_smart_write(mcp_client, output_filename, final_md_content)
    
    yield sse("artifact", {"type": "file", "filename": output_filename})
    yield sse("phase_end", {"name": phase_name})


# ----------------- 辅助函数区 (新增清单整合辅助函数) -----------------

def split_outline_by_headings(outline_text: str) -> Dict[str, str]:
    """
    使用正则表达式，将一份完整的 Markdown 大纲文本，按一级标题（# heading）
    拆分为一个字典。这使得后续可以对大纲的各个部分进行独立处理。

    Args:
        outline_text (str): 包含 Markdown 标题的完整文本。

    Returns:
        Dict[str, str]: 一个字典，键是从标题中识别出的核心词（"business", 
                        "technical", "pricing"），值是包含标题在内的完整部分文本。
    """
    sections = {}
    parts = re.split(r'(^#\s.*)', outline_text, flags=re.MULTILINE)
    
    if not parts:
        return {"": outline_text}

    for i in range(1, len(parts), 2):
        title = parts[i].strip()
        content = parts[i+1].strip() if i + 1 < len(parts) else ""
        
        if "商务" in title:
            key = "business"
        elif "技术" in title:
            key = "technical"
        elif "价格" in title:
            key = "pricing"
        else:
            key = title
            
        sections[key] = f"{title}\n\n{content}".strip()
        
    return sections

async def mcp_read_file(mcp_client: MCPClient, file_path: str) -> str | None:
    """
    通过 MCP 服务，以一种健壮的方式读取文件内容。

    Args:
        mcp_client (MCPClient): 已初始化的 MCP 客户端实例。
        file_path (str): 目标文件的路径（相对于 MCP 服务的工作目录）。

    Returns:
        str | None: 如果成功，返回文件内容；如果文件未找到或发生错误，返回 None。
    """
    try:
        read_res = await mcp_client.call_tool("read_file", {"path": file_path, "limit": 10000000})
        content = str(getattr(read_res, "data", read_res))
        if "文件未找到" in content or "file not found" in content.lower():
            return None
        return content
    except Exception:
        return None

async def event_generator(
    docx_path: str = DEFAULT_DOCX_PATH,
    model_name: str = DEFAULT_MODEL_NAME,
    language: str = "zh",
    stream_token_deltas: bool = True # <-- 核心改动：接收配置参数
):
    """
    标书解析流水线的总入口和核心事件生成器。

    它按照预设的七大阶段，依次或并行地执行所有任务，并通过 `yield` 语句，
    以 Server-Sent Events (SSE) 的形式，向外实时推送整个过程的状态。

    Args:
        docx_path (str): 要解析的 .docx 文件的本地路径。
        model_name (str): 用于执行任务的 LLM 模型名称。
        language (str): Agent 输出内容的语言。
        stream_token_deltas (bool): 控制分析阶段（2-5）的执行模式。
                                    True  -> 串行执行，并实时推送所有 token 流。
                                    False -> 并行执行，只实时推送一个随机任务的 token 流。

    Yields:
        str: 格式化后的 SSE 事件字符串。
    """
    # 1. 初始化
    litellm_model = LitellmModel(model=model_name, api_key=settings.OPENAI_API_KEY)
    run_config = RunConfig(
        model=litellm_model, model_settings=ModelSettings(include_usage=False), tracing_disabled=True
    )
    mcp_client = MCPClient(settings.MCP_SERVER_URL)
    
    try:
        # --- 阶段 1: 文档预处理 ---
        yield sse("phase_start", {"name": "文档预处理"})
        yield sse("note", {"phase": "文档预处理", "text": f"正在读取并转换文档: {os.path.basename(docx_path)}..."})
        
        # --- 核心改动：替换 MCP 调用为本地函数调用 ---
        try:
            markdown_content = convert_docx_to_markdown(docx_path)
            yield sse("note", {"phase": "文档预处理", "text": "文档转换成功！"})
        except Exception as e:
            yield sse("error", {"type": "DocxConversionError", "message": f"处理 Docx 文件时出错: {e}"})
            return

        # --- 核心改动：保存并推送 Markdown 全文 ---
        async with mcp_client:
            await mcp_smart_write(mcp_client, OUTPUT_PATHS["intermediate_md"], markdown_content)
        yield sse("artifact", {"type": "file", "filename": OUTPUT_PATHS["intermediate_md"]})

        # --- 新阶段：项目概要生成 ---
        yield sse("phase_start", {"name": "项目概要生成"})
        yield sse("note", {"phase": "项目概要生成", "text": "正在调用 LLM 生成项目概要描述..."})
        summary_agent = project_summary_agent(language=language)
        summary_input_text = markdown_content
        truncated = False
        if len(summary_input_text) > SUMMARY_INPUT_CHAR_LIMIT:
            summary_input_text = summary_input_text[:SUMMARY_INPUT_CHAR_LIMIT]
            truncated = True
        summary_prompt = (
            "请根据以下招标文件（Markdown 形式）的内容，输出符合指令要求的项目概要：\n\n"
            f"{summary_input_text}"
        )
        summary_result = await Runner.run(summary_agent, summary_prompt, run_config=run_config)
        summary_text = summary_result.final_output.strip()
        if not summary_text:
            summary_text = "（模型未生成有效内容）"
        metadata_notice = (
            "" if truncated else ""
        )
        project_summary_md = f"# 招标项目概要\n\n{summary_text}{metadata_notice}\n"
        async with mcp_client:
            await mcp_smart_write(
                mcp_client,
                OUTPUT_PATHS["project_summary"],
                project_summary_md,
            )
        yield sse("artifact", {"type": "file", "filename": OUTPUT_PATHS["project_summary"]})
        yield sse("phase_end", {"name": "项目概要生成"})


        yield sse("note", {"phase": "文档预处理", "text": "正在分析文档结构并进行文本分块..."})
        sections = analyze_structure(markdown_content)
        final_chunks_with_meta = chunk_content(sections)
        text_chunks = [chunk['content'] for chunk in final_chunks_with_meta] # 提取纯文本内容列表
        yield sse("note", {"phase": "文档预处理", "text": f"文档分块完成，共生成 {len(text_chunks)} 个文本块。"})

        # --- 核心改动：保存并推送分块结果 ---
        chunks_json_content = json.dumps(
            final_chunks_with_meta,
            ensure_ascii=False,
            indent=2
        )
        async with mcp_client:
            await mcp_smart_write(mcp_client, OUTPUT_PATHS["intermediate_chunks"], chunks_json_content)
        yield sse("artifact", {"type": "file", "filename": OUTPUT_PATHS["intermediate_chunks"]})
        yield sse("phase_end", {"name": "文档预处理"})

        # --- 阶段 2-5: 顺序执行各项提取 ---
        # --- 核心改动：根据 stream_token_deltas 的值，选择执行模式 ---
        if stream_token_deltas:
            # --- 模式一：串行执行，实时推送 Token 流 (便于调试) ---
            yield sse("note", {"phase": "分析", "text": "以串行模式启动分析，将实时推送 Token 流..."})
            async with mcp_client:
                async for event in run_extraction_phase("商务要求", "business", business_requirement_extractor_agent, final_chunks_with_meta, run_config, mcp_client, language): yield event
                async for event in run_extraction_phase("技术要求", "technical", technical_requirement_extractor_agent, final_chunks_with_meta, run_config, mcp_client, language): yield event
                async for event in run_extraction_phase("报价要求", "pricing", pricing_requirement_extractor_agent, final_chunks_with_meta, run_config, mcp_client, language): yield event
                async for event in run_extraction_phase("评分要求", "scoring", scoring_requirement_extractor_agent, final_chunks_with_meta, run_config, mcp_client, language): yield event
        else:
            # --- 模式三：并行执行，但实时推送一个任务的流 (兼顾效率与反馈) ---
            yield sse("phase_start", {"name": "并行分析"})
            yield sse("note", {"phase": "并行分析", "text": "以并行模式启动分析，将实时推送其中一个任务的进度流..."})

            async with mcp_client:
                all_phases = {
                    "business": ("商务要求", business_requirement_extractor_agent),
                    "technical": ("技术要求", technical_requirement_extractor_agent),
                    "pricing": ("报价要求", pricing_requirement_extractor_agent),
                    "scoring": ("评分要求", scoring_requirement_extractor_agent),
                }

                if not all_phases:
                    yield sse("phase_end", {"name": "并行分析"})
                else:
                    # 随机选择一个 phase 进行流式推送
                    stream_phase_key = random.choice(list(all_phases.keys()))
                    stream_phase_name, stream_agent_factory = all_phases.pop(stream_phase_key)
                    yield sse("note", {"phase": "并行分析", "text": f"已随机选择 '{stream_phase_name}' 任务进行实时流推送。"})

                    # 将其他 phase 作为后台任务运行
                    background_tasks = []
                    for key, (name, factory) in all_phases.items():
                        task_coro = run_phase_and_collect_artifacts(
                            run_extraction_phase(name, key, factory, final_chunks_with_meta, run_config, mcp_client, language)
                        )
                        background_tasks.append(asyncio.create_task(task_coro))

                    # 运行并推送主任务的流
                    stream_generator = run_extraction_phase(
                        stream_phase_name, stream_phase_key, stream_agent_factory, final_chunks_with_meta, run_config, mcp_client, language
                    )
                    async for event in stream_generator:
                        yield event

                    # 等待后台任务完成并推送它们的最终产物
                    if background_tasks:
                        yield sse("note", {"phase": "并行分析", "text": "正在等待其余后台任务完成..."})
                        results_of_events = await asyncio.gather(*background_tasks)
                        for event_list in results_of_events:
                            for event in event_list:
                                yield event
                        yield sse("note", {"phase": "并行分析", "text": "所有后台任务均已完成。"})

            yield sse("phase_end", {"name": "并行分析"})

        # --- 阶段 6: 模版提取 (v4.0 终极版流水线) ---
        yield sse("phase_start", {"name": "模版提取"})
        
        # --- 步骤 A: 识别与打标 ---
        yield sse("note", {"phase": "模版提取", "text": "步骤 A: 正在进行初步识别与打标..."})
        standard_agent = standard_template_extractor_agent(language=language)
        all_found_templates = []
        async with mcp_client:
            for i, chunk_info in enumerate(final_chunks_with_meta):
                result = await Runner.run(standard_agent, 
                    f"请从以下文本中提取模版：\n\n---\n\n{chunk_info['content']}", 
                    run_config=run_config
                )
                try:
                    json_str = result.final_output.strip().replace("`", "")
                    if json_str.startswith("json"): json_str = json_str[4:]
                    
                    templates_from_chunk = json.loads(json_str)

                    if isinstance(templates_from_chunk, list):
                        for template in templates_from_chunk:
                            template["source_chunk_ids"] = [i]
                        all_found_templates.extend(templates_from_chunk)
                except (json.JSONDecodeError, AttributeError, TypeError):
                    pass # Silently ignore parsing errors in production
        
        # --- 步骤 B: 分诊 ---
        yield sse("note", {"phase": "模版提取", "text": "步骤 B: 正在对模板进行分诊..."})
        standard_results = [tpl for tpl in all_found_templates if tpl.get('key') is not None]
        non_standard_to_process = [tpl for tpl in all_found_templates if tpl.get('key') is None]
        yield sse("note", {"phase": "模版提取", "text": f"分诊完成：{len(standard_results)} 个标准模板，{len(non_standard_to_process)} 个待处理非标模板。"})

        # --- 步骤 C: 专家会诊 (非标提取) ---
        yield sse("note", {"phase": "模版提取", "text": "步骤 C: 正在进行非标提取（攻坚）..."})
        non_standard_agent = non_standard_template_extractor_agent(language=language)
        non_standard_results = []
        # 为了效率，我们将所有非标模板按其来源文本块进行分组
        grouped_to_process = {}
        for tpl in non_standard_to_process:
            # --- 核心改动：使用新的、正确的 `source_chunk_ids` 字段 ---
            if tpl.get("source_chunk_ids"):
                chunk_idx = tpl['source_chunk_ids'][0]
                if chunk_idx not in grouped_to_process:
                    grouped_to_process[chunk_idx] = []
                grouped_to_process[chunk_idx].append(tpl) # 传递完整的 tpl 对象

        async with mcp_client:
            for chunk_idx, templates_in_chunk in grouped_to_process.items():
                chunk_text = final_chunks_with_meta[chunk_idx]['content']
                # --- 核心改动：从 tpl 对象中提取 name ---
                names_to_extract = [t['name'] for t in templates_in_chunk]
                names_str = ", ".join(f"'{n}'" for n in names_to_extract)
                
                result = await Runner.run(
                    non_standard_agent,
                    f"待提取的模板名称列表: [{names_str}]\n\n---\n\n招标文件文本:\n{chunk_text}",
                    run_config=run_config
                )
                try:
                    json_str = result.final_output.strip().replace("`", "")
                    if json_str.startswith("json"): json_str = json_str[4:]
                    
                    templates_from_chunk = json.loads(json_str)

                    if isinstance(templates_from_chunk, list):
                        non_standard_results.extend(templates_from_chunk)
                except (json.JSONDecodeError, AttributeError, TypeError):
                    pass # Silently ignore
        yield sse("note", {"phase": "模版提取", "text": f"步骤 C 完成：成功提取 {len(non_standard_results)} 个非标模板。"})
        
        # --- 步骤 D: 结果汇总与最终处理 ---
        yield sse("note", {"phase": "模版提取", "text": "步骤 D: 正在合并、去重并格式化..."})
        
        final_results = []
        processed_names = set()

        # 首先处理标准模板结果，应用严格的字段控制
        for std_tpl in standard_results:
            name = std_tpl.get("name", "").strip()
            if name and name not in processed_names:
                final_results.append({
                    "id": f"tpl_{uuid.uuid4().hex[:8]}",
                    "name": name,
                    "title": std_tpl.get("key") 
                })
                processed_names.add(name)

        # 然后处理非标模板结果，应用严格的字段控制和安全的 .get() 访问
        for ns_tpl in non_standard_results:
            name = ns_tpl.get("name", "").strip()
            if name and name not in processed_names:
                # 严格的字段控制，并使用 .get() 方法确保安全
                final_results.append({
                    "id": f"tpl_{uuid.uuid4().hex[:8]}",
                    "name": name,
                    "start": ns_tpl.get("start"),
                    "end": ns_tpl.get("end"),
                    "keywords": ns_tpl.get("keywords", [])
                })
                processed_names.add(name)

        yield sse("note", {"phase": "模版提取", "text": f"步骤 D 完成：共生成 {len(final_results)} 个最终模板记录。"})
        
        # 写入最终的 JSON 文件
        final_json_content = json.dumps(final_results, ensure_ascii=False, indent=4)
        async with mcp_client:
            await mcp_smart_write(mcp_client, OUTPUT_PATHS["template"], final_json_content)
        
        yield sse("artifact", {"type": "file", "filename": OUTPUT_PATHS["template"]})
        yield sse("phase_end", {"name": "模版提取"})

        # --- 阶段 7: 最终清单整合 (从 test_checklist_pipeline.py 移植) ---
        yield sse("phase_start", {"name": "最终清单整合"})

        # --- 步骤 7.1: 读取所有分析报告 ---
        yield sse("note", {"phase": "最终清单整合", "text": "步骤 A: 正在读取所有分析报告..."})
        report_keys = ["scoring", "business", "technical", "pricing"]
        report_contents: Dict[str, str] = {}
        async with mcp_client:
            tasks = [mcp_read_file(mcp_client, OUTPUT_PATHS[key]) for key in report_keys]
            results = await asyncio.gather(*tasks)
            
            for key, content in zip(report_keys, results):
                if content is None:
                    yield sse("warning", {"phase": "最终清单整合", "text": f"警告：未找到分析报告 '{OUTPUT_PATHS[key]}'，该部分可能不完整。"})
                    report_contents[key] = ""
                else:
                    report_contents[key] = content
        
        if not report_contents.get("scoring"):
            yield sse("error", {"type": "MissingInputError", "message": "无法进行清单整合，因为评分报告缺失。"})
            return

        # --- 步骤 7.2: 构建“评分驱动”的大纲 ---
        yield sse("note", {"phase": "最终清单整合", "text": "步骤 B: 正在构建“评分驱动”的清单大纲..."})
        outline_agent = checklist_outline_agent(language=language)
        outline_result = await Runner.run(
            outline_agent,
            f"请根据以下评分要求文档，创建清单大纲：\n\n---\n\n{report_contents['scoring']}",
            run_config=run_config
        )
        checklist_outline = outline_result.final_output
        
        # 保存并推送大纲产物
        outline_filename = "checklist_outline.md"
        async with mcp_client:
            await mcp_smart_write(mcp_client, outline_filename, checklist_outline)
        yield sse("artifact", {"type": "file", "filename": outline_filename})
        yield sse("note", {"phase": "最终清单整合", "text": "清单大纲构建完成。"})

        # --- 步骤 7.3: 拆分大纲并分三次富化 ---
        yield sse("note", {"phase": "最终清单整合", "text": "步骤 C: 正在分三次、逐部分地填充大纲..."})
        outline_sections = split_outline_by_headings(checklist_outline)
        enrichment_agent = checklist_enrichment_agent(language=language)
        final_checklist_parts: List[str] = []

        process_order = ["business", "technical", "pricing"]
        for section_key in process_order:
            if section_key in outline_sections:
                yield sse("update", {"phase": "最终清单整合", "progress": f"正在处理 {section_key} 部分..."})
                
                outline_part = outline_sections[section_key]
                report_content = report_contents.get(section_key, '无相关报告内容')

                full_context_input = (
                    f"# 核心行动大纲 (当前部分)\n\n{outline_part}\n\n"
                    f"---\n\n# 详细需求报告 (对应部分)\n\n{report_content}"
                )
                
                enriched_part_result = await Runner.run(
                    enrichment_agent, full_context_input, run_config=run_config
                )
                enriched_part = enriched_part_result.final_output
                final_checklist_parts.append(enriched_part)
                yield sse("note", {"phase": "最终清单整合", "text": f"{section_key} 部分填充完成。"})

        # --- 步骤 7.4: 合并并保存最终清单 ---
        yield sse("note", {"phase": "最终清单整合", "text": "步骤 D: 正在合并并保存最终清单..."})
        final_checklist = "\n\n---\n\n".join(final_checklist_parts)
        final_checklist_filename = "final_checklist.md"
        
        async with mcp_client:
            await mcp_smart_write(mcp_client, final_checklist_filename, final_checklist)
        
        yield sse("artifact", {"type": "file", "filename": final_checklist_filename})
        yield sse("phase_end", {"name": "最终清单整合"})

        # --- 流水线结束 ---
        yield sse("complete", {"final_output": "所有分析阶段均已完成！"})

    except Exception as e:
        error_info = {"type": type(e).__name__, "message": str(e)}
        yield sse("error", error_info)
