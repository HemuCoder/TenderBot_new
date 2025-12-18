from fastapi import FastAPI
from fastapi.responses import StreamingResponse, HTMLResponse
from pydantic import BaseModel
import os

from tender_analysis.orchestrator import event_generator

# ----------------- FastAPI 应用定义 -----------------

app = FastAPI(
    title="Tender Analysis Service",
    description="一个用于执行标书分析流水线并通过 SSE 推送事件的服务。",
)

class AnalysisRequest(BaseModel):
    """定义 API 请求体的数据模型。"""
    docx_path: str
    model: str
    language: str
    stream_token_deltas: bool = True # 增加配置开关，默认为 True

# ----------------- API 路由 -----------------

@app.post("/api/tender_analysis/stream")
async def stream_analysis(payload: AnalysisRequest):
    """
    接收分析请求，调用编排器，并以 SSE 流式返回事件。
    """
    return StreamingResponse(
        event_generator(
            docx_path=payload.docx_path,
            model_name=payload.model,
            language=payload.language,
            stream_token_deltas=payload.stream_token_deltas 
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

@app.get("/", response_class=HTMLResponse)
async def serve_debug_page():
    """
    提供前端调试页面。
    """
    # 动态地确定 HTML 文件的路径
    current_dir = os.path.dirname(os.path.abspath(__file__))
    html_file_path = os.path.join(current_dir, "tender_analysis_debug.html")
    
    try:
        with open(html_file_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return "<h1>错误：tender_analysis_debug.html 未找到。</h1>"

# ----------------- Uvicorn 启动入口 (可选) -----------------

if __name__ == "__main__":
    import uvicorn
    # 允许从命令行直接运行此文件进行测试
    # 在生产环境中，建议使用 gunicorn + uvicorn worker
    # 端口8002: 避免与MCP服务(8000)和catalog服务(8001)冲突
    uvicorn.run(app, host="0.0.0.0", port=8002)
