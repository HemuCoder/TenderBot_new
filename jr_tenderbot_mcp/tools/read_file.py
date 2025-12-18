import os
from typing import Annotated
from pydantic import Field
from utils.file_util import get_safe_path

def read_file_impl(
    path: Annotated[str, Field(description="要读取的文件的路径。")], 
    offset: Annotated[int, Field(description="可选：开始读取的行号 (0-based)。用于分页。")] = 0, # <-- 核心改动
    limit: Annotated[int, Field(description="可选：要读取的最大行数。用于分页。")] = -1 # <-- 核心改动
) -> str:
    """读取指定文件的完整内容。如果文件很大，它会自动截断并提供提示。你可以使用 offset 和 limit 参数来分页读取大文件。"""
    if offset is not None and offset < 0:
        return "错误: 'offset' 必须是一个非负整数。"
    if limit is not None and limit <= 0:
        return "错误: 'limit' 必须是一个正整数。"

    safe_path = get_safe_path(path)
    
    if os.path.isdir(safe_path):
        return f"错误: 路径 '{path}' 是一个目录，而不是文件。"

    try:
        with open(safe_path, "r", encoding="utf-8", newline='') as f:
            lines = f.readlines()
        
        total_lines = len(lines)
        
        # --- 核心改动：适配新的默认值 ---
        if offset == 0 and limit == -1:
            # 读取整个文件
            return "".join(lines)

        # 处理分页
        start = offset or 0
        end = start + (limit if limit != -1 else total_lines)
        
        if start >= total_lines:
            return f"错误: 'offset' ({start}) 超出文件总行数 ({total_lines})。"
            
        sliced_lines = lines[start:end]
        
        is_truncated = start > 0 or end < total_lines
        
        if not is_truncated:
            return "".join(sliced_lines)

        # 构建截断提示
        actual_end = start + len(sliced_lines)
        next_offset = actual_end
        
        hint = (
            f"重要: 文件内容已被截断。\n"
            f"状态: 显示行 {start + 1}-{actual_end}，共 {total_lines} 行。\n"
            f"操作: 要读取文件的下一部分，请在下一次调用中使用 'offset: {next_offset}'。\n"
            f"\n--- 文件内容 (截断) ---\n"
        )
        
        return hint + "".join(sliced_lines)

    except FileNotFoundError:
        return f"错误: 文件未找到 {path}"
    except Exception as e:
        return f"读取文件时发生错误: {e}"
