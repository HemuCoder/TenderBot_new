import re
import asyncio
import urllib.parse
from typing import Annotated
from pydantic import Field
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from utils.file_util import convert_to_raw_github_url, is_private_ip

async def _web_fetch_logic(url: str, prompt: str = "") -> str:
    """
    web_fetch 工具的核心实现逻辑。
    """
    from playwright.async_api import async_playwright

    # 优先从 prompt 中提取 URL
    if prompt:
        url_match = re.search(r'https?://[^\s]+', prompt)
        if url_match:
            url = url_match.group(0)
        else:
            return "错误: 在 'prompt' 中未找到有效的URL。"

    if not url or not (url.startswith("http://") or url.startswith("https://")):
        return "错误: 'url' 参数必须是一个有效的 HTTP/HTTPS 链接。"

    # 转换 GitHub URL
    url = convert_to_raw_github_url(url)

    # 检查是否为私有IP
    try:
        hostname = urllib.parse.urlparse(url).hostname
        if hostname and is_private_ip(hostname):
            return f"错误:出于安全原因，不允许访问私有IP地址 ({hostname})。"
    except Exception as e:
        return f"错误: 验证URL主机名时出错: {e}"

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            except Exception as e:
                await browser.close()
                return f"错误: 访问链接 {url} 失败: {type(e).__name__}。WEB_FETCH_FALLBACK_FAILED"

            from readability import Document

            # 1. 优先使用 readability 提取核心可读内容 (首选策略)
            html_content = await page.content()
            doc = Document(html_content)
            readable_html = doc.summary() # 这是包含文章主要内容的HTML字符串
            html_has_headings = re.search(r'<h[1-6]', readable_html, re.IGNORECASE)

            # 2. 如果 readability 失败(内容过短或缺少标题)，则降级到清理整个 body
            if not readable_html or len(readable_html) < 200 or not html_has_headings:
                soup = BeautifulSoup(html_content, "html.parser")
                target_node = soup.body
                if not target_node:
                    await browser.close()
                    return f"无法从 {url} 提取 body 内容。"
                # 深度清理 body
                for tag in target_node(["script", "style", "img", "nav", "header", "footer", "aside", "form"]):
                    tag.decompose()
                cleaned_html = str(target_node)
            else:
                # 3. 如果 readability 成功，则在其基础上进行二次清理
                soup = BeautifulSoup(readable_html, "html.parser")
                # 清理剩余的噪音
                for tag in soup(["script", "style", "img", "form"]):
                    tag.decompose()
                cleaned_html = str(soup)
            
            # 4. 将清理后的 HTML 转换为 Markdown
            markdown_content = md(cleaned_html, heading_style='ATX').strip()

            # 5. 对 Markdown 文本进行后处理，优化格式
            # 移除所有指向 javascript:; 的链接，但保留链接文本
            markdown_content = re.sub(r'\[(.*?)\]\(javascript:;\)', r'\1', markdown_content)
            # 移除空的 Markdown 链接
            markdown_content = re.sub(r'\[\]\((.*?)\)', '', markdown_content)
            # 移除只包含'#'或'/'或'##'等符号的链接
            markdown_content = re.sub(r'\[[#\s/]*?\]\((.*?)\)', '', markdown_content)
            # 将三个或更多的换行符合并为最多两个
            markdown_content = re.sub(r'\n{3,}', '\n\n', markdown_content)
            # 移除行首行尾的空白字符
            lines = [line.strip() for line in markdown_content.split('\n')]
            # 移除空行和只包含少量非字母数字字符的行
            lines = [line for line in lines if line and re.search(r'[a-zA-Z0-9]', line)]
            markdown_content = '\n'.join(lines)


            await browser.close()
            
            if not markdown_content:
                return f"无法从 {url} 提取任何文本内容。"
            
            return markdown_content

    except Exception as e:
        error_message = f"执行 web_fetch 时发生错误: {type(e).__name__} - {e}。WEB_FETCH_PROCESSING_ERROR"
        # 移除 print 语句，让测试输出更干净
        return error_message

async def web_fetch_tool_logic(url: str, prompt: str = "") -> str:
    """
    web_fetch 工具的核心并发和格式化逻辑。
    """
    # 优先从 prompt 中提取所有 URL，否则使用 url 参数
    text_to_scan = prompt if prompt else url
    # 改进正则表达式，通过白名单字符精确匹配 URL，避免将后续文本错误包含进来
    urls = re.findall(r'https?://[a-zA-Z0-9\-._~:/?#[\]@!$&\'()*+,;=%]+', text_to_scan)

    if not urls:
        # 如果 prompt 和 url 参数都没有提供，则返回错误
        if not url and not prompt:
             return "错误: 必须提供 'url' 或 'prompt' 参数之一。"
        # 如果在 prompt 中找不到 URL，但 url 参数存在，则使用 url 参数
        if url:
            urls = [url]
        else:
            return "错误: 未在输入中找到任何有效的 URL。"

    # 并发执行所有 URL 的抓取
    tasks = [_web_fetch_logic(u) for u in urls]
    results = await asyncio.gather(*tasks)

    # 格式化并合并所有结果
    if len(results) == 1:
        return results[0]
    
    output = []
    for i, result in enumerate(results):
        output.append(f"--- 链接 {i+1} ({urls[i]}) 的内容 ---\n\n{result}")
    
    return "\n\n".join(output)

async def web_fetch_impl(
    url: Annotated[str, Field(description="要访问的单个网页链接。如果提供了 prompt，则此参数将被忽略。")] = "", 
    prompt: Annotated[str, Field(description="可选：包含一个或多个 URL 的文本提示。将从中提取所有 URL 并发处理。格式应为：'一些文本 [\"url1\", \"url2\"]'。")] = ""
) -> str:
    """使用 Playwright 并发访问一个或多个 URL，并智能提取其主要文本内容，以 Markdown 格式返回。"""
    return await web_fetch_tool_logic(url=url, prompt=prompt)
