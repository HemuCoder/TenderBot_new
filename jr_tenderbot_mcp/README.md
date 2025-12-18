# LLM 驱动的本地文件操作代理

这是一个功能强大的本地代码助手，由大型语言模型 (LLM) 驱动，能够通过自然语言指令，智能地、安全地对本地文件和网页内容进行各种复杂操作。

## 核心功能

本项目通过一个健robust的 `server.py` 后端，提供了一系列经过严格测试的、行业级的本地文件系统和网页访问工具。前端的 `llm_agent.py` 负责与用户进行交互，并智能地调用这些工具来完成任务。

| 工具 (Tool) | 功能描述 | 核心亮点 |
| :--- | :--- | :--- |
| `list_files` | 详细地列出文件和目录 | 模仿 `ls -l` 的丰富输出, 支持递归 (`recursive`) |
| `read_file` | 读取文件内容 | 支持大文件分页 (`offset`, `limit`), 并提供清晰的截断提示 |
| `smart_edit` | 智能地编辑或创建文件 | 独有的“灵活匹配”能力, 能忽略缩进差异, 自动保留代码格式 |
| `grep` | 在文件中搜索内容 | 支持正则表达式, 默认大小写不敏感 |
| `glob_tool` | 查找匹配特定模式的文件 | 智能排序 (优先显示最近修改的文件) |
| `make_directory` | 创建目录 | 支持递归创建嵌套目录 |
| `delete_file` | 删除文件 | - |
| `web_search` | 执行互联网搜索 | 调用 Playwright 驱动真实浏览器，能绕过简单的反爬虫机制 |
| `web_fetch` | 获取网页核心内容 | 独有的“智能提取”算法，能自动去噪并以 Markdown 格式返回正文 |

## 技术栈

-   **后端**: Python 3.10+
-   **工具服务器**: FastMCP
-   **LLM 交互**: OpenAI / DMXAPI (通过 `httpx` 进行异步调用)
-   **网页自动化**: Playwright
-   **网页内容提取**: BeautifulSoup4, Readability
-   **异步框架**: `asyncio`
-   **核心库**: `glob2`, `pydantic`

## 快速开始

### 1. 安装依赖

请确保您已安装 Python 3.10 或更高版本，并建议使用虚拟环境。

```bash
# 创建并激活虚拟环境 (可选，但推荐)
python3 -m venv venv
source venv/bin/activate

# 安装所有必需的 Python 库
pip install -r requirements.txt

# 安装 Playwright 所需的浏览器驱动
playwright install --with-deps
```

### 2. 配置 API 密钥

打开 `llm_agent.py` 文件，在第 9 行找到以下代码，并将其替换为您的 API 密钥：

```python
# llm_agent.py Line 9
DMX_AUTH_key = "sk-..."
```

### 3. 启动服务

您需要**同时运行**两个独立的进程。

**在第一个终端中，启动后端工具服务器：**

```bash
fastmcp run server:mcp --port 8000

# 服务器部署
uvicorn app:app --host 0.0.0.0 --port 8000
```

**在第二个终端中 (确保已激活虚拟环境)，启动 LLM 代理并开始对话：**

```bash
python llm_agent.py
```

### 4. 开始使用

现在，您可以直接在第二个终端的提示符 `>` 后面输入自然语言指令了。

## 开发者指南: 添加新工具

本项目的工具注册机制是完全自动化的。要添加一个新的工具，您只需遵循以下三个简单的约定即可：

1.  **创建工具文件**: 在 `tools/` 目录下创建一个新的 Python 文件。该文件的名称将自动成为工具的 `tool_name` (例如, `my_tool.py` 会被注册为 `my_tool`)。

2.  **实现工具函数**: 在新文件中，定义一个函数，其名称必须以 `_impl` 结尾 (例如, `def my_tool_impl(...)`)。这个函数就是工具的具体实现逻辑。

3.  **编写文档字符串**: 为该函数编写一个清晰、详细的文档字符串 (docstring)。这个文档字符串将自动被提取为工具的 `description`，供 LLM 理解其功能和用法。

完成以上步骤后，`server.py` 在下次启动时会自动发现并注册您的新工具，无需任何额外的手动配置。

## 使用示例

> **基础操作**
>
> `帮我看看当前目录下有哪些文件`
>
> (代理会调用 `list_files` 并返回一个详细的列表)

> **多步文件编辑**
>
> `用 grep 搜索一下 'hello'`
>
> (代理返回包含 'hello' 的文件列表)
>
> `好的，读取 welcome.txt`
>
> (代理返回文件内容)
>
> `帮我把里面的 'World' 改成 'Developer'`
>
> (代理会精确地执行替换操作)

> **网页交互**
>
> `今天AI领域有什么最新进展？`
>
> (代理会调用 `web_search` 搜索并返回结果)
>
> `帮我总结一下 https://www.jiqizhixin.com/articles/2023-05-22-2 这篇文章说了什么`
>
> (代理会调用 `web_fetch` 提取并总结文章核心内容)

> **复杂任务**
>
> `在 mcp-file 目录下创建一个名为 'docs' 的新目录，然后在里面创建一个 'readme.md' 文件，内容是 '# New Project'`

