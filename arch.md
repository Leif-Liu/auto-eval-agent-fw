# Defect Description Agent 评估框架 — 架构文档

## 概述

基于 `agent_evaluation_proposal_v3.0.md` 和 `step4_monitoring_implementation_guide.md` 实现的 Agent 能力评估框架。当前版本实现了 STEP 1-2 核心评估引擎。

## 项目结构

```
agent-evaluation-framework/
├── pyproject.toml              # 项目配置 + 依赖
├── .env.example                # 环境变量模板
├── config/
│   └── __init__.py             # 配置中心（权重、API、路径）
├── src/
│   ├── cli.py                  # Click CLI 入口
│   ├── orchestrator.py         # 评估流水线编排器
│   ├── agent_client.py         # Agent HTTP API 客户端
│   ├── llm_judge.py            # OpenAI LLM-as-a-Judge 评审
│   ├── models/                 # Pydantic 数据模型
│   │   ├── test_data.py        #   测试集模型
│   │   ├── agent_response.py   #   Agent 响应模型
│   │   └── evaluation_result.py #   评估结果模型
│   ├── data/
│   │   └── loader.py           # 测试数据加载器
│   ├── evaluation/             # 维度评估模块
│   │   ├── base.py             #   共享工具（样本匹配迭代器）
│   │   ├── summary_accuracy.py #   Summary准确性 (30%)
│   │   ├── conflict_detection.py #  冲突检测 (25%)
│   │   ├── grammar_correction.py # 语法纠错 (20%)
│   │   ├── system_stability.py #   系统稳定性 (10%)
│   │   └── output_quality.py   #   输出质量 (15%, 占位)
│   └── reporting/
│       ├── score_calculator.py #   综合得分 + 成熟度评级
│       ├── report_generator.py #   JSON 报告生成
│       └── chart_builder.py    #   雷达图 + 趋势图
└── test_data/                  # 测试数据（JSON）
    ├── standard/               #   标准测试样本
    ├── anomaly/                #   异常用例 (E1-E4)
    └── incremental/            #   增量测试序列
```

## 评估维度

| 维度 | 权重 | 方法 | 计算公式 |
|------|------|------|----------|
| Summary 准确性 | 30% | LLM-as-a-Judge | 语义准确×0.35 + 字段正确×0.30 + 概括质量×0.20 + 信息完整×0.15 |
| 冲突检测 | 25% | F1 | F1 = 2×(P×R)/(P+R), 得分 = F1×100 |
| 语法纠错 | 20% | 修复率 + 误改率 | 修复率×100×0.7 + (1-误改率)×100×0.3 |
| 输出质量 | 15% | 人工盲评（占位） | 流畅×0.3 + 专业×0.5 + 格式×0.2 |
| 系统稳定性 | 10% | 异常处理 + 推理效率 | (异常处理率×0.5 + 推理效率达标率×0.5)×100 |

**综合得分** = 各维度得分 × 权重之和（满分100）

**成熟度评级**: L1(<60) → L2(60-74) → L3(75-89) → L4(≥90)

## 数据流

```
CLI (click) 解析命令
    │
    ▼
Orchestrator 加载测试数据 (test_data/*.json → Pydantic 模型)
    │
    ▼
AgentClient.evaluate_batch() → HTTP POST 调用 Agent API → AgentResponse
    │
    ├── 标准样本 → 4个维度评估模块
    │   ├── summary_accuracy  → LLMJudge (OpenAI API)
    │   ├── conflict_detection → TP/FP/FN/TN → F1
    │   ├── grammar_correction → 修复率/误改率
    │   └── output_quality    → 占位 (需人工)
    │
    └── 异常用例 → system_stability (异常处理率 + 推理效率)
    │
    ▼
ScoreCalculator 加权汇总 → CompositeResult
    │
    ▼
ReportGenerator → results/YYYY-MM-DD_HHMMSS/
    ├── report.json          # 完整评估结果
    ├── radar_chart.png      # 雷达图
    └── trend_chart.png      # 趋势图（多次评估后）
```

## 关键设计决策

1. **Pydantic 数据模型** — 确保数据验证，在数据加载和响应解析时捕获格式错误
2. **独立维度模块** — 每个评估维度是一个独立 Python 模块，通过 `evaluation/base.py` 共享样本匹配逻辑
3. **文件存储** — JSON 文件 + 日期目录，简单且可版本控制，无需数据库
4. **Output Quality 占位** — 15% 权重维度返回 0 分，需人工盲评后手动输入
5. **OpenAI LLM-as-Judge** — temperature=0，单次调用返回 4 个子维度分数

## 环境搭建

### 1. 创建 Conda 虚拟环境

```bash
# 创建独立的 conda 环境（Python 3.11）
conda create -n agent-eval python=3.11 -y

# 激活环境
conda activate agent-eval
```

### 2. 安装项目依赖

```bash
# 进入项目目录
cd agent-evaluation-framework

# 以可编辑模式安装（开发时修改代码即时生效）
pip install -e .

# 如需开发工具（pytest、ruff 等）
pip install -e ".[dev]"
```

### 3. 配置环境变量

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env，填入实际值
# OPENAI_API_KEY=sk-your-actual-key
# AGENT_BASE_URL=http://your-agent-host:8080
```

### 4. 验证安装

```bash
# 检查环境（API Key、Agent 连通性、测试数据）
eval-framework check

# 验证测试数据格式
eval-framework validate
```

### 退出/切换环境

```bash
# 退出当前环境
conda deactivate

# 再次使用时重新激活
conda activate agent-eval
```

## CLI 使用方式

```bash
# 确保已激活环境
conda activate agent-eval

# 快速评估（5条样本）
eval-framework quickrun --sample-limit 5

# 全量评估
eval-framework run

# 指定维度评估
eval-framework run -d conflict_detection -d grammar_correction

# 与基线对比
eval-framework run --baseline results/2026-05-10_143022/report.json

# 查看历史报告 + 趋势图
eval-framework report
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `OPENAI_API_KEY` | OpenAI API 密钥（必需） | — |
| `OPENAI_MODEL` | 评审模型 | `gpt-4o` |
| `AGENT_BASE_URL` | Agent API 地址 | `http://localhost:8080` |
| `AGENT_TIMEOUT_SEC` | 请求超时（秒） | `30` |
| `TEST_DATA_DIR` | 测试数据目录 | `./test_data` |
| `RESULTS_DIR` | 结果输出目录 | `./results` |

## 下一步

1. **扩展测试数据** — 将 `standard_samples.json` 扩展到 200+ 条，覆盖所有 Product Line 和 VCU 类型
2. **接入真实 Agent** — 配置 Agent API 地址，运行首次全量评估建立基线
3. **STEP 4 监控模块** — 基于 `step4_monitoring_implementation_guide.md` 实现 6 项监控
4. **人工评估流程** — 建立 Output Quality 人工盲评规范，支持分数录入
5. **数据飞轮** — STEP 7 将生产环境 case 反哺测试集
