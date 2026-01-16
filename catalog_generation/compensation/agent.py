"""
============================================================
REACT AGENT
补偿 Agent - 智能分析和补偿缺失模块
============================================================
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, Any, List, AsyncGenerator
import httpx

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from compensation.tools import GetDefaultTemplateTool, UpdateNodeCategoryTool


class CompensationReActAgent:
    """
    补偿 ReAct Agent
    
    任务:
    1. 分析提取到的目录结构
    2. 判断缺失哪些模块(商务/技术/报价)
    3. 调用工具补偿缺失模块
    4. 给节点标注类别
    5. 返回完整结构
    """
    
    def __init__(
        self,
        api_url: str = "https://vip.dmxapi.com/v1/chat/completions",
        api_key: str = None,
        model_name: str = None
    ):
        self.api_url = api_url
        self.api_key = api_key or settings.OPENAI_API_KEY
        self.model_name = model_name or settings.DEFAULT_MODEL_NAME
        
        # 初始化工具
        self.tools = {
            "get_default_template": GetDefaultTemplateTool(),
            "update_node_category": UpdateNodeCategoryTool()
        }
        
        # 系统提示词
        self.system_prompt = self._build_system_prompt()
    
    def _build_system_prompt(self) -> str:
        """构建系统提示词"""
        return """你是投标文件目录补偿专家。

## 任务
分析招标文件目录结构,判断缺失模块并补偿,给节点标注类别。

## 模块类型
- **business**: 商务标(表格、证明文件、资质材料)
- **technical**: 技术标(方案、措施、计划)
- **pricing**: 报价表(价格、费用)

## 分类规则
1. 表格/模板/填写/盖章 → business
2. 方案/措施/计划 → technical
3. 报价/价格/费用 → pricing
4. 混合内容 → 标记为 mixed,子节点单独标注

## 可用工具

**get_default_template(module_type)**
获取默认模板(business/technical/pricing)

**update_node_category(structure, node_path, category)**
给节点标注类别,路径格式: "root/节点名/子节点名"

## 工作流程
1. 分析现有结构,识别每个节点的类型
2. 判断缺失哪些模块(business/technical/pricing)
3. 调用 get_default_template 补偿缺失模块
4. 调用 update_node_category 标注节点类别
5. 返回完整结构

## 输出格式

推理时:
```
Thought: [分析]
Action: [工具名]
Action Input: {"param": "value"}
```

完成时:
```
Thought: [总结]
Final Answer: [完整JSON结构]
```

## 规则
- 只补偿确实缺失的模块
- 不修改已提取的节点
- 不确定的标记为 unknown
- 每步说明理由

开始分析!
"""
    
    async def run(self, extraction_result: List[Dict[str, Any]], log_file: str = None, log_callback=None) -> Dict[str, Any]:
        """
        运行 Agent (ReAct 循环)
        
        Args:
            extraction_result: 提取到的目录结构
            log_file: 日志文件路径(可选)
            log_callback: 日志回调函数(可选),用于流式输出
        
        Returns:
            {
                "compensated_structure": [...],
                "log": [...]
            }
        """
        # 初始化日志文件
        if log_file:
            log_f = open(log_file, 'w', encoding='utf-8')
            def log_print(msg):
                """同时输出到控制台、文件和回调"""
                print(msg)
                log_f.write(msg + '\n')
                log_f.flush()
                if log_callback:
                    log_callback(msg)
        else:
            def log_print(msg):
                """输出到控制台和回调"""
                print(msg)
                if log_callback:
                    log_callback(msg)
        
        # 初始化对话历史
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": f"""请分析以下提取到的招标文件格式要求,并进行补偿:

```json
{json.dumps(extraction_result, ensure_ascii=False, indent=2)}
```

请按照 ReAct 格式逐步分析和处理。"""}
        ]
        
        log_print("\n" + "="*60)
        log_print("Agent ReAct 循环:")
        log_print("="*60)
        
        max_iterations = 10
        current_structure = extraction_result.copy()
        
        try:
            for iteration in range(1, max_iterations + 1):
                log_print(f"\n[迭代 {iteration}]")
                log_print("-" * 60)
                
                # 1. 调用 LLM
                response = await self._call_llm_non_streaming(messages)
                
                # 打印 Agent 响应
                log_print(response)
                
                # 2. 检查是否完成
                if "Final Answer:" in response:
                    log_print(f"\n✓ Agent 完成任务 (迭代 {iteration})")
                    final_structure = self._extract_final_answer(response)
                    if final_structure:
                        # 验证结构是否有效
                        validation_error = self._validate_structure(final_structure)
                        if validation_error:
                            log_print(f"⚠️ Final Answer 结构验证失败: {validation_error}")
                            log_print("→ 将错误反馈给 Agent,要求修复...")
                            
                            # 反馈错误给 Agent
                            messages.append({"role": "assistant", "content": response})
                            messages.append({"role": "user", "content": f"""你的 Final Answer 存在以下问题:

