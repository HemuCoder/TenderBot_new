import os
from typing import Annotated
from pydantic import Field
from utils.file_util import get_safe_path

def make_directory_impl(path: Annotated[str, Field(description="要创建的目录的路径。")]) -> str:
    """创建一个新的（可能是嵌套的）目录。"""
    safe_path = get_safe_path(path)
    try:
        os.makedirs(safe_path, exist_ok=True)
        return f"目录已成功创建或已存在: {path}"
    except Exception as e:
        return f"创建目录时发生错误: {e}"
