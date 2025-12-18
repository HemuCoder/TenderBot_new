# -*- coding: utf-8 -*-
"""
@File    : settings.py
@Description: This file contains the configuration for the catalog generation module.
@Author  : <<your name>>
@Date    : <<date>>
@Version : 1.0
"""

import os

# Input: None
# Output: Configuration variables for the catalog generation process.

# ==============================================================================
# 全局配置区
# ==============================================================================

# 从环境变量加载敏感信息
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "sk-Zh0uHqONlPjxrJxEP1vLtxrREDiSAkfiargKchTL4zJdz5jO")
MCP_SERVER_URL = "http://localhost:8000/mcp"

DEFAULT_MODEL_NAME = "gpt-4.1-mini"

# File Paths
_INPUT_BASE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "jr_tenderbot_mcp", "mcp-file")
_OUTPUT_DIR = os.path.join(_INPUT_BASE_DIR, "catalog_generation")

# 确保输出目录存在
os.makedirs(_OUTPUT_DIR, exist_ok=True)

# 定义输入和输出文件的标准路径
INPUT_PATHS = {
    "intermediate_chunks": os.path.join(_INPUT_BASE_DIR, "intermediate_chunks.json"),
    "final_checklist": os.path.join(_INPUT_BASE_DIR, "final_checklist.md"),
    "templates": os.path.join(_INPUT_BASE_DIR, "templates.json"),
    "reference_catalog": os.path.join(_INPUT_BASE_DIR, "example.md"),
}

OUTPUT_PATHS = {
    "format_framework": os.path.join(_OUTPUT_DIR, "format_framework.json"),
    "business_catalog_intermediate": os.path.join(_OUTPUT_DIR, "business_catalog_intermediate.json"),
    "business_catalog": os.path.join(_OUTPUT_DIR, "business_catalog_final.json"),
    "business_catalog_linked": os.path.join(_OUTPUT_DIR, "business_catalog_final_linked.json"),
    "pricing_catalog_linked": os.path.join(_OUTPUT_DIR, "pricing_catalog_linked.json"),
    "technical_catalog_standardized_md": os.path.join(_OUTPUT_DIR, "technical_catalog_standardized.md"),
    "technical_catalog": os.path.join(_OUTPUT_DIR, "technical_catalog_enriched.json"),
    "technical_catalog_linked": os.path.join(_OUTPUT_DIR, "technical_catalog_linked.json"),
    "templates": os.path.join(_OUTPUT_DIR, "templates_md.json"),
}
