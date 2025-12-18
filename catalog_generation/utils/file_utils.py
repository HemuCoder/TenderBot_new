# -*- coding: utf-8 -*-
"""
@File    : file_utils.py
@Description: This file contains utility functions for file and data manipulation.
@Author  : <<your name>>
@Date    : <<date>>
@Version : 1.0
"""

# Input: Markdown content, JSON data, etc.
# Output: Processed data structures (JSON, lists, etc.).

import re
from typing import Dict, Any, List


def extract_json_from_response(response_text: str) -> str:
    """从 LLM 的响应文本中提取 JSON 字符串。"""
    # 查找被 ```json ... ``` 包围的代码块
    match = re.search(r"```json\s*([\s\S]*?)\s*```", response_text)
    if match:
        return match.group(1).strip()
    # 如果没有找到代码块，直接返回原始文本，让后续的 json.loads 尝试解析
    return response_text


def extract_section(markdown_content: str, section_name: str) -> str:
    """
    从完整的 Markdown 文本中，根据一级标题提取特定部分的内容。
    
    Args:
        markdown_content: 完整的 Markdown 文本。
        section_name: 要提取的部分的名称 (例如 "商务文件", "技术部分", "价格部分")。
    
    Returns:
        只包含指定部分的文本。
    """
    # 构建正则表达式，匹配从指定标题开始，到下一个一级标题之前的所有内容
    pattern = re.compile(rf'#\s*{section_name}.*?(?=\n#\s|\Z)', re.DOTALL | re.IGNORECASE)
    match = pattern.search(markdown_content)
    if match:
        return match.group(0).strip()
    
    # 兼容 example.md 中的格式
    pattern_alt = re.compile(rf'-\s*{section_name}.*?(?=\n-\s*第|\Z)', re.DOTALL | re.IGNORECASE)
    match_alt = pattern_alt.search(markdown_content)
    if match_alt:
        return match_alt.group(0).strip()
        
    return ""


def parse_requirement_blocks(checklist_content: str) -> List[str]:
    """
    将需求清单 Markdown 文本解析为需求块列表。
    
    每个需求块是一个以 "- [ ]" 开头的完整段落（包含所有子项）。
    
    Args:
        checklist_content: 完整的需求清单 Markdown 文本。
    
    Returns:
        需求块的列表，每个元素是一个完整的需求块字符串。
    """
    blocks = []
    lines = checklist_content.split('\n')
    current_block = []
    in_block = False
    
    for line in lines:
        # 检测需求块的开始（一级需求：- [ ] 开头，顶格或缩进很少）
        if re.match(r'^-\s*\[\s*\]\s+', line):
            # 如果之前有正在构建的块，保存它
            if current_block:
                blocks.append('\n'.join(current_block))
            # 开始新块
            current_block = [line]
            in_block = True
        elif in_block:
            # 继续收集当前块的内容
            # 如果是空行且下一行是新的一级需求，则结束当前块
            if line.strip() == '':
                # 检查是否是块之间的分隔
                current_block.append(line)
            elif line.startswith('#') and not line.startswith('##'):
                # 遇到新的一级标题，结束当前块
                if current_block:
                    blocks.append('\n'.join(current_block))
                current_block = []
                in_block = False
            else:
                # 普通行（子项、描述等）
                current_block.append(line)
    
    # 保存最后一个块
    if current_block:
        blocks.append('\n'.join(current_block))
    
    # 过滤掉空块
    blocks = [b.strip() for b in blocks if b.strip()]
    
    return blocks


def find_and_update_node(catalog: List[Dict], path: List[str], update_data: Dict) -> bool:
    """递归查找并更新节点，支持根路径模糊匹配。"""
    if not path:
        return False
        
    # --- 新增：根路径模糊匹配 ---
    if (len(catalog) == 1 and 
        catalog[0].get("name") and 
        path[0] != catalog[0].get("name")):
        
        child_names = [child.get("name") for child in catalog[0].get("children", [])]
        if path[0] in child_names:
            path.insert(0, catalog[0]["name"])
    # --- 结束 ---
            
    target_name = path[0]
    remaining_path = path[1:]
    
    for item in catalog:
        if item.get("name") == target_name:
            if not remaining_path:
                # --- 核心改动：采用更智能的"追加式"描述更新 ---
                if 'content_description' in update_data:
                    new_desc = update_data.pop('content_description', '')
                    if 'content_description' in item and item['content_description']:
                        # 如果已有描述，则将新描述作为补充内容追加
                        item['content_description'] += f"\n\n---\n# 补充要求\n{new_desc}"
                    else:
                        # 如果没有描述，则直接设置
                        item['content_description'] = new_desc
                
                # 更新其他可能的字段（例如 children）
                item.update(update_data)
                return True
            else:
                # 继续在子节点中查找
                if "children" in item:
                    if find_and_update_node(item["children"], remaining_path, update_data):
                        return True
    return False


