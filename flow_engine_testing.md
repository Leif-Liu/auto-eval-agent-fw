# flow_engine 测试方法与候选 features

`src/flow_engine/` 是基于 `claude_agent_sdk` 的多 agent / 多 skill 编排 runtime。本文档记录**如何测试它**（含实测命令与通过判据）以及**后续候选 features**（供学习后决策）。

入口：`src/flow_engine/example_flow.py`，示例 flow 为 `describe → judge → approve(门) → human_fix(人) → report`。

> 与 `src/orchestrator.py`（评估流程编排器）是不同模块，包名刻意避开冲突。

---

## 前置条件

| 项 | 要求 |
|---|---|
| Python 环境 | conda env `agent-eval`（含 `claude_agent_sdk==0.2.123`） |
| `.env` | `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_MODEL`（Aliyun MaaS 网关用小写 `glm-5.2`） |
| checkpoint | 默认落 `results/flow_run.json`，每次**新 run 前 `rm -f`**（除非故意 resume） |
| 编辑器告警 | Pyright 报 `Import ".agents" could not be resolved` 是它没指向 conda env，**运行时无影响，忽略** |

两个核心开关：

| 开关 | 取值 | 含义 |
|---|---|---|
| 位置参数 | `mock` / `real` | mock 不调 LLM（用 `MockRunner`）；real 调真实 LLM（`AgentRunner`） |
| `--hitl` | `interactive`（默认）/ `pause` | interactive：终端 `input()` 实时收决策；pause：raise 退出 + `--resume` |
| `--resume STEP DECISION` | 可选 | 把决策注入 `ckpt.pending`，优先级**高于** handler |

---

## 测试矩阵总表

| # | 场景 | 命令（节选） | 验证什么 | 成本 |
|---|---|---|---|---|
| 1 | pause 空启动 | `mock --hitl pause` | DAG 调度 + HITL 暂停 + 落盘 | 0 |
| 2 | pause resume 单步 | `mock --hitl pause --resume approve APPROVED` | resume 跳过已完成 + 消费 pending | 0 |
| 3 | pause 跑到完成 | 继续上一步 `--resume human_fix none` | 全链路 + report 拿到上游 context | 0 |
| 4 | 幂等重跑 | 完成态后再 `mock --hitl pause` | checkpoint 幂等（全跳过） | 0 |
| 5 | interactive 一次跑完 | `printf 'A\nB\n' \| mock` | 实时交互、不退出 | 0 |
| 6 | 混合优先级 | pause 暂停后 `--hitl interactive --resume approve APPROVED` | `pending` 优先于 handler | 0 |
| 7 | real 端到端 | `real`（默认 interactive） | 真实 LLM + 实时审批闭环 | 3 次 LLM |

---

## A. 离线 mock 测试（不花钱）

> mock 模式用 `MockRunner`，输出形如 `[agent-name] mock output (prompt N chars)`；N 反映上游 context 是否正确注入。

### 场景 1–3：pause-resume 三段式

```bash
rm -f results/flow_run.json
python -m src.flow_engine.example_flow mock --hitl pause
# 期望: describe/judge mock 跑完 → 在 approve 暂停, 打印 resume 提示

python -m src.flow_engine.example_flow mock --hitl pause --resume approve APPROVED
# 期望: 跳过 describe/judge → 消费 approve=APPROVED → 在 human_fix 暂停

python -m src.flow_engine.example_flow mock --hitl pause --resume human_fix none
# 期望: 消费 human_fix=none → 跑 report → === FLOW COMPLETE ===
```

**通过判据**：第 3 步末尾出现 `=== FLOW COMPLETE ===`，且 `report` 的 prompt 字符数 > `judge`（说明 context 注入了 judge + fix）。

### 场景 4：幂等重跑

```bash
# 紧接场景 3（checkpoint 已全 completed）
python -m src.flow_engine.example_flow mock --hitl pause
# 期望: 不再暂停, 直接 === FLOW COMPLETE === (所有 step 跳过)
```

**意义**：证明 crash/中断后重跑不会重复执行已完成的 step——resume 的可靠性根基。

### 场景 5：interactive 一次跑完（实时交互）

```bash
rm -f results/flow_run.json
printf 'APPROVED\nnone\n' | python -m src.flow_engine.example_flow mock
# 期望: describe/judge mock → approve 实时收 "APPROVED" → human_fix 实时收 "none" → report mock → 一次完成
```

**通过判据**：输出里出现两次 `your decision>` 提示，且**没有** `paused at step`（进程未退出）。用管道喂 stdin 模拟实时输入。

