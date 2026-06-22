# Defect Description Agent 评估框架 — 架构文档

## 概述

基于 `agent_evaluation_proposal_v3.0.md` 设计、以 RAGFlow SDK + vLLM 为基础设施实现的 Agent 能力自动化评估框架。当前版本实现了 STEP 1-2（黄金测试集构建 + 基线评估引擎），并通过 LLM-as-a-Judge 模式实现了 **"用 Agent/LLM 评估 Agent"** 的闭环迭代架构。

## 项目结构

```
agent-evaluation-framework/
├── pyproject.toml              # 项目配置 + 依赖
├── .env.example                # 环境变量模板
├── config/
│   └── __init__.py             # 配置中心（权重、API、路径）
├── src/
│   ├── cli.py                  # Click CLI 入口 (eval-framework)
│   ├── orchestrator.py         # 评估流水线编排器
│   ├── agent_client.py         # RAGFlow SDK 客户端（调用被评估 Agent）
│   ├── llm_judge.py            # vLLM LLM-as-a-Judge 评审
│   ├── models/                 # Pydantic 数据模型
│   │   ├── test_data.py        #   测试集模型
│   │   ├── agent_response.py   #   Agent 响应模型
│   │   ├── evaluation_result.py #   评估结果模型
│   │   └── monitoring.py       #   演进指标视图模型（派生）
│   ├── data/
│   │   └── loader.py           # 测试数据加载器
│   ├── evaluation/             # 维度评估模块
│   │   ├── base.py             #   共享工具（样本匹配迭代器）
│   │   ├── summary_accuracy.py #   Summary准确性 (30%)
│   │   ├── conflict_detection.py #  冲突检测 (25%)
│   │   ├── grammar_correction.py # 语法纠错 (20%)
│   │   ├── system_stability.py #   系统稳定性 (10%)
│   │   └── output_quality.py   #   输出质量 (15%, 占位)
│   ├── monitoring/             # 演进监控 / Dashboard（监控 Agent）
│   │   ├── metrics.py          #   确定性指标：Δ / 成熟度轨迹 / 维度斜率
│   │   └── dashboard.py        #   rich 表格渲染 + 图表编排 + LLM 洞察
│   └── reporting/
│       ├── score_calculator.py #   综合得分 + 成熟度评级
│       ├── report_generator.py #   JSON 报告生成
│       └── chart_builder.py    #   雷达图 + 趋势图 + 成熟度/斜率图
└── test_data/                  # 测试数据（JSON）
    ├── standard/               #   标准测试样本 (5条)
    ├── anomaly/                #   异常用例 (5条, E1-E4)
    └── incremental/            #   增量测试序列 (2条)
```

## 评估维度

| 维度 | 权重 | 方法 | 计算公式 |
|------|------|------|----------|
| Summary 准确性 | 30% | LLM-as-a-Judge | 语义准确×0.35 + 字段正确×0.30 + 概括质量×0.20 + 信息完整×0.15 |
| 冲突检测 | 25% | LLM-as-a-Judge + F1 | TP/FP/FN → F1 = 2×(P×R)/(P+R), 得分 = F1×100 |
| 语法纠错 | 20% | LLM-as-a-Judge | 修复率×100×0.7 + (1-误改率)×100×0.3 |
| 输出质量 | 15% | 人工盲评（占位） | 流畅×0.3 + 专业×0.5 + 格式×0.2 |
| 系统稳定性 | 10% | 异常处理 + 推理效率 | (异常处理率×0.5 + 推理效率达标率×0.5)×100 |

**综合得分** = 各维度得分 × 权重之和（满分100）

**成熟度评级**: L1(<60) → L2(60-74) → L3(75-89) → L4(≥90)

## 核心架构：Agent/LLM 评估 Agent 的闭环迭代

### 设计理念

本框架的核心思路是构建一条 **"被评估 Agent → 评审 LLM → 评估报告 → 优化反馈 → 迭代重评"** 的闭环链路：

