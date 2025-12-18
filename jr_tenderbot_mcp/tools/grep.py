import os
import re
from typing import Annotated
from pydantic import Field
from utils.file_util import get_safe_path, BASE_DIR

def grep_impl(
    pattern: Annotated[str, Field(description="要搜索的正则表达式，搜索时会忽略大小写。")], 
    path: Annotated[str, Field(description="可选：开始搜索的目录路径，默认为根目录 '.'。")] = "."
) -> str:
    """在指定路径下的文件中搜索与正则表达式匹配的行。"""
    try:
        # get_safe_path 已经包含了 BASE_DIR 的拼接和验证
        safe_search_path = get_safe_path(path)
        if not os.path.isdir(safe_search_path):
            return f"错误: 路径 '{path}' 不是一个有效的目录。"
    except ValueError as e:
        return f"错误: 无效的路径: {e}"
        
    matches = []
    try:
        regex = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        return f"错误: 无效的正则表达式: {e}"

    for root, _, files in os.walk(safe_search_path):
        for file in files:
            file_path = os.path.join(root, file)
            relative_path = os.path.relpath(file_path, BASE_DIR)
            
            try:
                # 简单地跳过非文本文件
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line_num, line in enumerate(f, 1):
                        if regex.search(line):
                            matches.append(f"{relative_path}:{line_num}:{line.strip()}")
            except Exception:
                # 忽略无法读取的文件
                continue
    
    if not matches:
        return f"在 '{path}' 目录下没有找到与 '{pattern}' 匹配的内容。"
    
    return "\n".join(matches)
