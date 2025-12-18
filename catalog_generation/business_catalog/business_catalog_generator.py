# -*- coding: utf-8 -*-
"""
@File    : business_catalog_generator.py
@Description: This file contains the logic for generating the business catalog.
@Author  : <<your name>>
@Date    : <<date>>
@Version : 1.0
"""

# Input: Optional format framework (can be read from file if None).
# Output: SSE events indicating the progress and result of the business catalog generation.

import json
from typing import Dict, Any, List, AsyncGenerator, Tuple
import re
import uuid
import datetime

from fastmcp import Client as MCPClient

from ..config import settings
from ..utils.mcp_utils import sse, mcp_read_file, mcp_smart_write, call_llm_streaming
from ..utils.file_utils import (
    extract_business_section,
    collect_leaf_nodes_with_path,
    extract_json_from_response,
    extract_section,
    parse_requirement_blocks,
    find_and_add_node,
    find_and_update_node,
    assign_ids_and_levels,
)
from .agents import (
    business_catalog_analysis_agent,
    business_catalog_children_generation_agent,
    catalog_matching_agent,
    directory_optimization_agent,
)
from ..linking.linker import run_template_linking_pipeline


async def generate_business_catalog_v2_event_generator(
    format_framework: List[Dict[str, Any]] = None,
    model_name: str = settings.DEFAULT_MODEL_NAME,
    language: str = "zh"
) -> AsyncGenerator[str, None]:
    """
    阶段2：生成商务目录 V2。
    """
    mcp_client = MCPClient(settings.MCP_SERVER_URL)
    
    try:
        yield sse("phase_start", {"name": "生成商务目录"})
        
        # 1. 读取 format_framework.json
        if format_framework is None:
            async with mcp_client:
                framework_content = await mcp_read_file(mcp_client, settings.OUTPUT_PATHS["format_framework"])
            if not framework_content:
                yield sse("error", {"message": "无法读取格式框架文件，请先执行格式框架提取"})
                return
            try:
                format_framework = json.loads(framework_content)
            except json.JSONDecodeError:
                yield sse("error", {"message": "格式框架文件格式错误"})
                return
        
        business_framework = extract_business_section(format_framework)
        if not business_framework:
            yield sse("warning", {"phase": "商务目录生成", "text": "未在格式框架中找到商务部分。"})
            return
        
        # ==============================================================================
        # 步骤1：逐项分析并生成子目录
        # ==============================================================================
        yield sse("phase_start", {"name": "步骤1: 逐项分析并生成子目录"})
        
        leaf_nodes = collect_leaf_nodes_with_path(business_framework)
        
        if not leaf_nodes:
            yield sse("warning", {"phase": "商务目录生成", "text": "未找到叶子节点，跳过处理。"})
            yield sse("phase_end", {"name": "步骤1: 逐项分析并生成子目录"})
            yield sse("phase_end", {"name": "商务目录生成（重构版）"})
            yield sse("complete", {"final_output": "商务目录生成完成（无需处理）"})
            return
        
        yield sse("note", {"phase": "分析并生成", "text": f"识别到 {len(leaf_nodes)} 个叶子节点，开始逐项处理..."})
        
        analysis_agent = business_catalog_analysis_agent(language=language)
        analysis_system_prompt = analysis_agent.instructions
        children_gen_agent = business_catalog_children_generation_agent(language=language)
        children_system_prompt = children_gen_agent.instructions
        
        for idx, node_info in enumerate(leaf_nodes, 1):
            node_name = node_info['name']
            node = node_info['node']
            
            yield sse("update", {"phase": "分析并生成", "progress": f"{idx}/{len(leaf_nodes)}"})
            
            content_desc = node.get('content_description', '')
            if not content_desc:
                yield sse("note", {"phase": "分析并生成", "text": f"➡️ ({idx}/{len(leaf_nodes)}) 跳过（无描述）: {node_name}"})
                continue
            
            analysis_input = f"""# 目录项：
{json.dumps({'name': node_name, 'children': []}, ensure_ascii=False, indent=2)}

# 该目录的内容描述：
{content_desc}

请按照要求生成分析报告。"""
            
            md_analysis = ""
            try:
                log_id = f"log-{uuid.uuid4()}"
                yield sse("debug_log", {"title": f"分析内容 ({idx}/{len(leaf_nodes)}): {node_name}", "log_id": log_id})

                async for item in call_llm_streaming(
                    system_prompt=analysis_system_prompt,
                    user_input=analysis_input,
                    model_name=model_name,
                    yield_tokens=True
                ):
                    if 'event: token_delta' in item:
                        try:
                            data_line = next(line for line in item.split('\n') if line.startswith('data: '))
                            data = json.loads(data_line[len('data: '):])
                            delta = data.get('delta', '')
                            if delta:
                                md_analysis += delta
                                yield sse("debug_token_delta", {"log_id": log_id, "delta": delta})
                        except (StopIteration, json.JSONDecodeError):
                            pass
            except Exception as e:
                yield sse("warning", {"phase": "分析并生成", "text": f"⚠️ ({idx}/{len(leaf_nodes)}) 分析失败: {node_name} - {str(e)}"})
                continue
            
            children_input = f"""# 当前分析的目录对象：
            {json.dumps({'name': node_name, 'children': [], 'content_description': content_desc}, ensure_ascii=False, indent=2)}

            # 该目录的详细分析报告：
            {md_analysis}

            请判断是否需要添加子目录，并按照要求输出。"""

            children_response = ""
            try:
                log_id = f"log-{uuid.uuid4()}"
                yield sse("debug_log", {"title": f"生成子目录 ({idx}/{len(leaf_nodes)}): {node_name}", "log_id": log_id})
                async for item in call_llm_streaming(
                    system_prompt=children_system_prompt,
                    user_input=children_input,
                    model_name=model_name,
                    yield_tokens=True
                ):
                    if 'event: token_delta' in item:
                        try:
                            data_line = next(line for line in item.split('\n') if line.startswith('data: '))
                            data = json.loads(data_line[len('data: '):])
                            delta = data.get('delta', '')
                            if delta:
                                children_response += delta
                                yield sse("debug_token_delta", {"log_id": log_id, "delta": delta})
                        except (StopIteration, json.JSONDecodeError):
                            pass
            except Exception as e:
                yield sse("warning", {"phase": "分析并生成", "text": f"⚠️ ({idx}/{len(leaf_nodes)}) 子目录生成失败: {node_name} - {str(e)}"})
                continue
            
            if "NO_CHILDREN" in children_response.strip():
                yield sse("note", {"phase": "分析并生成", "text": f"➡️ ({idx}/{len(leaf_nodes)}) 不需要子目录: {node_name}"})
                continue
            
            try:
                children_json_str = extract_json_from_response(children_response)
                children_data = json.loads(children_json_str)
                
                if not isinstance(children_data, list):
                    yield sse("warning", {"phase": "分析并生成", "text": f"⚠️ ({idx}/{len(leaf_nodes)}) 返回格式错误（非数组）: {node_name}"})
                    continue
            
                node['children'] = children_data
                yield sse("note", {"phase": "分析并生成", "text": f"✅ ({idx}/{len(leaf_nodes)}) 已添加 {len(children_data)} 个子目录: {node_name}"})
                
            except json.JSONDecodeError as e:
                yield sse("warning", {"phase": "分析并生成", "text": f"⚠️ ({idx}/{len(leaf_nodes)}) JSON 解析失败: {node_name} - {str(e)}"})
                continue
        
        yield sse("note", {"phase": "分析并生成", "text": "所有叶子节点处理完成"})
        yield sse("phase_end", {"name": "步骤1: 逐项分析并生成子目录"})

        # 保存步骤1结束后的中间文件
        intermediate_catalog_json = json.dumps(business_framework, ensure_ascii=False, indent=2)
        async with mcp_client:
            await mcp_smart_write(
                mcp_client,
                settings.OUTPUT_PATHS["business_catalog_intermediate"],
                intermediate_catalog_json
            )
        yield sse("artifact", {"type": "file", "filename": settings.OUTPUT_PATHS["business_catalog_intermediate"]})


        # ==============================================================================
        # 步骤2：需求验证与目录优化
        # ==============================================================================
        
        try:
            async with mcp_client:
                checklist_content = await mcp_read_file(mcp_client, settings.INPUT_PATHS["final_checklist"])
            
            if checklist_content:
                check_section = extract_section(checklist_content, "商务部分评分")
                requirement_blocks = parse_requirement_blocks(check_section) if check_section else []
                
                if requirement_blocks:
                    yield sse("note", {"phase": "需求验证", "text": f"识别到 {len(requirement_blocks)} 个需求块，开始验证..."})
                    
                    matching_agent = catalog_matching_agent(language=language)
                    matching_system_prompt = matching_agent.instructions
                    
                    verification_report_full = "# 商务目录需求验证报告\n\n"
                    
                    for idx, req_block in enumerate(requirement_blocks, 1):
                        yield sse("update", {"phase": "需求验证", "progress": f"{idx}/{len(requirement_blocks)}"})
                        
                        verification_input = f"""当前需要判断的需求：
                        {req_block}

                        当前的目录是：
                        {json.dumps(business_framework, ensure_ascii=False, indent=2)}
                        """
                            
                        try:
                            matching_analysis = ""
                            log_id = f"log-{uuid.uuid4()}"
                            yield sse("debug_log", {"title": f"需求验证 ({idx}/{len(requirement_blocks)})", "log_id": log_id})
                            
                            async for item in call_llm_streaming(
                                system_prompt=matching_system_prompt,
                                user_input=verification_input,
                                model_name=model_name,
                                yield_tokens=True
                            ):
                                if 'event: token_delta' in item:
                                    try:
                                        data_line = next(line for line in item.split('\n') if line.startswith('data: '))
                                        data = json.loads(data_line[len('data: '):])
                                        delta = data.get('delta', '')
                                        if delta:
                                            matching_analysis += delta
                                            yield sse("debug_token_delta", {"log_id": log_id, "delta": delta})
                                    except (StopIteration, json.JSONDecodeError):
                                        pass
            
                            if "IRRELEVANT_REQUIREMENT" in matching_analysis:
                                yield sse("note", {"phase": "需求验证", "text": f"➡️ ({idx}/{len(requirement_blocks)}) 跳过无关需求"})
                                verification_report_full += f"{'='*80}\n"
                                verification_report_full += f"## 需求 {idx}/{len(requirement_blocks)}\n\n"
                                verification_report_full += f"### 需求内容\n```\n{req_block[:200]}{'...' if len(req_block) > 200 else ''}\n```\n\n"
                                verification_report_full += f"**状态**: ⏭️ 跳过 (无关需求)\n\n"
                                continue
                            
                            verification_report_full += f"{'='*80}\n"
                            verification_report_full += f"## 需求 {idx}/{len(requirement_blocks)}\n\n"
                            verification_report_full += f"### 需求内容\n```\n{req_block[:200]}{'...' if len(req_block) > 200 else ''}\n```\n\n"
                            verification_report_full += f"### 验证结果\n\n{matching_analysis}\n\n"
                            
                            yield sse("stream_start", {"phase": "目录优化", "current": f"需求 {idx} - 执行优化"})
                            
                            optimization_input = f"""# 需求分析与操作建议

{matching_analysis}

# 当前目录结构
{json.dumps(business_framework, ensure_ascii=False, indent=2)}

# 你的任务
根据上面的"操作建议"，使用工具来修改目录。
- **只输出工具调用**，不要输出其他任何文字。
- 如果需要新增，请调用 `add_catalog_child` 工具。
- 如果需要更新，请调用 `update_catalog_node` 工具。"""
                            
                            try:
                                optimization_agent = directory_optimization_agent()
                                optimization_response = ""
                                log_id = f"log-{uuid.uuid4()}"
                                yield sse("debug_log", {"title": f"目录优化 ({idx}/{len(requirement_blocks)})", "log_id": log_id})

                                async for item in call_llm_streaming(
                                    system_prompt=optimization_agent.instructions,
                                    user_input=optimization_input,
                                    model_name=model_name,
                                    yield_tokens=True
                                ):
                                    if 'event: token_delta' in item:
                                        try:
                                            data_line = next(line for line in item.split('\n') if line.startswith('data: '))
                                            data = json.loads(data_line[len('data: '):])
                                            delta = data.get('delta', '')
                                            if delta:
                                                optimization_response += delta
                                                yield sse("debug_token_delta", {"log_id": log_id, "delta": delta})
                                        except (StopIteration, json.JSONDecodeError):
                                            pass
                                
                                tool_calls_executed, execution_logs = _parse_and_execute_tool_calls(
                                    optimization_response,
                                    business_framework
                                )
                                
                                for log in execution_logs:
                                    if "✅" in log:
                                        log_summary = log.split("]")[1].strip() if "]" in log else log
                                        yield sse("note", {"phase": "目录优化", "text": f"✅ {log_summary}"})
                                        verification_report_full += f"- {log}\n"
                                    else:
                                        log_summary = log.split("]")[1].strip() if "]" in log else log
                                        yield sse("warning", {"phase": "目录优化", "text": f"❌ {log_summary}"})
                                        verification_report_full += f"- {log}\n"
                                
                                if tool_calls_executed > 0:
                                    yield sse("note", {"phase": "需求验证", "text": f"🔧 ({idx}/{len(requirement_blocks)}) 已根据分析优化目录，执行 {tool_calls_executed} 个修改。"})
                                    verification_report_full += f"**状态**: ✅ 已优化\n"
                                    verification_report_full += f"**执行结果**: 已自动优化 {tool_calls_executed} 处。\n\n"
                                else:
                                    yield sse("note", {"phase": "需求验证", "text": f"✅ ({idx}/{len(requirement_blocks)}) 需求已满足，无需修改。"})
                                    verification_report_full += f"**状态**: ✅ 已满足\n"
                                    verification_report_full += f"**执行结果**: AI分析后认为无需修改。\n\n"
                                
                            except Exception as opt_error:
                                yield sse("warning", {"phase": "需求验证", "text": f"⚠️ ({idx}/{len(requirement_blocks)}) 目录优化失败"})
                                
                        except Exception as e:
                            yield sse("warning", {"phase": "需求验证", "text": f"⚠️ ({idx}/{len(requirement_blocks)}) 验证失败"})
                            
                            verification_report_full += f"{'='*80}\n"
                            verification_report_full += f"## 需求 {idx}/{len(requirement_blocks)}\n\n"
                            verification_report_full += f"### 需求内容\n```\n{req_block[:200]}{'...' if len(req_block) > 200 else ''}\n```\n\n"
                            verification_report_full += f"### 验证结果\n\n❌ 验证失败: {str(e)}\n\n"
                            verification_report_full += f"**状态**: ❌ 验证失败\n\n"
                    
                    verification_report_full += f"{'='*80}\n\n"
                    verification_report_full += "## 验证总结\n\n"
                    verification_report_full += f"- 总需求数: {len(requirement_blocks)}\n"
                    verification_report_full += f"- 报告完成时间: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n\n"
                    
                    async with mcp_client:
                        await mcp_smart_write(
                            mcp_client,
                            "catalog_verification_report.md",
                            verification_report_full
                        )
                    
                    yield sse("info", {"phase": "需求验证", "text": "📄 验证报告已保存: catalog_verification_report.md"})
                    yield sse("note", {"phase": "需求验证", "text": "需求验证完成"})
                else:
                    yield sse("note", {"phase": "需求验证", "text": "未找到需求块，跳过验证"})
            else:
                yield sse("note", {"phase": "需求验证", "text": "未找到checklist文件，跳过验证"})
                
        except Exception as e:
            yield sse("warning", {"phase": "需求验证", "text": f"验证过程出错: {str(e)}"})
        
        yield sse("phase_end", {"name": "步骤2: 需求验证与目录优化"})
        
        def remove_analysis_report(nodes):
            for node in nodes:
                if 'analysis_report' in node:
                    del node['analysis_report']
                if node.get('children'):
                    remove_analysis_report(node['children'])
        
        remove_analysis_report(business_framework)
        
        assign_ids_and_levels(business_framework, prefix="bus")
        
        # 3. 保存最终的商务目录
        final_catalog_json = json.dumps(business_framework, ensure_ascii=False, indent=2)
        async with mcp_client:
            await mcp_smart_write(
                mcp_client,
                settings.OUTPUT_PATHS["business_catalog"],
                final_catalog_json
            )
        
        yield sse("artifact", {"type": "file", "filename": settings.OUTPUT_PATHS["business_catalog"]})
        yield sse("phase_end", {"name": "生成商务目录"})

        # ==============================================================================
        # 步骤4: 模板关联
        # ==============================================================================
        yield sse("phase_start", {"name": "步骤4: 模板关联"})
        async for event in run_template_linking_pipeline(
            catalog_input_path=settings.OUTPUT_PATHS["business_catalog"],
            templates_input_path=settings.INPUT_PATHS["templates"],
            catalog_output_path=settings.OUTPUT_PATHS["business_catalog_linked"],
            language=language
        ):
            yield event
        yield sse("phase_end", {"name": "步骤4: 模板关联"})

        yield sse("complete", {"final_output": "商务目录及模板关联已全部完成！", "catalog": business_framework})
        
    except Exception as e:
        error_info = {"type": type(e).__name__, "message": str(e)}
        yield sse("error", error_info)
    finally:
        print("商务目录生成任务已终止或完成。")


