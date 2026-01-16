"""
============================================================
COMPENSATION TOOLS
补偿工具集 - 供 ReAct Agent 调用的工具
============================================================
"""

import json
import os
from typing import Dict, Any, List, Optional
from pathlib import Path


# ============================================================
# TOOL 1: GET DEFAULT TEMPLATE
# 获取默认模板
# ============================================================

class GetDefaultTemplateTool:
    """
    获取默认目录模板
    
    功能:
    - 根据模块类型(business/technical/pricing)返回对应的默认模板
    - 返回模板结构和插入位置建议
    """
    
    name = "get_default_template"
    description = "获取指定模块类型的默认目录模板"
    
    def __init__(self, template_file_path: str = None):
        """
        初始化工具
        
        Args:
            template_file_path: 模板文件路径,默认使用 mcp-file/data/default_templates.json
        """
        if template_file_path is None:
            # 默认路径指向 mcp-file/data 目录
            current_dir = Path(__file__).parent
            project_root = current_dir.parent.parent
            template_file_path = project_root / "jr_tenderbot_mcp" / "mcp-file" / "data" / "default_templates.json"
        
        self.template_file_path = template_file_path
        self._load_templates()
    
    def _load_templates(self):
        """加载模板文件"""
        with open(self.template_file_path, 'r', encoding='utf-8') as f:
            self.templates = json.load(f)
    
    def run(self, module_type: str) -> Dict[str, Any]:
        """
        执行工具
        
        Args:
            module_type: 模块类型 (business/technical/pricing)
        
        Returns:
            {
                "module": "business",
                "template": {...},
                "insert_position_hint": "before_all",
                "success": true
            }
        """
        template_key = f"{module_type}_template"
        
        if template_key not in self.templates:
            return {
                "success": False,
                "error": f"未找到模块类型 '{module_type}' 的模板"
            }
        
        template = self.templates[template_key]
        
        return {
            "success": True,
            "module": module_type,
            "template": template,
            "insert_position_hint": template.get("insert_position_hint", "after_all")
        }


# ============================================================
# TOOL 2: GET EXTRACTION RESULT
# 获取提取结果
# ============================================================

class GetExtractionResultTool:
    """
    获取前一阶段提取到的目录结构
    
    功能:
    - 读取提取结果文件
    - 返回完整的目录结构
    """
    
    name = "get_extraction_result"
    description = "获取前一阶段提取到的招标文件目录结构"
    
    def run(self, file_path: str) -> Dict[str, Any]:
        """
        执行工具
        
        Args:
            file_path: 提取结果文件路径
        
        Returns:
            {
                "success": true,
                "structure": {...},
                "metadata": {...}
            }
        """
        if not os.path.exists(file_path):
            return {
                "success": False,
                "error": f"文件不存在: {file_path}"
            }
        
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            return {
                "success": True,
                "structure": data,
                "metadata": {
                    "source_file": file_path,
                    "node_count": self._count_nodes(data)
                }
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"读取文件失败: {str(e)}"
            }
    
    def _count_nodes(self, node: Dict) -> int:
        """递归统计节点数量"""
        count = 1
        if "children" in node and node["children"]:
            for child in node["children"]:
                count += self._count_nodes(child)
        return count


# ============================================================
# TOOL 3: UPDATE NODE CATEGORY
# 更新节点类别
# ============================================================

class UpdateNodeCategoryTool:
    """
    更新节点类别
    
    功能:
    - Agent 指定节点路径和类别
    - 工具给该节点打上类别标签
    - 子节点自动继承父节点类别
    
    注意:
    - 工具不做任何判断,只执行 Agent 的指令
    - Agent 负责分析和决策
    """
    
    name = "update_node_category"
    description = "给指定节点更新类别标签,子节点自动继承"
    
    def run(self, structure: Dict[str, Any], node_path: str, category: str) -> Dict[str, Any]:
        """
        执行工具
        
        Args:
            structure: 完整的目录结构
            node_path: 节点路径,用 "/" 分隔,例如 "root/商务标/投标函"
            category: 要标注的类别 (business/technical/pricing)
        
        Returns:
            {
                "success": true,
                "updated_structure": {...},
                "message": "已将节点 '商务标' 标注为 business"
            }
        """
        if category not in ["business", "technical", "pricing"]:
            return {
                "success": False,
                "error": f"无效的类别: {category},必须是 business/technical/pricing 之一"
            }
        
        # 复制结构,避免修改原始数据
        updated_structure = self._deep_copy(structure)
        
        # 找到目标节点
        target_node = self._find_node_by_path(updated_structure, node_path)
        
        if target_node is None:
            return {
                "success": False,
                "error": f"未找到节点: {node_path}"
            }
        
        # 标注节点及其所有子节点
        self._annotate_node_and_children(target_node, category)
        
        return {
            "success": True,
            "updated_structure": updated_structure,
            "message": f"已将节点 '{target_node.get('name', 'unknown')}' 及其子节点标注为 {category}"
        }
    
    def _deep_copy(self, node: Dict) -> Dict:
        """深拷贝节点"""
        copied = {
            "name": node.get("name", ""),
            "category": node.get("category")
        }
        
        if "children" in node and node["children"]:
            copied["children"] = [self._deep_copy(child) for child in node["children"]]
        else:
            copied["children"] = []
        
        # 保留其他字段
        for key, value in node.items():
            if key not in ["name", "category", "children"]:
                copied[key] = value
        
        return copied
    
    def _find_node_by_path(self, structure: Dict, path: str) -> Optional[Dict]:
        """
        根据路径查找节点
        
        Args:
            structure: 目录结构
            path: 节点路径,例如 "root/商务标/投标函"
        
        Returns:
            找到的节点,如果未找到返回 None
        """
        if not path or path == "root":
            return structure
        
        parts = path.split("/")
        current = structure
        
        for part in parts:
            if part == "root":
                continue
            
            # 在子节点中查找
            found = False
            if "children" in current and current["children"]:
                for child in current["children"]:
                    if child.get("name") == part:
                        current = child
                        found = True
                        break
            
            if not found:
                return None
        
        return current
    
    def _annotate_node_and_children(self, node: Dict, category: str):
        """
        标注节点及其所有子节点
        
        Args:
            node: 要标注的节点
            category: 类别
        """
        node["category"] = category
        
        # 递归标注所有子节点
        if "children" in node and node["children"]:
            for child in node["children"]:
                self._annotate_node_and_children(child, category)
