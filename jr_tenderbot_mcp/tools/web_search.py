import re
import asyncio
import urllib.parse
from typing import Annotated
from pydantic import Field

async def _get_real_url(page, url):
    """访问百度中转链接并返回跳转后的真实URL"""
    if not url or "baidu.com/link?url=" not in url:
        return url
    try:
        # 修正：将协程包装成Task
        await asyncio.wait([
            asyncio.create_task(page.goto(url)),
            asyncio.create_task(page.wait_for_event('domcontentloaded'))
        ], return_when=asyncio.FIRST_COMPLETED)
        
        real_url = page.url
        if "baidu.com" in real_url and "wd=" in real_url:
            return url
        return real_url
    except Exception:
        return url

def _decode_title_from_url(url: str) -> str:
    """尝试从百度中转链接的查询参数中解码出完整标题。"""
    try:
        parsed_url = urllib.parse.urlparse(url)
        query_params = urllib.parse.parse_qs(parsed_url.query)
        # 百度可能会用 'title' 或 'wd' 等参数存放标题
        if 'title' in query_params:
            return query_params['title'][0]
        if 'wd' in query_params:
            return query_params['wd'][0]
    except Exception:
        return ""
    return ""

async def _parse_result(result_element, temp_page):
    """
    解析单个搜索结果元素，提取标题、链接、摘要和图片。
    """
    title, href, body, image_url = "", "", "", ""

    # 策略1: 匹配卡片式/特殊结果 (如 id="1")
    title_element = await result_element.query_selector("h3.cosc-title a")
    if title_element:
        title = await title_element.text_content()
        href = await title_element.get_attribute("href")
        body_element = await result_element.query_selector(".summary_22rnB > span")
        if body_element:
            body = await body_element.text_content()
    
    # 策略2: 匹配常规搜索结果 (如 id="2", id="3")
    if not title:
        title_element = await result_element.query_selector("h3[class^='t'] a")
        if title_element:
            title = await title_element.text_content()
            href = await title_element.get_attribute("href")
        body_element = await result_element.query_selector(".c-abstract, .summary-text_560AW")
        if body_element:
            body = await body_element.text_content()

    # 修正：直接从 <img> 标签的 src 属性获取图片链接
    image_element = await result_element.query_selector("img.cos-image-body, img._img_14uts_11")
    if image_element:
        image_url = await image_element.get_attribute("src")

    # 尝试从链接解码完整标题
    if href:
        decoded_title = _decode_title_from_url(href)
        if decoded_title:
            title = decoded_title

    # 如果通过特定选择器仍未找到内容，启用通用后备方案
    if not body:
        full_text = await result_element.text_content()
        body = re.sub(r'\s+', ' ', full_text).strip()

    # 清理摘要
    body = re.sub(r'展开剩余\d+%内容', '', body).strip()
    
    # 异步解析真实链接
    if href and "baidu.com/link" in href:
        href = await _get_real_url(temp_page, href)

    # 如果摘要仍然不足，并且有有效链接，则访问页面获取
    if (not body or len(body) < 20) and "http" in href:
        try:
            print(f"摘要不足，尝试访问链接获取: {href}")
            # 修正：将协程包装成Task
            await asyncio.wait([
                asyncio.create_task(temp_page.goto(href)),
                asyncio.create_task(temp_page.wait_for_event('domcontentloaded'))
            ], return_when=asyncio.FIRST_COMPLETED)

            # 等待页面出现有意义的内容
            await temp_page.wait_for_function("() => document.body && document.body.innerText.length > 50")

            p_elements = await temp_page.query_selector_all("p")
            page_text = " ".join([await p.text_content() for p in p_elements])
            
            cleaned_text = re.sub(r'\s+', ' ', page_text).strip()
            if len(cleaned_text) > len(body):
                body = cleaned_text

        except Exception as page_e:
            print(f"访问链接 {href} 失败: {type(page_e).__name__}")

    # 最后的回退逻辑
    if not title and body:
        title = body
    if not body:
        body = "无摘要"
        
    return {"title": title, "href": href, "body": body, "image_url": image_url}


async def web_search_impl(
    query: Annotated[str, Field(description="要搜索的关键词或问题。")],
    num_results: Annotated[int, Field(description="可选：希望返回的结果数量，默认为 10。")] = 10
) -> str:
    """使用 Playwright 驱动百度执行网页搜索，以获取包含标题、真实链接、摘要和图片的丰富信息。"""
    from playwright.async_api import async_playwright
    import asyncio

    if not query or not isinstance(query, str) or query.strip() == '':
        return "错误: 'query' 参数不能为空。"
    if not isinstance(num_results, int) or num_results <= 0:
        return "错误: 'num_results' 必须是一个正整数。"

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            temp_page = await browser.new_page() # 用于解析真实链接和摘要
            
            # 修正：将协程包装成Task
            await asyncio.wait([
                asyncio.create_task(page.goto(f"https://www.baidu.com/s?wd={query}")),
                asyncio.create_task(page.wait_for_event('domcontentloaded'))
            ], return_when=asyncio.FIRST_COMPLETED)
            
            # 等待第一个结果块出现即可，不必等所有内容
            results_selector = "div.result.c-container:not([tpl*='recommend_list'])"
            await page.wait_for_selector(results_selector)
            result_elements = await page.query_selector_all(results_selector)

            if not result_elements:
                await browser.close()
                return f"没有找到关于 '{query}' 的搜索结果。"

            tasks = []
            for result in result_elements[:num_results]:
                tasks.append(_parse_result(result, temp_page))
            
            parsed_results = await asyncio.gather(*tasks)

            formatted_results = []
            for res in parsed_results:
                if res['title'] != '无标题':
                    result_str = f"标题: {res['title']}\n链接: {res['href']}\n摘要: {res['body']}"
                    if res['image_url']:
                        result_str += f"\n图片链接: {res['image_url']}"
                    formatted_results.append(result_str)
            
            await browser.close()
            if not formatted_results:
                return f"没有找到关于 '{query}' 的有效搜索结果。"
            return "\n\n---\n\n".join(formatted_results)

    except Exception as e:
        error_message = f"执行 Playwright 网页搜索时发生错误: {type(e).__name__} - {e}"
        print(error_message)
        return error_message