def _parse_and_execute_tool_calls(
    response_text: str, 
    business_framework: List[Dict]
) -> Tuple[int, List[str]]:
    """
    从LLM的响应文本中解析并执行目录编辑的工具调用。
    """
    logs = []
    tool_calls_to_process = []

    try:
        data = json.loads(response_text)
        if isinstance(data, list):
            tool_calls_to_process = data
        elif isinstance(data, dict):
            tool_calls_to_process = [data]
    except json.JSONDecodeError:
        json_strings = re.findall(r'```json\s*([\s\S]*?)\s*```', response_text)
        if not json_strings:
            json_strings = re.findall(r'```\s*([\s\S]*?)\s*```', response_text)
        
        for block_str in json_strings:
            try:
                data = json.loads(block_str)
                if isinstance(data, list):
                    tool_calls_to_process.extend(data)
                else:
                    tool_calls_to_process.append(data)
            except json.JSONDecodeError:
                logs.append(f"⚠️ JSON解析失败，跳过块: {block_str[:100]}")
                continue

    tool_calls_executed = 0

    if not tool_calls_to_process:
        logs.append(f"📋 未在响应中解析到任何有效的工具调用: {response_text[:200]}")
        return 0, logs

    for tool_call in tool_calls_to_process:
        if not isinstance(tool_call, dict):
            continue
        try:
            tool_name = tool_call.get("function") or tool_call.get("name") or tool_call.get("tool")
            args = tool_call.get("parameters") or tool_call.get("arguments") or tool_call.get("args") or tool_call.get("params") or {}
            if not args:
                args = tool_call

            if tool_name == "add_catalog_child":
                parent_path_raw = args.get("parent_catalog_path") or args.get("path") or args.get("parent_path")
                if isinstance(parent_path_raw, str):
                    parent_path = [p.strip() for p in parent_path_raw.split(">")]
                else:
                    parent_path = parent_path_raw
                
                node_data = args.get("new_child_catalog") or args.get("child") or args.get("new_catalog") or args.get("new_child")
                
                if not node_data:
                    child_name = args.get("child_name")
                    if child_name:
                        node_data = {"name": child_name, "children": [], "content_description": ""}

                if parent_path and node_data:
                    if find_and_add_node(business_framework, parent_path, node_data):
                        tool_calls_executed += 1
                        logs.append(f"✅ [ADD] 在 '{' > '.join(parent_path)}' 下添加 '{node_data.get('name')}'")
                    else:
                        logs.append(f"❌ [ADD] 在 '{' > '.join(parent_path)}' 添加失败")

            elif tool_name == "update_catalog_node":
                path_raw = args.get("catalog_path") or args.get("path") or args.get("target_path")
                if isinstance(path_raw, str):
                    path = [p.strip() for p in path_raw.split(">")]
                else:
                    path = path_raw

                description = args.get("content_description") or args.get("new_content_description")
                
                if path and description:
                    update_data = {"content_description": description}
                    if find_and_update_node(business_framework, path, update_data):
                        tool_calls_executed += 1
                        logs.append(f"✅ [UPDATE] 成功更新 '{' > '.join(path)}'")
                    else:
                        logs.append(f"❌ [UPDATE] 更新 '{' > '.join(path)}' 失败")

        except Exception as exec_error:
            logs.append(f"⚠️ 工具调用执行失败: {exec_error}")
            continue
            
    return tool_calls_executed, logs