```
┌──────────────────────────────────────────────────────────────────────┐
│                  Agent 评估闭环 — 自动化引擎                          │
│                                                                      │
│    ┌─────────────┐    RAGFlow SDK    ┌──────────────┐               │
│    │  黄金测试集   │ ──────────────> │  被评估 Agent  │               │
│    │ (test_data/) │   逐样本调用      │  (RAGFlow)    │               │
│    └─────────────┘                  └──────┬───────┘               │
│           │                                │                        │
│           │  Ground Truth                  │ Agent 响应              │
│           │                                ▼                        │
│           │                      ┌──────────────────┐               │
│           └────────────────────> │  LLM-as-a-Judge  │               │
│                                  │  (vLLM + Gemma)  │               │
│                                  └──────┬───────────┘               │
│                                         │ 结构化评分                  │
│                                         ▼                            │
│                                  ┌──────────────┐                   │
│                                  │ 评分汇总 +    │                   │
│                                  │ 基线对比      │                   │
│                                  └──────┬───────┘                   │
│                                         │                            │
│                        ┌────────────────┼────────────────┐          │
│                        ▼                ▼                ▼          │
│                 ┌────────────┐  ┌────────────┐  ┌────────────┐     │
│                 │ 雷达图      │  │ 趋势图      │  │ JSON 报告   │     │
│                 │ (五维度)    │  │ (历史对比)  │  │ (完整数据)  │     │
│                 └────────────┘  └────────────┘  └────────────┘     │
│                                                                      │
│    ┌─────────────────────── 数据飞轮 ──────────────────────────┐    │
│    │                                                            │    │
│    │  生产 case → 筛选标注 → 扩充测试集 → 重新评估 → 更新基线    │    │
│    │                                                            │    │
│    └────────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

### 双 Agent 角色分工

框架中存在两个角色不同的 Agent/LLM，形成 **"被评者—评审者"** 的评估关系：

| 角色 | 实体 | 技术栈 | 职责 |
|------|------|--------|------|
| **被评估 Agent** | RAGFlow Agent | `ragflow_sdk` → RAGFlow 平台 | 接收 defect description，输出结构化 Summary、冲突检测、语法纠错 |
| **评审 LLM** | vLLM + Gemma | `openai` SDK → vLLM 推理服务 | 对比 Ground Truth 与 Agent 输出，按维度返回结构化评分 (0-100) |

### 评估引擎数据流

```
CLI: eval-framework run
    │
    ▼