### 场景 6：混合 —— resume 注入 + interactive 实时（验证优先级）

```bash
rm -f results/flow_run.json
python -m src.flow_engine.example_flow mock --hitl pause >/dev/null 2>&1   # 跑到 approve 暂停
printf 'none\n' | python -m src.flow_engine.example_flow mock --hitl interactive --resume approve APPROVED
# 期望: approve 用 pending 的 APPROVED(不调 handler), human_fix 走 stdin 读 "none", report 完成
```

**意义**：证明 `ckpt.pending` 决策优先于 handler——外部注入（resume / Web / IM）和实时输入能共存。

---

## B. real 端到端测试（花 3 次 LLM）

```bash
rm -f results/flow_run.json        # 关键! 否则被旧存档短路
python -m src.flow_engine.example_flow real
# 交互: approve 时敲 APPROVE 回车; human_fix 时敲 none 回车
```

**通过判据**（全满足 = pass）：

1. `describe` / `judge` / `report` 三步 trace 尾部都是 `{'type': 'ResultMessage', 'num_turns': 1, 'is_error': False}`
2. `report` 文本里**引用了 `judge` 的分数**（如 "82/100"）——证明 ContextBus 把 `judge.output` 跨步喂给了 `report`
3. `approve` / `human_fix` 的 trace 是 `{'human_input': True}`，且 text 是你实时敲的内容
4. `results/flow_run.json` 里 `completed` 含全部 5 步，`pending` 为空

**实测输出（节选，glm-5.2）**：

```
===== describe (trace: ResultMessage num_turns=1 is_error=False) =====
**Defect: Checkout Login Button 500 Error with Large Cart**
**Reproduction Steps:** 1. Add 51 or more items ...  (807 chars)

===== judge (trace: ResultMessage num_turns=1 is_error=False) =====
**Verdict:** Strong defect description ... **Score:** 82/100   (656 chars)

===== approve / human_fix (trace: human_input) =====
approve / none

===== report (trace: ResultMessage num_turns=1 is_error=False) =====
# Defect Description Quality Evaluation Report
**Score:** 82/100  ... (1718 chars, 引用了 judge 的分数)
```

### 验证 checkpoint 内容（不用重跑）

```bash
python - <<'PY'
import json
d = json.load(open("results/flow_run.json"))
print("completed:", list(d["completed"].keys()))
print("pending  :", list(d["pending"].keys()))
for sid, out in d["completed"].items():
    if sid.startswith("__"): continue
    print(f"--- {sid} ({out['trace'][-1]}) ---\n{out['text'][:200]}\n")
PY
```

---

## 故障排查

| 现象 | 原因 | 修复 |
|---|---|---|
| real 模式输出却是 `[xxx] mock output`，且无暂停 | 旧 checkpoint 全 completed，所有 step 被跳过，读到的是磁盘上的旧文本 | `rm -f results/flow_run.json` 后重跑 |
| `TypeError: catching classes that do not inherit from BaseException` | （已修）`PendingApproval` 曾是 dataclass | 已改为继承 `Exception`，若再现说明回退了 |
| interactive 模式在 CI / 后台卡死 | `input()` 等 stdin，无终端 | 改用 `--hitl pause`（raise 退出，适合无 TTY） |
| `unrecognized arguments` 传 decision | （已修）曾需单独 `--decision` | 现已支持 `--resume STEP DECISION` 位置形式 |
| Pyright `Import ".agents" could not be resolved` | 编辑器没指向 conda env | 运行时无影响，忽略；要消告警就把解释器切到 `agent-eval` |
| real 报 model 400 | 网关拒非 `glm-5.2` model 名 | `.env` 里 `ANTHROPIC_MODEL=glm-5.2`（小写） |

---

## 候选 features（路线图）

> 每条含：**动机 / 实现要点 / 复杂度 / 建议优先级 / 如何验证**。复杂度 ☆(半天)～★★★(数天)；优先级 P0(必做)→P2(锦上添花)。

### 1. DAG 并发执行  ★·P0

- **动机**：现在 `execute()` 是串行 topo。无依赖的分支（如 `describe` 拆成两个独立调研 agent）应并行，省墙钟时间。
- **实现**：`runtime.py` 里把"取下一个 ready step"改成维护 ready 集合，用 `asyncio.gather` 并发推进；done 一个就唤醒等待它的后继。
- **验证**：构造 `a,b → c` flow，a/b 各 sleep 2s，总时长应 ≈2s（并发）而非 4s（串行）。
- **注意**：要配**全局并发闸**（semaphore），否则 token 会爆。

