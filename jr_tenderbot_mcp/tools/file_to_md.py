import time
from pathlib import Path
from typing import Optional, List, Annotated
from pydantic import Field
from utils.file_util import get_safe_path, is_url, get_runtime_subdir, download_to, BASE_DIR
from docling.document_converter import DocumentConverter
import os

def file_to_md_impl(
    path: Annotated[str, Field(description="要转换为 Markdown 的文件的本地路径或 URL。")]
) -> str:
    """
    将指定路径（本地文件或URL）的文档内容转换为格式清晰的 Markdown 文本。
    支持多种文件格式，如 PDF, DOCX, HTML 等，并能自动处理表格和图片。
    """
    try:
        source_path = _get_source_path(path)
        markdown_content = _convert_to_markdown_content(source_path)
        cleaned_markdown = _clean_markdown_table(markdown_content)
        return cleaned_markdown
    except (FileNotFoundError, ConnectionError, ValueError) as e:
        return f"错误: {e}"
    except Exception as e:
        return f"处理文件时发生未知错误: {e}"

def _get_source_path(source: str) -> Path:
    """处理输入源，如果是 URL 则下载，如果是本地路径则验证，确保所有操作都在沙箱内。"""
    if is_url(source):
        # 所有下载内容都必须存放在 BASE_DIR 内的 runtime/downloads 子目录中
        downloads_dir = get_runtime_subdir("downloads")
        try:
            return download_to(downloads_dir, source)
        except ConnectionError as e:
            raise ConnectionError(f"无法从 URL 下载文件: {e}")
    else:
        # 对本地文件路径执行严格的安全检查
        safe_path_str = get_safe_path(source)
        safe_path = Path(safe_path_str)
        if not safe_path.exists():
            raise FileNotFoundError(f"指定的本地文件不存在: '{source}'")
        if not safe_path.is_file():
            raise ValueError(f"指定的路径不是一个文件: '{source}'")
        return safe_path

def _convert_to_markdown_content(src_path: Path) -> str:
    """使用 docling 将文件转换为 Markdown 内容。"""
    converter = DocumentConverter()
    
    try:
        result = converter.convert(str(src_path))
        doc = result.document
        markdown_content = doc.export_to_markdown(image_mode="referenced")
    finally:
        os.chdir(Path(BASE_DIR).parent.resolve()) # 无论成功与否，都切回原工作目录
        
    return markdown_content

def _clean_markdown_table(md_content: str) -> str:
    """清理和规范化 Markdown 表格，确保格式统一。"""
    lines = md_content.split('\n')
    output_lines = []
    in_table = False
    table_lines = []

    for line in lines:
        is_table_line = line.strip().startswith('|') and line.strip().endswith('|')

        if is_table_line:
            if not in_table:
                in_table = True
            table_lines.append(line)
        else:
            if in_table:
                output_lines.extend(_process_table(table_lines))
                table_lines = []
                in_table = False
            output_lines.append(line)

    if in_table:
        output_lines.extend(_process_table(table_lines))

    return '\n'.join(output_lines)

def _process_table(table_lines: List[str]) -> List[str]:
    """处理单个 Markdown 表格的内部逻辑。"""
    if len(table_lines) < 2:
        return table_lines

    # 规范化表格数据，确保每行都有相同的列数
    table_data = [[cell.strip() for cell in line.strip().strip('|').split('|')] for line in table_lines]
    num_cols = max(len(row) for row in table_data) if table_data else 0
    if num_cols == 0: return []

    for i, row in enumerate(table_data):
        if len(row) != num_cols:
            table_data[i].extend([''] * (num_cols - len(row)))

    # 分析对齐方式
    separator_row = table_data[1]
    alignments = []
    for cell in separator_row:
        left = cell.startswith(':')
        right = cell.endswith(':')
        if left and right:
            alignments.append('center')
        elif left:
            alignments.append('left')
        elif right:
            alignments.append('right')
        else:
            alignments.append('left') # 默认为左对齐

    # 构建处理后的表格
    processed_lines = []
    for i, row in enumerate(table_data):
        if i == 1:
            # 生成标准的分隔符行
            separator_parts = [':---:' if align == 'center' else '---:' if align == 'right' else ':---' for align in alignments]
            processed_lines.append('|' + '|'.join(separator_parts) + '|')
        else:
            processed_lines.append('| ' + ' | '.join(row) + ' |')

    return processed_lines


