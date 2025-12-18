import os
import inspect
import importlib
from fastmcp import FastMCP
from fastmcp.tools import Tool

mcp = FastMCP("Tender Tool Server")

# ========================================
# 路径配置:使用脚本所在目录的绝对路径
# ========================================
# 获取当前脚本文件的目录(无论从哪里运行都正确)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = SCRIPT_DIR  # MCP工作目录
TOOLS_DIR = os.path.join(SCRIPT_DIR, "tools")  # 工具目录

# 确保基础目录存在
if not os.path.exists(BASE_DIR):
    os.makedirs(BASE_DIR)

def register_tools_from_directory(mcp_instance: FastMCP):
    """
    自动扫描tools目录,动态导入并注册所有工具。
    """
    if not os.path.exists(TOOLS_DIR):
        print(f"错误: tools目录不存在: {TOOLS_DIR}")
        return
    
    for filename in os.listdir(TOOLS_DIR):
        if filename.endswith(".py") and not filename.startswith("__"):
            module_name = f"tools.{filename[:-3]}"
            tool_name = filename[:-3]

            try:
                module = importlib.import_module(module_name)
                func_to_register = None

                for name, func in inspect.getmembers(module, inspect.isfunction):
                    if name.endswith("_impl"):
                        func_to_register = func
                        break
                
                if func_to_register:
                    description = inspect.getdoc(func_to_register)
                    if not description:
                        print(f"警告: 工具 '{tool_name}' 的实现函数缺少文档字符串,已跳过。")
                        continue

                    base_tool = mcp_instance.tool(func_to_register)

                    transformed_tool = Tool.from_tool(
                        tool=base_tool,
                        name=tool_name,
                        description=description
                    )
                    
                    mcp_instance.add_tool(transformed_tool)

                    base_tool.disable()
                else:
                    print(f"警告: 在模块 {module_name} 中未找到有效的工具实现函数。")

            except ImportError as e:
                print(f"无法导入模块 {module_name}: {e}")

# 自动注册工具
register_tools_from_directory(mcp)

if __name__ == "__main__":
    mcp.run()