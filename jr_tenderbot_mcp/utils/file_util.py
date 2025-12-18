import os
import re
import socket
import ipaddress
import urllib.parse
from pathlib import Path
import httpx

BASE_DIR = "mcp-file"

def get_runtime_subdir(name: str) -> Path:
    """在 BASE_DIR 下创建一个带时间戳的子目录，用于存放运行时文件"""
    runtime_dir = Path(BASE_DIR) / "runtime" / name
    runtime_dir.mkdir(parents=True, exist_ok=True)
    return runtime_dir

def is_url(text: str) -> bool:
    """检查字符串是否为有效的URL"""
    try:
        result = urllib.parse.urlparse(text)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False

def safe_filename(name: str) -> str:
    """清理并返回一个安全的文件名"""
    # 移除URL中的协议和域名部分，只保留路径
    if is_url(name):
        parsed = urllib.parse.urlparse(name)
        name = parsed.path
    # 移除可能导致路径问题的字符
    return re.sub(r'[\\/*?:"<>|]', "", name).lstrip('/')

def download_to(directory: Path, url: str) -> Path:
    """从URL下载文件并保存到指定目录"""
    filename = safe_filename(url)
    if not filename:
        filename = "downloaded_file"
    
    filepath = directory / filename
    
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=30) as response:
            response.raise_for_status()
            with open(filepath, "wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)
        return filepath
    except httpx.RequestError as e:
        raise ConnectionError(f"下载文件时出错: {e}")

def get_safe_path(path: str) -> str:
    """获取安全的文件路径，防止目录遍历"""
    # 仅在路径不是URL时应用安全路径检查
    if not is_url(path):
        full_path = os.path.join(BASE_DIR, path)
        safe_path = os.path.abspath(full_path)
        if not safe_path.startswith(os.path.abspath(BASE_DIR)):
            raise ValueError("不允许访问基础目录之外的路径")
        return safe_path
    return path

def is_private_ip(hostname: str) -> bool:
    """检查给定的主机名是否解析为私有IP地址。"""
    try:
        ip_addr = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(ip_addr)
        return ip.is_private
    except (socket.gaierror, ValueError):
        # 如果无法解析主机名或不是有效的IP地址，则假定为非私有
        return False

def convert_to_raw_github_url(url: str) -> str:
    """如果URL是GitHub blob链接，则将其转换为raw.githubusercontent.com链接。"""
    if "github.com" in url and "/blob/" in url:
        return url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
    return url

def calculate_flexible_replacement(current_content: str, old_string: str, new_string: str):
    source_lines = current_content.splitlines(True)
    search_lines = old_string.splitlines()
    replace_lines = new_string.splitlines()

    if not search_lines:
        return None, 0

    search_lines_stripped = [line.strip() for line in search_lines]
    
    match_indices = []
    for i in range(len(source_lines) - len(search_lines) + 1):
        window = source_lines[i : i + len(search_lines)]
        window_stripped = [line.strip() for line in window]
        if window_stripped == search_lines_stripped:
            match_indices.append(i)

    if len(match_indices) != 1:
        return None, len(match_indices)

    match_start_index = match_indices[0]
    
    first_line_in_match = source_lines[match_start_index]
    indentation_match = re.match(r'^(\s*)', first_line_in_match)
    indentation = indentation_match.group(1) if indentation_match else ""
    
    # an empty line in replace_lines should not carry indentation
    new_block_with_indent = [
        f"{indentation}{line}" if line else "" for line in replace_lines
    ]

    # Reconstruct the file content
    new_content_lines = (
        source_lines[:match_start_index] +
        [line + '\n' for line in new_block_with_indent[:-1]] + 
        [new_block_with_indent[-1]] +
        source_lines[match_start_index + len(search_lines):]
    )

    # Ensure the last line has a newline if it's not empty
    if new_block_with_indent and new_block_with_indent[-1]:
         # if the original file had a trailing newline, preserve it.
        if source_lines[-1].endswith('\n') and not new_content_lines[-1].endswith('\n'):
            new_content_lines[-1] += '\n'

    return "".join(new_content_lines), 1