def find_and_add_node(catalog: List[Dict], parent_path: List[str], node_data: Dict) -> bool:
    """
    递归查找父节点并添加子节点。
    支持根路径模糊匹配和中间路径自动创建。
    """
    if not parent_path:
        catalog.append(node_data)
        return True

    if not isinstance(node_data, dict):
        return False

    # --- 新增：根路径模糊匹配 ---
    # 如果目录只有一个根节点，且路径的第一个部分在根节点下能找到，则自动补全根路径
    if (len(catalog) == 1 and 
        catalog[0].get("name") and 
        parent_path[0] != catalog[0].get("name")):
        
        child_names = [child.get("name") for child in catalog[0].get("children", [])]
        if parent_path[0] in child_names:
            parent_path.insert(0, catalog[0]["name"])
    # --- 结束 ---

    current_level_nodes = catalog
    
    # 遍历路径，查找或创建父节点
    for i, part in enumerate(parent_path):
        found_node = None
        for node in current_level_nodes:
            if node.get("name") == part:
                found_node = node
                break
        
        if found_node:
            # 如果找到了节点，则进入下一层 children
            current_level_nodes = found_node.setdefault("children", [])
        else:
            # 如果没找到，创建新的父节点
            new_parent = {"name": part, "children": []}
            current_level_nodes.append(new_parent)
            current_level_nodes = new_parent["children"]
            
    # 在最终找到或创建的父节点的 children 中添加新节点
    current_level_nodes.append(node_data)
    return True


def assign_ids_and_levels(catalog: List[Dict], level: int = 1, prefix: str = "cat") -> None:
    """递归地为目录分配ID和层级"""
    for i, item in enumerate(catalog, 1):
        item_id = f"{prefix}_{i:03d}"
        item["id"] = item_id
        item["level"] = level
        if "children" in item and item["children"]:
            assign_ids_and_levels(item["children"], level + 1, item_id)


