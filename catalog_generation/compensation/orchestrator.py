"""
============================================================
COMPENSATION ORCHESTRATOR
补偿编排器 - 主流程控制
============================================================
"""

import sys
from pathlib import Path
from typing import Dict, Any, List

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from compensation.tools import GetDefaultTemplateTool
from compensation.agent import CompensationReActAgent


class CompensationOrchestrator:
    """
    补偿编排器
    
    流程:
    1. 判断提取结果是否为空
    2. 为空 → 使用默认三段式模板
    3. 不为空 → 交给 ReAct Agent 智能处理
    
    设计哲学:
    - 简单判断直接做,复杂决策交给 Agent
    - 消除不必要的抽象层
    """
    
    def __init__(self, api_key: str = None, model_name: str = None):
        self.template_tool = GetDefaultTemplateTool()
        self.agent = CompensationReActAgent(
            api_key=api_key or settings.OPENAI_API_KEY,
            model_name=model_name or settings.DEFAULT_MODEL_NAME
        )
    
    async def run(
        self, 
        extraction_result: List[Dict[str, Any]], 
        log_file: str = None, 
        log_callback=None
    ) -> Dict[str, Any]:
        """
        主流程
        
        Args:
            extraction_result: 提取到的目录结构
            log_file: 日志文件路径(可选)
            log_callback: 日志回调函数(可选)
        
        Returns:
            {
                "compensated_structure": [...],
                "source": "default_template" | "agent",
                "log": [...]
            }
        """
        log = []
        
        # 直接判断是否为空,不需要 Detector 包装
        is_empty = not extraction_result or len(extraction_result) == 0
        node_count = len(extraction_result) if extraction_result else 0
        
        log.append(f"检测结果: {'为空' if is_empty else f'有 {node_count} 个节点'}")
        
        if is_empty:
            # 为空 → 使用默认模板
            log.append("提取结果为空,使用默认三段式模板")
            compensated = self._get_default_structure()
            source = "default_template"
            agent_log = []
        else:
            # 不为空 → 交给 Agent
            log.append("提取结果不为空,交给 ReAct Agent 处理")
            agent_result = await self._use_agent(
                extraction_result, 
                log_file=log_file, 
                log_callback=log_callback
            )
            compensated = agent_result["compensated_structure"]
            agent_log = agent_result.get("log", [])
            source = "agent"
        
        return {
            "compensated_structure": compensated,
            "source": source,
            "log": log + agent_log
        }
    
    def _get_default_structure(self) -> List[Dict[str, Any]]:
        """
        获取默认三段式结构
        
        Returns:
            [商务模板, 技术模板, 报价模板]
        """
        business = self.template_tool.run("business")["template"]
        technical = self.template_tool.run("technical")["template"]
        pricing = self.template_tool.run("pricing")["template"]
        
        return [business, technical, pricing]
    
    async def _use_agent(
        self, 
        extraction_result: List[Dict[str, Any]], 
        log_file: str = None, 
        log_callback=None
    ) -> Dict[str, Any]:
        """
        使用 ReAct Agent 处理
        
        Args:
            extraction_result: 提取结果
            log_file: 日志文件路径
            log_callback: 日志回调函数
        
        Returns:
            Agent 处理后的结果
        """
        print("\n" + "="*60)
        print("ReAct Agent 开始处理")
        print("="*60)
        print(f"输入: {len(extraction_result)} 个节点")
        if log_file:
            print(f"日志文件: {log_file}")
        
        result = await self.agent.run(
            extraction_result, 
            log_file=log_file, 
            log_callback=log_callback
        )
        
        print(f"\n输出: {len(result['compensated_structure'])} 个节点")
        print("="*60)
        
        return result

