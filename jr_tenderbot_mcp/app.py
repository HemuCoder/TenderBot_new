from server import mcp
import uvicorn

# 配置 http 应用，并指定路径为
app = mcp.http_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