def build_nested_catalog(flat_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    将一个带有 'level' 属性的扁平列表，转换为一个带有 'children' 的嵌套结构。
    """
    if not flat_list or not isinstance(flat_list, list):
        return []

    nested_catalog = []
    # 栈，用于追踪各层级的父节点。键是层级，值是父节点的 'children' 列表。
    parent_stack = {0: nested_catalog}

    for item in flat_list:
        level = item.get("level")
        name = item.get("name")

        if level is None or name is None:
            continue

        # 创建嵌套格式的新节点
        new_node = {"name": name, "children": []}

        # 在栈中找到正确的父节点（当前节点的上一级）
        parent_level = level - 1
        
        # 如果层级不连续（例如从level 1直接到level 3），向上找到最近的有效父节点
        while parent_level not in parent_stack and parent_level > 0:
            parent_level -= 1

        # 默认为根目录
        parent_list = parent_stack.get(parent_level, nested_catalog)
        parent_list.append(new_node)
        
        # 为当前层级更新栈
        parent_stack[level] = new_node["children"]

    return nested_catalog


def extract_business_section(full_catalog: List[Dict]) -> List[Dict]:
    """从完整的目录框架中，只提取出"商务部分"的节点。"""
    for node in full_catalog:
        # 使用模糊匹配，避免因"第一部分"等前缀导致匹配失败
        if "商务" in node.get("name", ""):
            return [node] # 返回一个只包含商务节点的列表
    return [] # 如果没找到，返回空列表


def extract_technical_section(full_catalog: List[Dict]) -> List[Dict]:
    """从完整的目录框架中，只提取出"技术部分"的节点。"""
    for node in full_catalog:
        # 使用模糊匹配，避免因"第二部分"等前缀导致匹配失败
        if "技术" in node.get("name", ""):
            return [node] # 返回一个只包含技术节点的列表
    return [] # 如果没找到，返回空列表

def extract_section_as_json(full_catalog: List[Dict], section_keyword: str) -> List[Dict]:
    """从完整的目录框架中，根据关键词提取特定部分。"""
    for node in full_catalog:
        if section_keyword in node.get("name", ""):
            return [node]
    return []

def convert_json_to_markdown(catalog_json: List[Dict], indent_level: int = 0, include_descriptions: bool = True) -> str:
    """
    将 JSON 格式的目录结构转换为 Markdown 列表格式。
    
    Args:
        catalog_json: JSON 格式的目录结构
        indent_level: 当前缩进级别
        include_descriptions: 是否包含 content_description
        
    Returns:
        Markdown 格式的目录文本
    """
    result = []
    indent = "  " * indent_level  # 每级缩进2个空格
    
    for item in catalog_json:
        name = item.get("name", "")
        line = f"{indent}- {name}"
        
        # 如果有 content_description 且需要包含，则添加
        if include_descriptions:
            description = item.get("content_description", "")
            if description:
                line += f"\n{indent}  > {description}"
        
        result.append(line)
        
        # 递归处理子节点
        children = item.get("children", [])
        if children:
            child_md = convert_json_to_markdown(children, indent_level + 1, include_descriptions)
            result.append(child_md)
    
    return "\n".join(result)


def parse_markdown_to_json(markdown_text: str) -> List[Dict[str, Any]]:
    """
    将 Markdown 列表格式的目录转换为 JSON 结构。
    
    Args:
        markdown_text: Markdown 格式的目录文本
    
    Returns:
        层级化的 JSON 数组
    """
    lines = markdown_text.strip().split('\n')
    root = []
    stack = [(root, -1)]  # (当前节点的children列表, 缩进级别)
    
    for line in lines:
        if not line.strip() or not line.strip().startswith('-'):
            continue
        
        # 计算缩进级别（每2个空格为一级）
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        level = indent // 2
        
        # 移除列表标记和前后空格
        name = stripped.lstrip('-').strip()
        if not name:
            continue
        
        # 创建新节点
        node = {
            "name": name,
            "description": "",
            "children": []
        }
        
        # 找到正确的父节点
        while len(stack) > 1 and stack[-1][1] >= level:
            stack.pop()
        
        # 添加到父节点
        stack[-1][0].append(node)
        
        # 将当前节点加入栈，作为潜在的父节点
        stack.append((node["children"], level))
    
    return root


def extract_leaf_nodes(catalog: List[Dict], parent_path: str = "") -> List[Dict]:
    """
    提取所有叶子节点及其路径
    返回: [{'path': '路径', 'name': '名称', 'node': 节点引用}, ...]
    """
    leaf_nodes = []
    
    def traverse(node, path):
        current_path = f"{path} > {node['name']}" if path else node['name']
        
        if not node.get('children'):  # 叶子节点
            leaf_nodes.append({
                'path': current_path,
                'name': node['name'],
                'node': node  # 保留引用，用于后续回填
            })
        else:
            for child in node['children']:
                traverse(child, current_path)
    
    for item in catalog:
        traverse(item, "")
    
    return leaf_nodes


def locate_text_segment(node_name: str, full_text: str, context_lines: int = 50) -> str:
    """
    在原文中定位目录对应的文本片段
    返回该目录前后的相关文本（而不是整篇）
    """
    lines = full_text.split('\n')
    
    # 尝试多种匹配模式
    patterns = [
        node_name,  # 直接匹配
        re.escape(node_name),  # 转义特殊字符
        f"\\d+\\.\\d*\\s*{re.escape(node_name)}",  # 带编号：5.1 投标人基本情况表
    ]
    
    start_idx = -1
    
    # 找到目录名称所在行
    for i, line in enumerate(lines):
        for pattern in patterns:
            if re.search(pattern, line, re.IGNORECASE):
                start_idx = i
                break
        if start_idx != -1:
            break
    
    if start_idx == -1:
        return ""  # 如果找不到，返回空
    
    # 提取前后文本
    start = max(0, start_idx - 2)  # 往前取2行
    end = min(len(lines), start_idx + context_lines)  # 往后取 context_lines 行
    
    return '\n'.join(lines[start:end])


def collect_leaf_nodes_with_path(catalog: List[Dict], parent_path: List[str] = None) -> List[Dict]:
    """
    收集所有叶子节点（children为空的节点），并记录其路径和节点引用。
    
    Args:
        catalog: 目录结构列表
        parent_path: 父级路径列表
    
    Returns:
        包含叶子节点信息的列表，每个元素包含:
        - path: 节点路径（列表）
        - name: 节点名称
        - node: 节点引用（用于后续修改）
    """
    if parent_path is None:
        parent_path = []
    
    leaf_nodes = []
    
    for item in catalog:
        current_path = parent_path + [item['name']]
        
        if not item.get('children') or len(item.get('children', [])) == 0:
            # 叶子节点
            leaf_nodes.append({
                'path': current_path,
                'name': item['name'],
                'node': item
            })
        else:
            # 递归处理子节点
            leaf_nodes.extend(collect_leaf_nodes_with_path(item['children'], current_path))
    
    return leaf_nodes


def collect_all_nodes_with_path(catalog: List[Dict], parent_path: List[str] = None) -> List[Dict]:
    """
    收集所有节点（包括非叶子节点），并记录其路径和节点引用。
    
    Args:
        catalog: 目录结构列表
        parent_path: 父级路径列表
    
    Returns:
        包含所有节点信息的列表，每个元素包含:
        - path: 节点路径（列表）
        - name: 节点名称
        - node: 节点引用（用于后续修改）
        - has_children: 是否有子节点
    """
    if parent_path is None:
        parent_path = []
    
    all_nodes = []
    
    for item in catalog:
        current_path = parent_path + [item['name']]
        has_children = bool(item.get('children') and len(item.get('children', [])) > 0)
        
        all_nodes.append({
            'path': current_path,
            'name': item['name'],
            'node': item,
            'has_children': has_children
        })
        
        # 递归处理子节点
        if has_children:
            all_nodes.extend(collect_all_nodes_with_path(item['children'], current_path))
    
    return all_nodes

def add_empty_linking_field(nodes: List[Dict]):
    """递归地为所有节点添加 'linked_template_ids': [] 字段。"""
    for node in nodes:
        node['linked_template_ids'] = []
        if node.get('children'):
            add_empty_linking_field(node['children'])
