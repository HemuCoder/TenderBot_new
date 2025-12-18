# 模块：智能标书解析 (`tender_analysis`)

## 1. 功能概览

本模块是一个基于大型语言模型（LLM）和多 Agent 协作的智能标书解析服务。它能够接收一份 `.docx` 格式的招标文件，通过一个包含七大阶段的复杂流水线，对其进行深度分析、提取和整合，最终生成一系列结构化的分析报告和一份以“满分”为导向的、可执行的投标行动清单。

整个过程通过一个 FastAPI 服务对外暴露，并以 Server-Sent Events (SSE) 的形式，实时地向客户端推送进度，提供了极佳的实时反馈和透明度。

## 2. 核心架构

本模块采用现代化的微服务与 Agent 架构，主要由以下三部分组成：

-   **FastAPI Web 服务 (`main.py`)**: 作为模块的总入口，负责接收 HTTP 请求，并管理整个解析流程的生命周期。
-   **核心编排器 (`orchestrator.py`)**: 整个流水线的“总指挥部”，负责定义和执行七大分析阶段，调度和协调下属的各个 Agent。
-   **Agent 工厂 (`analysis_agents.py`)**: 定义了执行各项具体任务（如商务提取、技术提取、清单生成等）的专家 Agent。
-   **主控程序 (MCP) 服务**: 作为一个独立的外部服务，为本模块提供底层的、可靠的文件操作（读/写）能力。本模块通过 `fastmcp.Client` 与之进行交互。

## 3. 安装与配置

在运行本模块前，请确保已完成以下步骤：

### 3.1. 安装依赖

本模块的所有 Python 依赖，都已在项目根目录的 `requirements.txt` 中列出。请通过以下命令进行安装：

```bash
pip install -r requirements.txt
```

### 3.2. 配置环境变量

本模块通过一个 `.env` 文件来管理所有敏感配置。请在 `tender_analysis` 目录下，创建一个名为 `.env` 的文件，并参照以下格式，填入您的配置：

```dotenv
# .env

# [必填] 您的大语言模型 API Key
OPENAI_API_KEY="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# [必填] 您的 API 代理地址或服务商提供的 Base URL
# 例如：https://api.openai.com/v1 或 https://vip.dmxapi.com
OPENAI_API_BASE="YOUR_API_BASE_URL"

# [必填] 正在运行的 MCP 服务地址
MCP_SERVER_URL="http://127.0.0.1:8123"
```

**重要提示**: 请确保 `.env` 文件与 `core/config.py` 文件位于同一目录下，或在项目根目录，以便 `pydantic-settings` 能够正确加载。

## 4. 如何运行

### 4.1. 启动 MCP 服务

在启动本模块前，**必须**确保 MCP 服务已在后台正常运行，因为本模块的所有文件操作，都强依赖于它。

### 4.2. 启动 FastAPI 服务

完成上述配置后，您可以通过两种方式启动本模块的 FastAPI 服务：

**A 开发模式 (推荐)**

在项目根目录下，运行 `main.py` 文件中定义的 `uvicorn` 服务器，它将开启热重载功能：

```bash
uvicorn tender_analysis.main:app --reload
```

**B 直接运行**

直接运行 `main.py` 脚本：

```bash
python tender_analysis/main.py
```

服务成功启动后，您将在终端看到类似如下的输出：

```
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

此时，您可以访问 `http://127.0.0.1:8000` 来查看本模块自带的前端调试页面。

## 5. API 接口详解

本模块提供了一个核心的 API 接口，用于触发整个解析流水线。

-   **接口路径**: `/api/tender_analysis/stream`
-   **请求方式**: `POST`
-   **请求体 (Body)**: `application/json`

### 请求参数

| 参数名              | 类型    | 是否必填 | 默认值                               | 说明                                                                                              |
| ------------------- | ------- | -------- | ------------------------------------ | ------------------------------------------------------------------------------------------------- |
| `docx_path`         | `string`  | 否       | 指向一份默认的测试标书               | 要解析的 `.docx` 文件在**服务器上**的**绝对路径**。                                                 |
| `model`             | `string`  | 否       | `"gpt-4.1-mini"`                       | 用于执行所有 Agent 任务的 LLM 模型名称。                                                            |
| `language`          | `string`  | 否       | `"zh"`                                 | Agent 输出内容的语言。                                                                              |
| `stream_token_deltas` | `boolean` | 否       | `true`                               | **核心开关**：控制分析阶段（2-5）的执行模式。`true` 为串行，`false` 为并行（但会直播一个随机任务）。 |

### 请求示例 (使用 `curl`)

```bash
curl -X POST http://127.0.0.1:8000/api/tender_analysis/stream \
-H "Content-Type: application/json" \
-d '{
    "docx_path": "/path/to/your/tender_document.docx",
    "model": "gpt-4-turbo",
    "stream_token_deltas": false
}'
```

### 响应

该接口将以 **Server-Sent Events (SSE)** 的形式，返回一个**事件流**。客户端需要通过监听此流，来实时地获取整个解析过程的状态更新。关于所有事件的类型和数据结构，请参考前端调试页面 (`tender_analysis_debug.html`) 中的 JavaScript 代码。
