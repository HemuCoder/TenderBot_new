import os
from typing import Annotated
from pydantic import Field
from utils.file_util import get_safe_path

def delete_file_impl(path: Annotated[str, Field(description="要删除的文件的路径。")]) -> str:
    """删除指定的文件。"""
    safe_path = get_safe_path(path)
    try:
        if os.path.exists(safe_path):
            os.remove(safe_path)
            return f"文件已成功删除: {path}"
        else:
            return f"文件不存在，无需删除: {path}"
    except Exception as e:
        return f"删除文件时发生错误: {e}"
