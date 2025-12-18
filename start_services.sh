#!/bin/bash
# ========================================
# TenderBot 服务启动脚本
# ========================================
# 用法: ./start_services.sh
# 停止: Ctrl+C 会停止所有服务
# ========================================

set -e  # 遇到错误立即退出

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  TenderBot 服务启动${NC}"
echo -e "${BLUE}========================================${NC}"

# 激活虚拟环境
echo -e "${YELLOW}[1/4] 激活虚拟环境...${NC}"
source /Users/cris/venv_all/tenderbot/bin/activate

# 启动MCP服务 (端口8000)
echo -e "${YELLOW}[2/4] 启动MCP文件服务 (端口8000)...${NC}"
cd jr_tenderbot_mcp
python app.py &
MCP_PID=$!
cd ..
sleep 2

# 启动标书解析服务 (端口8002)
echo -e "${YELLOW}[3/4] 启动标书解析服务 (端口8002)...${NC}"
python -m uvicorn tender_analysis.main:app --reload --port 8002 &
TENDER_PID=$!
sleep 2

# 启动目录生成服务 (端口8001)
echo -e "${YELLOW}[4/4] 启动目录生成服务 (端口8001)...${NC}"
python -m uvicorn catalog_generation.api:app --reload --port 8001 &
CATALOG_PID=$!
sleep 2

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  所有服务启动成功!${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "MCP服务:        ${BLUE}http://localhost:8000${NC}"
echo -e "标书解析:       ${BLUE}http://localhost:8002${NC}"
echo -e "目录生成:       ${BLUE}http://localhost:8001${NC}"
echo -e "${GREEN}========================================${NC}"
echo -e "${YELLOW}按 Ctrl+C 停止所有服务${NC}"

# 等待用户中断
wait

# 清理函数
cleanup() {
    echo -e "\n${YELLOW}正在停止所有服务...${NC}"
    kill $MCP_PID $TENDER_PID $CATALOG_PID 2>/dev/null || true
    echo -e "${GREEN}所有服务已停止${NC}"
    exit 0
}

trap cleanup SIGINT SIGTERM