### 2. 全局并发闸 + 预算护栏  ★·P0（与 #1 配套）

- **动机**：并发开后多个 agent 同时调 LLM，token/速率会炸。
- **实现**：`AgentRunner.run` 外包一层 `asyncio.Semaphore(N)`；可选加 token 预算累加，超限 raise。
- **验证**：N=1 时行为等价串行；N=2 时最多 2 个并发。

### 3. skill registry  ★·P1

- **动机**：现在 `Step(kind="skill")` 没有解析路径。让 skill 和 agent 同构，runtime 能控制何时进 skill（而不是把 skill 藏在某个 agent 黑盒里）。
- **实现**：`SkillRegistry` 存 `name -> AgentSpec`；`execute()` 里 `spec = step.agent or registry[step.skill]`（已预留参数 `skill_registry`）。
- **验证**：注册一个 `arch-review` skill，`Step("x", kind="skill", skill="arch-review")` 能跑通。

### 4. 结构化 human 输入  ★·P1

- **动机**：现在人敲的是自由文本（"APPROVED"/"none"）。关键决策应结构化（如 `{decision: approve|modify|reject, comment: ...}`），防止手抖、便于下游 step 解析。
- **实现**：`HumanSpec` 加 `output_schema: pydantic.BaseModel`；`InteractiveHitl` 收到输入后 `schema.model_validate_json(text)`，失败重问。
- **验证**：故意敲非法 JSON，应被拒并重新提示。

### 5. trace 可观测 / 导出  ☆·P1

- **动机**：`StepOutput.trace` 已收每步的消息类型/turn 数，但没有聚合视图。调试和审计需要"一眼看全"。
- **实现**：加 `trace.py`，把每个 step 的 trace 聚合成一张表（step / 类型序列 / 耗时 / 是否 error / 工具调用次数），支持 print 和导出 JSON。
- **验证**：real run 后打印聚合表，能看到每步内部发生了什么。

### 6. 自定义 HITL handler（Web / IM）  ★★·P2

- **动机**：`HitlHandler` 协议已就位，目前只有 terminal 两种实现。接 Web/IM（飞书/钉钉/Slack）后，审批可异步跨人跨时区。
- **实现**：写 `WebhookHitl`——把 PendingApproval 推到外部，`await` 一个 future/callback；决策回填时 resolve。
- **验证**：mock 一个 callback handler，断言它能和 interactive 一样产出决策。
- **注意**：这条路天然走 pause-resume（进程不保活），checkpoint 是基础。

### 7. 真实多轮 session 复用  ☆·P2

- **动机**：某些 agent 需要跨 step 保持记忆（如反复追问同一个代码库）。现在每个 step 默认无状态。
- **实现**：给 `Step` 设 `session_id`，`AgentRunner` 已透传 `options.resume=session_id`；需要一个 session store 把 SDK 返回的 session id 存起来供下次复用。
- **验证**：两个 step 共享 session_id，第二个 step 能"记得"第一个的上下文。
- **注意**：大多数 step 不需要——别默认开，token 成本高。

### 决策建议

| 你的场景 | 建议先做 |
|---|---|
| flow 会变复杂、有并行机会 | #1 + #2（并发 + 闸） |
| 要把 skill 纳入编排 | #3 |
| 审批是关键流程、要防错 | #4 |
| 要给别人看 / 接外部系统 | #5 → #6 |
| 单 agent 需要记忆 | #7（按需） |

---

## 关键设计点（备忘）

- **`ckpt.pending` 优先于 `hitl` handler**：resume 注入 / 外部系统的决策最高优先；handler 只在没有 pending 时被调用。
- **每步原子 checkpoint**：`Checkpoint.save()` 用 `os.replace` 原子写；`execute` 每完成一步就存。crash/被杀都能 resume。
- **HITL 三态合一**：`InteractiveHitl`（实时）/ `PauseResumeHitl`（退出+resume）/ 自定义（Web），共享同一套 checkpoint——这就是"实时 pause-resume"。
- **agent step 默认 `query()`**：无状态、简单；需要多轮记忆才给 `Step.session_id`，需要 mid-turn 中断才升级到 `ClaudeSDKClient`。
- **skill 与 agent 同构**（#3 落地后）：`Step(kind="skill")` 走 registry 解析为 `AgentSpec`，和 agent step 走同一条路，runtime 能控制进入时机。
- **`PendingApproval` 是 `Exception`**：从 `execute()` 任意深度冒泡出来，不需要特殊返回值 plumbing。
