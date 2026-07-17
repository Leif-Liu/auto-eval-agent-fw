---
description: 启动 architecture-flow-analyzer 子代理分析当前项目的架构与流程，输出架构图和流程图
argument-hint: [可选：目标路径，默认当前工程]
---

使用 Agent 工具启动 **architecture-flow-analyzer** 子代理（subagent_type 为 `architecture-flow-analyzer`），对指定目标进行分析。

## 任务

- 目标路径：用户在 `$ARGUMENTS` 中提供则用该路径；未提供则默认为当前工程根目录 `/home/liufeng/auto-eval-agent-fw`。
- 让子代理完成其内置 Methodology 的全部五个阶段（Codebase Exploration → Architecture Analysis → Flow Analysis → Diagram Production → Written Analysis）。
- 输出必须包含：
  1. 架构图（Mermaid `graph`）
  2. 关键流程图（Mermaid `sequenceDiagram` / `flowchart`）
  3. 组件清单表
  4. 观察与改进建议

## 指令

在 prompt 中明确传给子代理：
- 待分析的绝对路径
- 输出语言跟随用户（默认中文）
- 严格遵守 Mermaid 语法，标签中含特殊字符时用引号包裹

## 交付

把子代理返回的完整分析（含 Mermaid 图、表格、建议）原样呈现给用户。不要在子代理之外额外补充架构结论——若需补充，明确标注为主代理补充。