EvaluationOrchestrator.run_full_evaluation()
    │
    ├─ 1. load_test_data()
    │     test_data/*.json → TestDataSet (Pydantic)
    │
    ├─ 2. AgentClient.evaluate_batch(standard_samples)
    │     每条样本:
    │       ragflow_sdk → agent.create_session() → session.ask(description, stream=True)
    │       → AgentResponse {summary, conflicts, corrections, processing_time_ms}
    │
    ├─ 3. AgentClient.evaluate_batch(anomaly_cases)
    │     同上流程，收集异常用例响应
    │
    ├─ 4. 维度评估（逐维度调用评审 LLM）
    │     │
    │     ├─ summary_accuracy.evaluate()
    │     │    逐样本: llm_judge.judge_summary(ground_truth, agent_summary)
    │     │    → 4 子维度加权 → DimensionScore
    │     │
    │     ├─ conflict_detection.evaluate()
    │     │    逐样本: llm_judge.judge_conflicts(input, response, expected)
    │     │    → TP/FP/FN 汇总 → P/R/F1 → DimensionScore
    │     │
    │     ├─ grammar_correction.evaluate()
    │     │    逐样本: llm_judge.judge_grammar(input, response, expected_errors)
    │     │    → fix_rate × 0.7 + (1-overcorrection) × 0.3 → DimensionScore
    │     │
    │     ├─ system_stability.evaluate()
    │     │    异常处理率(关键词检测) × 0.5 + 推理效率(时延阈值) × 0.5
    │     │    → DimensionScore
    │     │
    │     └─ output_quality.evaluate()
    │          占位返回 0.0（待人工盲评接入）
    │
    ├─ 5. build_composite_result()
    │     各维度 × 权重 → 总分 → L1~L4 成熟度
    │     可选: 与 --baseline 对比，计算 delta
    │
    └─ 6. 输出报告
          ├── results/YYYY-MM-DD_HHMMSS/report.json
          ├── results/.../radar_chart.png
          └── results/.../trend_chart.png (≥2 次评估后)
```

### 评审 LLM 的三路评审机制

`LLMJudge` 针对三个自动化维度设计了专用的评审 Prompt，均通过 vLLM 推理服务执行：

| 评审方法 | Prompt | 输入 | 输出 |
|----------|--------|------|------|
| `judge_summary()` | JUDGE_SUMMARY_PROMPT | Ground Truth + Agent Summary | 4 子维度分数 (0-100) + 评语 |
| `judge_conflicts()` | JUDGE_CONFLICT_PROMPT | 原文 + Agent 响应 + 预期冲突 | detected_count, false_positives, detection_details |
| `judge_grammar()` | JUDGE_GRAMMAR_PROMPT | 原文 + Agent 响应 + 预期错误 | correctly_fixed, total_errors, over_corrections |

所有评审调用均设置 `temperature=0.0`，解析策略包含 JSON 直解 → 代码块提取 → 花括号匹配三级 fallback，失败时自动重试（最多 2 次）。

### 闭环迭代与演进的完整流程

本框架按照 7 步生命周期设计闭环评估体系，当前实现覆盖 STEP 1-2，并为 STEP 4-7 预留了扩展接口：

```
STEP 1: 构建验证数据集 ─── [已实现]
  test_data/standard/ → 标准样本 (含 Ground Truth、冲突标注、语法错误标注)
  test_data/anomaly/  → 异常用例 (超长/乱码/格式异常/跨语言)
  test_data/incremental/ → 增量序列 (多轮追问，测推理效率)
          │
          ▼
STEP 2: 建立基线 ─── [已实现]
  eval-framework run → 全量评估 → 五维度得分 + 成熟度评级
  eval-framework run --baseline <path> → 与上次基线对比 delta
          │
          ▼
STEP 3: Go-live 部署上线 ─── [手动]
  Quality Gate: 总分 ≥ 60 (L2) 方可上线
          │
          ▼
STEP 4: 监控与运维 ─── [待实现，详见 step4_monitoring_implementation_guide.md]
  6 项监控: 可用性 / 推理效率 / 输出质量抽检 / 用户反馈 / 异常处理 / 质量漂移
          │
          ▼
STEP 5: 收集问题 + 归档 ─── [待实现]
  低分 case + 用户拒绝 case + 漂移告警 case → 结构化归档 (Case ID / 维度 / 根因)
          │
          ▼
STEP 6: 优化 Agent 迭代开发 ─── [每 2 周一轮]
  Top-N 问题 → Prompt/RAG 调优 → 回归测试 → 对比基线
  ├─ 得分提升 + 无回归 → 合并发布，更新基线
  └─ 出现回归 → 回滚，重新调整
          │
          ▼
STEP 7: 数据飞轮 ─── [持续]
  生产 case → 专家标注 → 扩充黄金测试集 → 回到 STEP 2 重评基线
  每迭代 +10~20 条新 case，每季度大版本更新测试集
```

### 基线追踪与成长可视化

框架已内置多轮评估的历史对比能力，支撑闭环迭代中的效果验证：

| 机制 | 实现 | 用途 |
|------|------|------|
| **基线对比** | `--baseline` CLI 参数 → `CompositeResult.baseline_comparison` | 本轮 vs 指定历史 run 的各维度 delta |
| **趋势图** | `build_trend_chart()` 读取 `results/` 下所有历史 `report.json` | 五维度得分随时间变化的折线图 |
| **雷达图** | `build_radar_chart()` | 当前 run 的五维度能力分布 |
| **报告归档** | `results/YYYY-MM-DD_HHMMSS/` 目录结构 | 每次 run 独立目录，JSON + 图表完整保留 |

### 演进监控 Dashboard（监控 Agent）

在多轮评估归档之上，`src/monitoring/` 提供一个**只读、离线优先**的"监控 Agent"，量化被评估 Agent 的能力随迭代提升与演进的进度。数据源完全是现有 `results/*/report.json` 历史，无需生产部署或额外数据库。

```
eval-framework dashboard [--limit N] [--charts] [--no-insight]
    │
    ▼  src/monitoring/dashboard.py
    ├─ load_previous_results(RESULTS_DIR, limit)      # 复用 reporting
    ├─ metrics.compute_run_deltas()                    # 逐轮 Δ（≤ 阈值标红）
    ├─ metrics.compute_maturity_trajectory()           # L1→L4 阶梯轨迹
    ├─ metrics.compute_dimension_slopes()              # 各维度线性回归斜率 + R²
    ├─ rich 表格输出（Δ / 成熟度 / 斜率）→ 终端
    ├─ --charts → chart_builder 成熟度阶梯图 + 斜率条形图 → results/monitoring/*.png
    └─ 非 --no-insight 且 vLLM 可达 → llm_judge.judge_evolution(digest) → 中文演进叙事
```

| 组件 | 位置 | 职责 |
|------|------|------|
| 指标计算 | `src/monitoring/metrics.py` | 纯函数，输入 `list[CompositeResult]` → `RunDelta` / `MaturityStep` / `DimensionSlope` / `EvolutionDigest`（numpy `polyfit` 求斜率） |
| 视图模型 | `src/models/monitoring.py` | 派生指标的 Pydantic 模型（非持久化，仅渲染/喂给 LLM） |
| 渲染编排 | `src/monitoring/dashboard.py` | rich 表格 + 图表编排 + LLM 洞察调用，失败均降级不中断 |
| LLM 洞察 | `src/llm_judge.py::judge_evolution()` | 复用 vLLM，`temperature=0.0`，读量化摘要生成中文叙事（自由文本，走 `_call_text_with_retry` 而非 JSON fallback） |
| 图表 | `src/reporting/chart_builder.py` | 新增 `build_maturity_chart()` + `build_slope_chart()`，复用 Agg 后端 |
| 配置 | `config/__init__.py` | `MONITORING_REGRESSION_THRESHOLD`（Δ 标红阈值，默认 -3.0）、`MONITORING_SLOPE_MIN_RUNS`（算斜率最少轮数，默认 3） |

**设计要点**：
- **离线优先**：`dashboard` 不要求 RAGFlow；仅 LLM 洞察需要 vLLM，不可达时 warning 跳过（沿用 `dataset import` 的 degraded 模式）。
- **图表默认关**：终端表格为主交付物，`--charts` 显式开启 PNG（写入 `results/monitoring/`，不污染单次 run 目录）。
- **边界安全**：0 轮（友好提示）/ 1~2 轮（斜率 N/A）/ 旧报告缺维度（安全跳过）均有处理。
- **零新依赖**：numpy / matplotlib / rich / pydantic 均已在 `pyproject.toml`。

### 演进路径

```
当前状态 (STEP 1-2)                     近期目标                              远期目标
─────────────────────              ──────────────────              ──────────────────
5 条标准样本                        200+ 条标准样本                   季度全量评估
5 条异常用例                        40+ 条异常用例                    行业基准横向对比
2 条增量序列                        20+ 条增量序列                    自动化 CI/CD 集成
Gemma 31B 评审                      校准集验证 (LLM vs 人工 ≥0.85)   多模型交叉评审
人工盲评占位 (0分)                   专家盲评流程接入                  半自动化人工评估管线
手动 CLI 触发                       定时调度 + 监控告警               生产环境实时抽检
数据飞轮设计                        生产 case 反哺测试集              完全自动化的闭环飞轮
```

## 关键设计决策

1. **RAGFlow SDK 隔离会话** — 每条测试样本创建独立 RAGFlow Session，避免上下文污染
2. **vLLM 本地推理** — 评审 LLM 通过 vLLM 部署于内网，无外部 API 依赖，延迟可控
3. **Pydantic 数据模型** — 确保数据验证，在数据加载和响应解析时捕获格式错误
4. **独立维度模块** — 每个评估维度是独立 Python 模块，通过 `evaluation/base.py` 共享样本匹配逻辑
5. **文件存储** — JSON 文件 + 日期目录，简单且可版本控制，无需数据库
6. **三级 JSON 解析 fallback** — 直解 → 代码块提取 → 花括号匹配，提升评审 LLM 输出容错性

## 环境搭建

### 1. 创建 Conda 虚拟环境

```bash
conda create -n agent-eval python=3.11 -y
conda activate agent-eval
```

### 2. 安装项目依赖

```bash
cd agent-evaluation-framework
pip install -e .
pip install -e ".[dev]"   # 开发工具（pytest、ruff 等）
```

### 3. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入实际值：
# RAGFLOW_API_KEY=ragflow-xxx
# RAGFLOW_BASE_URL=http://10.10.11.7:9380
# RAGFLOW_AGENT_ID=xxx
# OPENAI_API_KEY=vllm
# OPENAI_BASE_URL=http://10.10.11.7:11542/v1
# OPENAI_MODEL=google/gemma-4-31B-it
```

### 4. 验证安装

```bash
eval-framework check      # 检查环境连通性
eval-framework validate   # 验证测试数据格式
```

## CLI 使用方式

```bash
conda activate agent-eval

eval-framework quickrun --sample-limit 5     # 快速评估
eval-framework run                           # 全量评估
eval-framework run -d conflict_detection -d grammar_correction  # 指定维度
eval-framework run --baseline results/2026-05-10_143022/report.json  # 基线对比
eval-framework report                        # 查看历史报告
eval-framework dashboard --no-insight        # 演进监控（离线：Δ / 成熟度轨迹 / 维度斜率）
eval-framework dashboard --charts            # 同上 + 成熟度/斜率 PNG 图表
eval-framework dashboard                     # 同上 + vLLM 中文演进叙事（监控 Agent）
```

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `RAGFLOW_API_KEY` | RAGFlow API 密钥（必需） | — |
| `RAGFLOW_BASE_URL` | RAGFlow 服务地址 | `http://localhost:9380` |
| `RAGFLOW_AGENT_ID` | 被评估 Agent ID（必需） | — |
| `OPENAI_API_KEY` | vLLM API Key（设为 `vllm`） | — |
| `OPENAI_BASE_URL` | vLLM 推理服务地址 | `http://localhost:11542/v1` |
| `OPENAI_MODEL` | 评审模型 | `google/gemma-4-31B-it` |
| `AGENT_TIMEOUT_SEC` | 请求超时（秒） | `120` |
| `TEST_DATA_DIR` | 测试数据目录 | `./test_data` |
| `RESULTS_DIR` | 结果输出目录 | `./results` |
| `MONITORING_REGRESSION_THRESHOLD` | 演进面板：Δ 标红阈值（vs 上轮总分） | `-3.0` |
| `MONITORING_SLOPE_MIN_RUNS` | 演进面板：计算维度斜率所需最少轮数 | `3` |
