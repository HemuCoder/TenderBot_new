"""
============================================================
CATALOG CLASSIFIER
目录分类器 - 基于 Agent 标注提取视图
============================================================
"""

from typing import Dict, Any, List


class CatalogClassifier:
    """
    目录分类器
    
    职责:
    完全信任 Agent 标注的 category 字段,提取对应视图
    
    设计哲学:
    - Agent 负责智能判断,Classifier 只负责机械执行
    - 消除关键词匹配的脆弱性和逻辑冲突
    """
    
    def classify_node(self, node: Dict[str, Any]) -> str:
        """
        获取节点类别
        
        Args:
            node: 节点对象
        
        Returns:
            "business" | "technical" | "pricing" | "mixed" | "unknown"
        """
        # 完全信任 Agent 的标注
        category = node.get("category", "unknown")
        
        # 确保返回值在预期范围内
        valid_categories = {"business", "technical", "pricing", "mixed", "unknown"}
        return category if category in valid_categories else "unknown"
    
    def extract_view(
        self,
        structure: List[Dict[str, Any]],
        target_type: str
    ) -> List[Dict[str, Any]]:
        """
        提取指定类型的视图
        
        Args:
            structure: 完整结构
            target_type: "business" | "technical" | "pricing"
        
        Returns:
            过滤后的结构
        """
        result = []
        
        for node in structure:
            category = self.classify_node(node)
            
            # 包含条件:
            # 1. 类别完全匹配
            # 2. 混合节点(包含所有视图)
            # 3. 未知类型(保守策略,包含所有视图)
            should_include = (
                category == target_type or 
                category == "mixed" or 
                category == "unknown"
            )
            
            if should_include:
                # 深拷贝节点
                filtered_node = {"name": node["name"]}
                
                # 复制所有字段(除了children)
                for key, value in node.items():
                    if key != "children":
                        filtered_node[key] = value
                
                # 递归处理子节点
                if node.get("children"):
                    filtered_children = self.extract_view(node["children"], target_type)
                    # 只有当子节点非空时才添加children字段
                    if filtered_children:
                        filtered_node["children"] = filtered_children
                    else:
                        filtered_node["children"] = []
                else:
                    filtered_node["children"] = []
                
                result.append(filtered_node)
        
        return result
    
    def classify_and_split(
        self,
        compensated_structure: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        分类并拆分为三个视图
        
        Args:
            compensated_structure: 补偿后的完整结构
        
        Returns:
            {
                "business": [...],
                "technical": [...],
                "pricing": [...]
            }
        """
        return {
            "business": self.extract_view(compensated_structure, "business"),
            "technical": self.extract_view(compensated_structure, "technical"),
            "pricing": self.extract_view(compensated_structure, "pricing")
        }
