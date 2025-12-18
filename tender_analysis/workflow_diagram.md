# Tender Analysis 模块核心工作流

这是一份使用 Mermaid 语法绘制的序列图，旨在清晰、直观地展示 `tender_analysis` 模块从接收请求到最终交付的全过程。

```mermaid
sequenceDiagram
    participant C as Client (e.g., Browser)
    participant F as FastAPI (/api/agent/stream)
    participant O as Orchestrator (event_generator)
    participant M as MCP Server
    participant ExtractorAgents as [商务/技术/报价/评分 Agents]
    participant TemplateAgents as [标准/非标模版 Agents]
    participant ChecklistAgents as [大纲/富化 Agents]

    C->>F: POST (docx_path, model, stream_token_deltas)
    F->>O: 调用主流程

    box rgb(240, 248, 255) 阶段 1: 文档预处理
        O->>O: convert_docx_to_markdown(docx_path)
        O->>M: smart_edit(intermediate_full.md)
        O->>O: analyze_structure() & chunk_content()
        O->>M: smart_edit(intermediate_chunks.json)
        O-->>C: sse(phase_start), sse(note), sse(artifact), sse(phase_end)
    end

    alt stream_token_deltas is true (串行)
        box rgb(230, 255, 230) 阶段 2-5: 串行分析
            loop for each in [商务, 技术, 报价, 评分]
                O->>ExtractorAgents: run(分析请求)
                ExtractorAgents-->>O: 返回分析结果
                O->>M: smart_edit(相应 summary.md)
                O-->>C: sse(token_delta), sse(artifact)
            end
        end
    else stream_token_deltas is false (并行)
        box rgb(230, 255, 230) 阶段 2-5: 并行分析
            O->>O: 随机选择一个任务 (e.g., 技术) 作为直播
            par
                O->>ExtractorAgents: run("技术"分析请求)
                ExtractorAgents-->>O: 返回"技术"分析结果
                O->>M: smart_edit(technical_summary.md)
                O-->>C: sse(token_delta), sse(artifact)
            and
                O->>ExtractorAgents: 并行 run 其他分析
                ExtractorAgents-->>O: 返回其他分析结果
                O->>M: 并行 smart_edit 其他 summary.md
                O-->>C: sse(artifact)
            end
        end
    end

    box rgb(255, 250, 240) 阶段 6: 模版提取
        O->>O: 步骤 A: 识别与打标
        O->>TemplateAgents: run(标准 Agent)
        TemplateAgents-->>O: 返回所有可能的模版
        O->>O: 步骤 B: 分诊 (标准 vs 非标)
        O->>O: 步骤 C: 专家会诊
        O->>TemplateAgents: run(非标 Agent, on 非标模版)
        TemplateAgents-->>O: 返回精确的非标模版信息
        O->>O: 步骤 D: 汇总与去重
        O->>M: smart_edit(templates.json)
        O-->>C: sse(phase_start), sse(note), sse(artifact), sse(phase_end)
    end
    
    box rgb(255, 240, 245) 阶段 7: 最终清单整合
        O->>M: read_file(所有 summary.md)
        M-->>O: 返回报告内容
        O->>O: 步骤 A: 构建大纲
        O->>ChecklistAgents: run(大纲 Agent, on 评分报告)
        ChecklistAgents-->>O: 返回“满分行动大纲”
        O->>M: smart_edit(checklist_outline.md)
        O->>O: 步骤 B: 拆分大纲 & 分三次富化
        loop for each in [商务, 技术, 报价]
            O->>ChecklistAgents: run(富化 Agent, on 大纲分块 + 对应报告)
            ChecklistAgents-->>O: 返回被富化的清单分块
        end
        O->>O: 步骤 C: 合并最终清单
        O->>M: smart_edit(final_checklist.md)
        O-->>C: sse(phase_start), sse(note), sse(artifact), sse(phase_end)
    end

    O-->>C: sse(complete)
```
