from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import find_dotenv, load_dotenv

# --- 使用更稳健的方式加载 .env 文件 ---
# find_dotenv (无参数) 会主动从当前文件位置向上搜索项目目录树，确保 .env 文件能被找到
dotenv_path = find_dotenv()
if not dotenv_path:
    print("警告：在项目目录中未找到 .env 文件。将依赖于系统环境变量。")
else:
    load_dotenv(dotenv_path=dotenv_path)


class Settings(BaseSettings):
    """
    集中管理 tender_analysis 模块的所有配置。
    配置项将自动从环境变量中加载（由上面的 load_dotenv 填充）。
    """
    # OpenAI / Agents SDK
    OPENAI_API_KEY: str
    OPENAI_API_BASE: str | None = None # <-- 核心改动：使用 LiteLLM 的标准环境变量名

    # MCP Server
    MCP_SERVER_URL: str

    # model_config 不再直接查找 .env 文件，而是依赖于已加载的环境变量
    model_config = SettingsConfigDict(env_file=None, extra='ignore')

# 创建一个全局的 settings 实例，方便其他模块直接导入使用
# 如果 .env 文件缺失或内容不全，这里会抛出明确的 ValidationError
settings = Settings()
