import os
from datetime import datetime
from typing import Annotated
from pydantic import Field
from utils.file_util import get_safe_path, BASE_DIR

def list_files_impl(
    path: Annotated[str, Field(description="要列出内容的目录路径，默认为根目录 '.'")] = ".", 
    recursive: Annotated[bool, Field(description="可选：是否递归地列出所有子目录的内容，默认为 False。")] = False
) -> str:
    """详细地列出指定路径下的文件和目录，返回一个类似于 'ls -l' 的格式化字符串。"""
    try:
        safe_path = get_safe_path(path)
        if not os.path.isdir(safe_path):
            return f"错误: 路径 '{path}' 不是一个有效的目录。"
    except ValueError as e:
        return f"错误: 无效的路径: {e}"

    def get_dir_entries(current_path):
        entries = []
        for entry_name in os.listdir(current_path):
            full_path = os.path.join(current_path, entry_name)
            try:
                stat = os.stat(full_path)
                is_dir = os.path.isdir(full_path)
                
                # 人类可读的文件大小
                size = stat.st_size
                if size < 1024:
                    size_str = f"{size} B"
                elif size < 1024 * 1024:
                    size_str = f"{size / 1024:.1f} KB"
                else:
                    size_str = f"{size / (1024 * 1024):.1f} MB"

                entries.append({
                    "name": entry_name,
                    "is_dir": is_dir,
                    "size": size_str if not is_dir else "",
                    "modified_time": datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M')
                })
            except OSError:
                continue # 忽略无法访问的文件/目录
        
        # 排序：目录在前，文件在后，然后按名称排序
        entries.sort(key=lambda e: (not e['is_dir'], e['name']))
        return entries

    def format_entries(entries, current_rel_path):
        lines = [f"目录列表: ./{current_rel_path}\n"]
        if not entries:
            return f"目录 './{current_rel_path}' 为空。\n"
            
        # 找到最长的尺寸字符串长度用于对齐
        max_size_len = 0
        if any(not e['is_dir'] for e in entries):
            max_size_len = max(len(e['size']) for e in entries if not e['is_dir'])

        for entry in entries:
            entry_type = "[DIR] " if entry['is_dir'] else "[FILE]"
            size_padding = " " * (max_size_len - len(entry['size'])) if not entry['is_dir'] else " " * max_size_len
            
            lines.append(
                f"{entry_type} {entry['modified_time']}  {size_padding}{entry['size']:>5}  {entry['name']}"
            )
        return "\n".join(lines) + "\n"

    output = []
    
    if recursive:
        for root, dirs, _ in os.walk(safe_path):
            # os.walk 默认先处理顶层目录
            relative_root = os.path.relpath(root, BASE_DIR)
            if relative_root == ".": relative_root = ""
            
            entries = get_dir_entries(root)
            output.append(format_entries(entries, relative_root))
            
            # 确保子目录排序与 get_dir_entries 一致
            dirs.sort()
    else:
        relative_path = os.path.relpath(safe_path, BASE_DIR)
        if relative_path == ".": relative_path = ""
        entries = get_dir_entries(safe_path)
        output.append(format_entries(entries, relative_path))

    return "\n".join(output)
