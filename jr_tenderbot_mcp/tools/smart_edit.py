import os
from typing import Annotated
from pydantic import Field
from utils.file_util import get_safe_path, calculate_flexible_replacement

def smart_edit_impl(
    file_path: Annotated[str, Field(description="要编辑或创建的文件的路径。")], 
    old_string: Annotated[str, Field(description="需要被替换的、包含多行上下文的精确文本块。创建新文件时，此项必须为空字符串 ''。")], 
    new_string: Annotated[str, Field(description="用于替换 old_string 的新文本块。")]
) -> str:
    """智能编辑或创建文件。它需要精确的上下文来定位和替换文本。"""
    safe_path = get_safe_path(file_path)
    file_exists = os.path.exists(safe_path)

    # 场景 1: 创建新文件
    if old_string == "":
        if file_exists:
            return "错误: 文件已存在，无法创建。如果想编辑，old_string 不能为空。"
        try:
            with open(safe_path, "w", encoding="utf-8", newline='') as f:
                f.write(new_string)
            return f"文件已成功创建: {file_path}"
        except Exception as e:
            return f"创建文件时发生错误: {e}"

    # 场景 2: 编辑已存在文件
    if not file_exists:
        return f"错误: 文件未找到 {file_path}。如果想创建，old_string 必须为空。"

    with open(safe_path, "r", encoding="utf-8", newline='') as f:
        current_content = f.read()

    # 策略 1: 精确匹配
    occurrences = current_content.count(old_string)
    if occurrences == 1:
        new_content = current_content.replace(old_string, new_string, 1)
        with open(safe_path, "w", encoding="utf-8", newline='') as f:
            f.write(new_content)
        return f"文件已通过精确匹配成功编辑: {file_path}"

    # 策略 2: 灵活匹配 (当精确匹配不唯一时)
    new_content, flex_occurrences = calculate_flexible_replacement(current_content, old_string, new_string)
    
    if flex_occurrences == 1 and new_content is not None:
        with open(safe_path, "w", encoding="utf-8", newline='') as f:
            f.write(new_content)
        return f"文件已通过灵活匹配成功编辑: {file_path}"
    
    # 根据最接近的匹配策略报告错误
    if flex_occurrences > 1:
        return f"错误: 灵活匹配找到 {flex_occurrences} 处匹配项，无法明确编辑。请提供更独特的 old_string。"

    if occurrences > 1:
        return f"错误: 精确匹配找到 {occurrences} 处匹配项，无法明确编辑。请提供更独特的 old_string。"
    
    return "错误: 未找到任何匹配项 (无论是精确匹配还是灵活匹配)。请使用 read_file 检查文件内容。"