{validation_error}

请修复这些问题,重新输出正确的 Final Answer。

要求:
1. 必须输出数组格式 [...] 或包含 children 字段的对象格式 {{"name": "root", "children": [...]}}
2. 每个节点必须包含 name 字段
3. 每个节点应该包含 category 字段(business/technical/pricing/unknown/mixed)
4. 保持原有的 content_description 和 children 字段

请立即修复并返回 Final Answer。"""})
                            continue  # 继续下一轮迭代
                        
                        # 结构有效,返回结果
                        return {
                            "compensated_structure": final_structure,
                            "log": [f"Agent 完成,共 {iteration} 次迭代"]
                        }
                    else:
                        log_print("⚠️ 解析 Final Answer 失败")
                        log_print("→ 将错误反馈给 Agent,要求修复...")
                        
                        # 反馈解析失败给 Agent
                        messages.append({"role": "assistant", "content": response})
                        messages.append({"role": "user", "content": """你的 Final Answer 无法被正确解析。

可能的问题:
1. JSON 格式不正确(缺少引号、逗号、括号等)
2. 格式不是数组 [...] 也不是对象 {"name": "root", "children": [...]}
3. JSON 中包含了注释或其他非法字符

请检查你的 Final Answer,确保:
1. 输出完整的、语法正确的 JSON
2. 使用数组格式 [...] 或对象格式 {"name": "root", "children": [...]}
3. 所有字符串都用双引号包裹
4. 对象之间用逗号分隔,最后一个对象后面不要逗号

请立即修复并返回正确的 Final Answer。"""})
                        continue  # 继续下一轮迭代
                
                # 3. 解析 Action
                action_name, action_input = self._parse_action(response)
                
                if not action_name:
                    log_print("⚠️ 未找到 Action,继续...")
                    messages.append({"role": "assistant", "content": response})
                    messages.append({"role": "user", "content": "请继续,使用工具或返回 Final Answer"})
                    continue
                
                log_print(f"\n→ 执行工具: {action_name}")
                log_print(f"  参数: {json.dumps(action_input, ensure_ascii=False)}")
                
                # 4. 执行工具
                observation = self._execute_tool(action_name, action_input, current_structure)
                
                log_print(f"  结果: {observation[:200]}...")
                
                # 5. 更新对话历史
                messages.append({"role": "assistant", "content": response})
                messages.append({"role": "user", "content": f"Observation: {observation}\n\n请继续分析,或返回 Final Answer。"})
            
            log_print(f"\n⚠️ 达到最大迭代次数 ({max_iterations}),使用当前结构")
            return {
                "compensated_structure": current_structure,
                "log": [f"达到最大迭代次数 {max_iterations}"]
            }
        finally:
            if log_file:
                log_f.close()
    
    async def _call_llm_non_streaming(self, messages: List[Dict]) -> str:
        """
        调用 LLM (非流式,带重试)
        
        设计:
        - 超时 180 秒(Agent 推理需要时间)
        - 最多重试 3 次
        - 每次重试间隔 5 秒
        """
        import asyncio
        
        headers = {
            'Accept': 'application/json',
            'Authorization': f'Bearer {self.api_key}',
            'User-Agent': 'DMXAPI/1.0.0',
            'Content-Type': 'application/json'
        }
        
        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": False
        }
        
        max_retries = 3
        retry_delay = 5  # 秒
        
        for attempt in range(1, max_retries + 1):
            try:
                # 增加超时到 180 秒
                async with httpx.AsyncClient(timeout=180.0) as client:
                    response = await client.post(self.api_url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                    return data["choices"][0]["message"]["content"]
            except (httpx.RemoteProtocolError, httpx.TimeoutException) as e:
                if attempt < max_retries:
                    print(f"\n⚠️ LLM API 调用失败 (尝试 {attempt}/{max_retries}): {str(e)}")
                    print(f"   → {retry_delay} 秒后重试...")
                    await asyncio.sleep(retry_delay)
                else:
                    print(f"\n❌ LLM API 调用失败 (已重试 {max_retries} 次): {str(e)}")
                    raise
            except Exception as e:
                print(f"\n❌ LLM API 调用失败: {str(e)}")
                raise
    
    def _parse_action(self, response: str) -> tuple:
        """
        解析 Action
        
        Returns:
            (action_name, action_input)
        """
        # 查找 Action: 和 Action Input:
        action_match = re.search(r'Action:\s*(\w+)', response)
        input_match = re.search(r'Action Input:\s*(\{.*?\})', response, re.DOTALL)
        
        if not action_match:
            return None, None
        
        action_name = action_match.group(1)
        
        if input_match:
            try:
                action_input = json.loads(input_match.group(1))
            except json.JSONDecodeError:
                print(f"⚠️ 解析 Action Input 失败: {input_match.group(1)}")
                action_input = {}
        else:
            action_input = {}
        
        return action_name, action_input
    
    def _execute_tool(self, tool_name: str, tool_input: Dict, current_structure: List[Dict]) -> str:
        """
        执行工具
        
        Returns:
            Observation 文本
        """
        if tool_name == "get_default_template":
            module_type = tool_input.get("module_type")
            if not module_type:
                return "错误: 缺少 module_type 参数"
            
            result = self.tools["get_default_template"].run(module_type)
            
            if result["success"]:
                template = result["template"]
                return f"""成功获取 {module_type} 模块的默认模板:
