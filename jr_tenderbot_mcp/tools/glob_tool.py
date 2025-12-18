import os
from datetime import datetime
from fnmatch import fnmatchcase
from typing import Annotated
from pydantic import Field
from utils.file_util import get_safe_path, BASE_DIR

def glob_tool_impl(
    pattern: Annotated[str, Field(description="要匹配的 glob 模式 (例如, '**/*.py', 'docs/*.md')。")], 
    path: Annotated[str, Field(description="可选：在其内搜索的目录路径。如果省略，则在根目录中搜索。")] = ".", 
    case_sensitive: Annotated[bool, Field(description="可选：搜索是否应区分大小写。默认为 False。")] = False
) -> str:
    """高效地查找匹配特定 glob 模式的文件，并按修改时间智能排序。"""
    # 1. 参数验证
    if not pattern or not isinstance(pattern, str) or pattern.strip() == '':
        return "错误: 'pattern' 参数不能为空。"

    try:
        safe_search_path = get_safe_path(path)
        if not os.path.isdir(safe_search_path):
            return f"错误: 搜索路径 '{path}' 不是一个有效的目录。"
    except ValueError as e:
        return f"错误: 无效的搜索路径: {e}"

    # 2. 执行 Glob 搜索
    all_files = []
    for root, _, files in os.walk(safe_search_path):
        for filename in files:
            full_path = os.path.join(root, filename)
            # fnmatchcase 默认区分大小写
            if not case_sensitive:
                if fnmatchcase(filename.lower(), pattern.lower()):
                    all_files.append(full_path)
            else:
                if fnmatchcase(filename, pattern):
                    all_files.append(full_path)
    
    if not all_files:
        return f"在 '{path}' 内没有找到匹配模式 '{pattern}' 的文件。"

    # 3. 智能排序 (移植自 glob.ts)
    now_timestamp_ms = datetime.now().timestamp() * 1000
    one_day_in_ms = 24 * 60 * 60 * 1000

    def get_mtime_ms(p):
        try:
            return os.path.getmtime(p) * 1000
        except OSError:
            return 0

    def sort_key(p):
        mtime_ms = get_mtime_ms(p)
        is_recent = (now_timestamp_ms - mtime_ms) < one_day_in_ms
        # 如果是最近的，按时间倒序；否则，按路径字母顺序
        return (not is_recent, -mtime_ms if is_recent else p.lower())

    sorted_paths = sorted(all_files, key=sort_key)
    
    # 转换为相对于 BASE_DIR 的路径
    relative_paths = [os.path.relpath(p, BASE_DIR) for p in sorted_paths]

    # 4. 构造丰富的返回信息
    file_count = len(relative_paths)
    file_list_description = "\n".join(relative_paths)
    
    result_message = (
        f"Found {file_count} file(s) matching '{pattern}'"
        f" within '{path}'"
        f", sorted by modification time (newest first):\n"
        f"{file_list_description}"
    )

    return result_message