标题: {template['title']}
类别: {template['category']}
子节点数: {len(template.get('children', []))}
插入位置建议: {result['insert_position_hint']}

模板结构:
{json.dumps(template, ensure_ascii=False, indent=2)}
"""
            else:
                return f"错误: {result.get('error', '未知错误')}"
        
        elif tool_name == "update_node_category":
            node_path = tool_input.get("node_path")
            category = tool_input.get("category")
            
            if not node_path or not category:
                return "错误: 缺少 node_path 或 category 参数"
            
            # 构造完整结构
            full_structure = {
                "name": "root",
                "children": current_structure
            }
            
            result = self.tools["update_node_category"].run(full_structure, node_path, category)
            
            if result["success"]:
                # 更新 current_structure
                current_structure.clear()
                current_structure.extend(result["updated_structure"]["children"])
                return result["message"]
            else:
                return f"错误: {result.get('error', '未知错误')}"
        
        else:
            return f"错误: 未知工具 '{tool_name}'"
    
    def _extract_final_answer(self, response: str) -> List[Dict]:
        """提取 Final Answer"""
        # 查找 Final Answer: 后的 JSON
        match = re.search(r'Final Answer:\s*```json\s*(.*?)\s*```', response, re.DOTALL)
        if not match:
            # 尝试匹配数组
            match = re.search(r'Final Answer:\s*(\[.*\])', response, re.DOTALL)
        if not match:
            # 尝试匹配对象
            match = re.search(r'Final Answer:\s*(\{.*\})', response, re.DOTALL)
        
        if match:
            try:
                result = json.loads(match.group(1))
                
                # 如果是对象且有 children 字段,提取 children
                if isinstance(result, dict) and 'children' in result:
                    return result['children']
                # 如果是数组,直接返回
                elif isinstance(result, list):
                    return result
                else:
                    print(f"⚠️ Final Answer 格式不正确: 既不是数组也不是包含 children 的对象")
                    return None
                    
            except json.JSONDecodeError as e:
                print(f"⚠️ 解析 Final Answer 失败: {str(e)}")
        
        return None
    
    def _validate_structure(self, structure: List[Dict]) -> str:
        """
        验证结构是否有效
        
        Returns:
            错误信息,如果结构有效则返回 None
        """
        if not isinstance(structure, list):
            return "结构必须是数组"
        
        if len(structure) == 0:
            return "结构不能为空"
        
        errors = []
        
        def validate_node(node, path="root"):
            """递归验证节点"""
            if not isinstance(node, dict):
                errors.append(f"{path}: 节点必须是对象")
                return
            
            # 检查必需字段
            if "name" not in node:
                errors.append(f"{path}: 缺少 name 字段")
            
            # 检查 children 字段
            if "children" in node:
                if not isinstance(node["children"], list):
                    errors.append(f"{path}: children 必须是数组")
                else:
                    for i, child in enumerate(node["children"]):
                        child_path = f"{path}/{node.get('name', f'node{i}')}"
                        validate_node(child, child_path)
        
        # 验证所有顶层节点
        for i, node in enumerate(structure):
            validate_node(node, f"root/node{i}")
        
        if errors:
            return "\n".join(errors)
        
        return None
