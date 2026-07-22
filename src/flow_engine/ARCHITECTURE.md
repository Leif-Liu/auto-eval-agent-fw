# flow_engine 架构与代码流程

> 本文聚焦 **runtime 是怎么实现的、代码怎么走**。命令与验证方法见 `flow_engine_testing.md`。
>
> 阅读顺序建议：先看 §1–§3 建立整体观，再读 §7（execute 逐行）和 §8（端到端走查）理解细节。

---

## 1. runtime 是什么

`src/flow_engine/` 是一个**多 agent / 多 skill 编排 runtime**，坐在 Claude Agent SDK 之上。它解决一个问题：

> SDK 的 `query()` 是**单向、无状态、fire-and-forget**的一次性 agent 调用。当你要"多个 agent 按确定顺序协作 + 人在关键步决策 + 崩了能恢复"时，SDK 本身给不了流程控制——这层控制由 runtime 提供。

runtime 把三件事统一到 **checkpoint** 这一个机制上：

1. **流程控制** — DAG 拓扑调度，步间显式传数据
2. **人工介入（HITL = Human-In-The-Loop，人在回路中）** — 在自动流程里让人参与决策；本 runtime 落在 `gate`(审批门) 和 `human`(人作为 agent) 两种 step 上，支持实时交互 / 暂停-恢复 / 外部系统三态
3. **崩溃恢复** — 每步原子落盘，进程被杀也能从断点续跑

---

## 2. 分层架构

```
┌──────────────────────────────────────────────────┐
│  Flow (DAG)        你的业务流程: Step 列表         │  ← 声明层
│  example_flow.build_flow()                        │
├──────────────────────────────────────────────────┤
│  Orchestrator Runtime        ← src/flow_engine    │  ← 本文重点
│   topo_order · resolve_inputs · render_prompt     │
│   execute (调度核心) · HitlHandler · Checkpoint   │
├──────────────────────────────────────────────────┤
│  Claude Agent SDK       query(prompt, options)    │  ← 每步一个调用
│  AgentRunner / MockRunner (agents.py)             │
├──────────────────────────────────────────────────┤
│  Claude Code CLI        真正的 agent loop          │  ← SDK spawn 的子进程
│   (模型 + 内置工具 + 权限 + MCP + session)         │
└──────────────────────────────────────────────────┘
```

**职责边界**：runtime **不碰** transport / JSON 协议 / 权限流 / interrupt——这些是 SDK 的活。runtime 只做"调度 + 上下文传递 + HITL + 存档"。

---

## 3. 核心数据结构（`types.py`）

| 结构 | 作用 | 关键字段 |
|---|---|---|
| `Step` | flow 的一个节点 | `id`, `kind`(agent/skill/human/gate), `agent`/`skill`/`human`(按 kind 取一), `inputs`, `depends_on`, `prompt_template`, `session_id` |
| `Flow` | Step 的 DAG | `steps`, 构造时校验 id 唯一 + depends_on 合法 |
| `StepOutput` | 一步的执行结果 | `text`(主输出), `data`(结构化), `trace`(可观测) |
| `Checkpoint` | 持久化的运行状态 | `completed`(已完成步), `pending`(外部注入的决策), `_path` |
| `HumanSpec` | human/gate 步的配置 | `prompt`, `output_hint` |
| `PendingApproval` | HITL 暂停信号 | 继承 `Exception`，`step_id`, `prompt`, `context` |

**几个关键设计点**：

- `Step.inputs` 是 `"本地名" -> "上游step.field"` 的映射（field 默认 `output` = `StepOutput.text`）。这是 ContextBus 的核心——步间数据**显式传递**，不靠 CLI 内部隐式上下文。
- `PendingApproval` 是 **Exception**（不是返回值），从 `execute` 任意深度冒泡出来，不需要特殊返回值 plumbing。
- `Checkpoint.save()` 用 `os.replace` 原子写（`types.py:140-142`）——先写 `.tmp` 再原子替换，避免崩溃写到一半。

---

## 4. runtime.py 组件地图

```
                    ┌─────────────┐
                    │  _deps()    │  算单个 step 的上游依赖
                    └──────┬──────┘
                           │ 被调用
                    ┌──────▼──────┐
                    │ topo_order()│  DFS 拓扑排序 + 环检测
                    └──────┬──────┘
                           │ 返回顺序
   ┌───────────────────────▼─────────────────────────┐
   │                  execute()                       │  调度核心
   │  for sid in topo_order:                          │
   │    resolve_inputs() ──> ctx                      │  解析上游输出
   │    render_prompt()   ──> prompt (agent 分支)      │
   │    分发:                                         │
   │      ├─ HITL ──> HitlHandler (协议)              │
   │      │            ├─ PauseResumeHitl (raise)     │
   │      │            └─ InteractiveHitl (input)     │
   │      └─ agent ──> runner.run()                   │
   │                   ├─ AgentRunner (SDK query)     │
   │                   └─ MockRunner   (离线)          │
   │    ckpt.completed[sid] = out; ckpt.save()        │
   └──────────────────────────────────────────────────┘
```

四个辅助函数 + 三个 handler 类 + 一个 `execute`，共 8 个公开符号（见 `__init__.py`）。

---

## 5. 辅助函数详解

### `topo_order(flow)` — `runtime.py:37-59`

DFS 拓扑排序。对每个 step：先递归访问它的所有依赖，再把自己 append 到序尾。

```python
def visit(sid):
    if sid in seen:    return          # 已访问, 跳过
    if sid in visiting: raise ValueError("cycle")  # 正在访问 = 环
    visiting.add(sid)
    for d in _deps(by_id[sid], idset): visit(d)   # 先访问依赖
    visiting.discard(sid)
    seen.add(sid)
    order.append(sid)                  # 依赖都访问完, 自己入序
```

**输出**：一个 step id 列表，保证每个 step 的上游都排在它前面。

### `_deps(step, idset)` — `runtime.py:23-34`

算一个 step 的所有上游依赖 = 显式 `depends_on` ∪ `inputs` 引用的上游 step。

```python
deps = set(step.depends_on)                       # 显式依赖
for ref in step.inputs.values():                  # inputs 引用的上游
    upstream = ref.split(".", 1)[0]
    if upstream in idset and upstream != step.id:  # 只算 flow 内的
        deps.add(upstream)
```

**关键设计**（`if upstream in idset`）：只把 *flow 内* 的引用算作排序依赖。`example_flow` 里 `describe.inputs={"task": "__seed__.output"}` 的 `__seed__` 不在 flow 里——它是**预置数据**（由 driver 注入到 `ckpt.completed`），不是排序依赖。这区分了"**排序依赖**"和"**数据依赖**"。

### `resolve_inputs(inputs, completed)` — `runtime.py:62-87`

ContextBus 的执行端：把 `"judge.output"` 这种引用解析成实际值。

```python
sid, _, field = ref.partition(".")        # "judge.output" -> ("judge", ".", "output")
field = field or "output"                 # 无 field 默认 output
if sid not in completed: raise RuntimeError(...)  # 上游没跑完
out = completed[sid]
if   field in ("output","text"): ctx[key] = out.text   # 默认: 文本
elif field == "data":           ctx[key] = out.data
elif field == "trace":          ctx[key] = out.trace
else:                           ctx[key] = out.data.get(field)  # data 的子键
```

**字段约定**：`output`/`text` → `StepOutput.text`；`data` → 整个 dict；`trace` → trace 列表；其他 → `data` 的子键。

### `render_prompt(step, ctx)` — `runtime.py:90-99`

把 ctx 渲染成 agent 的 prompt。

```python
if step.prompt_template:
    return step.prompt_template.format(**ctx)     # "Judge:\n{desc}".format(desc=...)
return f"# Context\n{json.dumps(ctx, ...)}\n\n# Task\nProceed."   # 无 template: dump JSON
```

**注意**：`format(**ctx)` 要求 template 的占位符都在 ctx 里有对应 key，否则 `KeyError` 被转成 `RuntimeError`（`L95-98`）。

---

## 6. HITL handler 体系

### `HitlHandler` 协议 — `runtime.py:102-121`

```python
@runtime_checkable
class HitlHandler(Protocol):
    async def __call__(self, *, step, prompt, context, ckpt) -> str: ...
```

任何"收一个决策字符串"的可调用对象都符合。handler **只在没有 pending 决策时**才被调用。

### 两个内置实现

| Handler | 行号 | 行为 | 场景 |
|---|---|---|---|
| `PauseResumeHitl` | `124-132` | `raise PendingApproval(...)` → 进程退出 → 外部带决策 resume | 服务化 / 无 TTY / 异步 |
| `InteractiveHitl` | `135-156` | `print` 提示 + `asyncio.to_thread(input, ...)` 实时收 | 单机终端交互 |

`InteractiveHitl` 用 `asyncio.to_thread` 包 `input()`（`L155`）——`input()` 阻塞 OS 线程，扔到线程池执行避免阻塞 event loop（为未来并发 agent step 预留）。

### 决策优先级（核心）

```
HITL step 到达
  ├─ ckpt.pending 有该步的决策? ──> pop 消费        [最高优先, handler 不调用]
  └─ 否则 ──> 调 hitl handler 收集
                ├─ InteractiveHitl: 终端实时收       [--hitl interactive, 默认]
                ├─ PauseResumeHitl: raise 退出       [--hitl pause]
                └─ 自定义: Web/IM/队列               [可注入]
```

**这个优先级让“外部注入”和“实时输入”能共存**——比如 approve 用 `--resume` 注入，human_fix 走 stdin 实时收，同一次 run 里混用。

### 两个层面：步间 HITL vs 步内工具级 HITL

> 关键区分，经常被混淆：上面的 handler 体系实现的是**步间** HITL；SDK 还提供另一个层面的**步内工具级** HITL，两者发生位置完全不同。

| 维度 | 步间 HITL（当前已实现） | 步内工具级 HITL（SDK 提供，未启用） |
|---|---|---|
| 触发位置 | 两个 step **之间** | 一个 agent step **内部**，agent 想调工具时 |
| 谁触发 | flow 调度器（确定性，到这步就问） | agent 自己（动态，取决于它想做什么） |
| 上报路径 | 不经 CLI — runtime 层直接 `input()` | CLI → SDK 控制协议 → 应用层回调 → 返回 CLI |
| 当前实现 | `gate`/`human` step + `HitlHandler` | **没有**（`AgentRunner` 用 `bypassPermissions` 全放行） |
| SDK 接口 | 不用 SDK 权限机制，runtime 自己做 | `can_use_tool` 回调（必须 `ClaudeSDKClient`） |
| 可控 / 可测 | 高（HITL 点写进 flow，可 resume） | 低（依赖 agent 动态决策，难复现） |

**当前实现证据**：`agents.py: AgentRunner` 用 `permission_mode="bypassPermissions"` + `query()` 一次性——agent step 内部任何工具都自动放行，从未“上传”审批请求；`approve`/`human_fix` 是 step **整步跑完之后**在 flow 层插入的独立 step，HITL 根本不经过 CLI。

**步内工具级 HITL 的标准实现 = `can_use_tool` 回调**（SDK 官方机制，`claude_agent_sdk/client.py:161-180`）：

```
agent 想调工具
  → CLI 通过 stdio 控制协议上报权限请求给 SDK
  → SDK 调用你的 can_use_tool 回调（应用层 Python，HITL 在这做）
  → 回调返回 allow / deny
  → SDK 通过控制协议把决策发回 CLI → CLI 继续
```

这正是“从 claude cli 上传到应用层交互后再返回 cli”的标准形态。**关键限制**：`can_use_tool` 必须用 `ClaudeSDKClient` + streaming prompt（`client.py:163-167` 强制校验，传 str prompt 直接 raise），`query()` 做不到——步内 HITL 需要中途交互通道，而 `query()` 是一次性 fire-and-forget。

**怎么选**：
- 审批点**静态确定**（“第 2 步、第 4 步要审”）→ **步间 HITL（当前实现）更优**：可见、可测、可 resume、不依赖 agent 行为；大多数编排场景够用。
- 审批点**动态**（“agent 一旦想 `rm -rf` 就拦”，无法预先建模）→ 步内工具级 HITL（`can_use_tool`），需升级到 `ClaudeSDKClient`。

---

## 7. `execute` 逐行拆解（`runtime.py:159-212`）

```python
async def execute(flow, *, ckpt, runner, hitl=None, skill_registry=None):
    order = topo_order(flow)                          # L181  ① 算拓扑序
    for sid in order:                                 # L182  ② 串行遍历
        if sid in ckpt.completed: continue            # L183  ③ resume 跳过已完成
        step = flow.step(sid)                         # L185
        ctx = resolve_inputs(step.inputs, ckpt.completed)  # L186  ④ 解析上游

        if step.kind in ("human", "gate"):            # L188  ⑤ HITL 分支
            if sid in ckpt.pending:                   # L189    路径 A: 有 pending
                text = ckpt.pending.pop(sid)          # L190    消费(不重复)
            else:                                     # L191    路径 B: 交 handler
                prompt = step.human.prompt or f"Approve step {sid!r}?"
                handler = hitl if hitl is not None else PauseResumeHitl()
                text = await handler(step=step, prompt=prompt, context=ctx, ckpt=ckpt)
            out = StepOutput(text=text, trace=[{"human_input": True}])   # L199
        else:                                         # L200  ⑥ agent/skill 分支
            spec = step.agent or (skill_registry or {}).get(step.skill or "")  # L201
            if spec is None: raise RuntimeError(...)  # L202  ⑦ spec 缺失门
            prompt = render_prompt(step, ctx)         # L207
            out = await runner.run(spec, prompt, session_id=step.session_id)  # L208

        ckpt.completed[sid] = out                     # L210  ⑧ 回写
        ckpt.save()                                   # L211  ⑨ 每步原子存档
    return ckpt.completed                             # L212
```

**九个动作的含义**：

| 动作 | 行号 | 作用 |
|---|---|---|
| ① 算拓扑序 | L181 | 顺序由 `topo_order` 定，execute 只按序 |
| ② 串行遍历 | L182 | **当前无并发**；DAG 并发是 roadmap 待做项 |
| ③ 跳过已完成 | L183 | resume 幂等的根基 |
| ④ 解析上游 | L186 | 把 `"judge.output"` → 实际文本 |
| ⑤ HITL 分支 | L188-199 | pending 优先 > handler |
| ⑥ agent 分支 | L200-208 | spec 解析 + 渲染 prompt + 调 runner |
| ⑦ spec 缺失门 | L202 | step 既无 agent 又无 skill(或 registry 没注册) → raise |
| ⑧ 回写 | L210 | 结果存进 ckpt.completed |
| ⑨ 存档 | L211 | 每步 `save()`，崩了不丢 |

**没有"flow 完成"的显式状态**：execute 跑完所有 step 就 `return completed`；中途 raise（`PendingApproval` 或错误）就停，已完成的都已存档。这正是 pause-resume 能 work 的基础。

---

## 8. 端到端代码流程走查（重点）

以 `example_flow.build_flow()` 为例，5 步 + 1 个伪输出：

```
__seed__ (pseudo)  →  describe (agent)  →  judge (agent)  →  approve (gate)
                                                       →  human_fix (human)  →  report (agent)
```

### 8.1 入口到完成的完整调用链

```
main()                                                 # example_flow.py:177
  └─ asyncio.run(drive(mode, resume_step, resume_text, task, hitl_mode))   # L201
       │
       drive():                                         # L123
       │  ├─ build_flow()                              # L130  → Flow
       │  ├─ Checkpoint.load(path) or .new(...)        # L133  恢复或新建
       │  ├─ if resume_step: ckpt.pending[step]=text   # L138  注入决策
       │  ├─ if __seed__ not in completed: seed it     # L142  注入初始任务
       │  ├─ runner = MockRunner(...) / AgentRunner()  # L146
       │  ├─ hitl = InteractiveHitl() / PauseResumeHitl()  # L157
       │  │
       │  ├─ try: results = await execute(flow, ckpt, runner, hitl)   # L160
       │  │     │
       │  │     └─ execute():                          # runtime.py:159
       │  │          for sid in topo_order(flow):
       │  │            ... (见 §7)
       │  │
       │  ├─ except PendingApproval as pa:             # L161  pause 模式才到这
       │  │     ckpt.save()                            # L162
       │  │     print("resume with: --resume ...")     # L164
       │  │     return                                 # L167  进程退出
       │  │
       │  └─ else: print("=== FLOW COMPLETE ===")      # L169  正常完成
       │             for sid, out in results: print(...)  # L170
```

### 8.2 场景 A：interactive 模式，一次跑完

`python -m src.flow_engine.example_flow mock`（管道喂两行决策）

每步执行后 `ckpt` 的状态快照：

| 循环指针 | 动作 | `completed` | `pending` |
|---|---|---|---|
| (seed) | driver 注入 `__seed__` | `{__seed__}` | `{}` |
| `describe` | `runner.run` → mock 文本 | `{__seed__, describe}` | `{}` |
| `judge` | `runner.run` → mock 文本 | `{…, judge}` | `{}` |
| `approve` | 无 pending → `InteractiveHitl` 读 stdin `"APPROVED"` | `{…, approve}` | `{}` |
| `human_fix` | 无 pending → `InteractiveHitl` 读 stdin `"none"` | `{…, human_fix}` | `{}` |
| `report` | `runner.run`（prompt 含 judge+fix） | `{…, report}` | `{}` |
| — | 循环结束 → `return completed` → 打印 | (5 步全完成) | `{}` |

**全程一次进程跑完**，approve/human_fix 在终端实时等输入，不退出。

### 8.3 场景 B：pause-resume 三段式

`--hitl pause`，分三次命令跑完。状态变迁：

**Run 1**：`mock --hitl pause`（无 `--resume`）

| 循环指针 | 动作 | `completed` | `pending` |
|---|---|---|---|
| `describe` | runner.run | `{__seed__, describe}` | `{}` |
| `judge` | runner.run | `{…, judge}` | `{}` |
| `approve` | 无 pending → `PauseResumeHitl.raise(PendingApproval)` | `{…, judge}` | `{}` |

→ 异常冒泡到 `drive` 的 `except`（`L161`）→ `ckpt.save()` → 打印 resume 提示 → 进程**退出**。

**Run 2**：`--resume approve "APPROVED"` → driver 先 `ckpt.pending["approve"]="APPROVED"`

| 循环指针 | 动作 | `completed` | `pending` |
|---|---|---|---|
| `describe` | **已完成，跳过**（L183） | `{…, judge}` | `{approve: APPROVED}` |
| `judge` | **已完成，跳过** | 同上 | 同上 |
| `approve` | `pending` 有 → `pop` 消费 | `{…, judge, approve}` | `{}` |
| `human_fix` | 无 pending → raise | `{…, approve}` | `{}` |

→ 又退出，等你 resume human_fix。

**Run 3**：`--resume human_fix "none"`

| 循环指针 | 动作 | `completed` | `pending` |
|---|---|---|---|
| `describe`,`judge`,`approve` | 全跳过 | `{…, approve}` | `{human_fix: none}` |
| `human_fix` | `pending` 有 → `pop` | `{…, human_fix}` | `{}` |
| `report` | runner.run（prompt 含 judge+fix） | `{…, report}` | `{}` |
| — | 循环结束 → 完成 | 全完成 | `{}` |

**观察要点**：
- 每次 resume，已完成的步全部跳过（L183），不重复执行、不重复花 LLM 钱。
- `pending` 的决策被 `pop` 消费后存进 `completed`，所以再跑该步会因 completed 直接跳过——**不会重复消费**。
- `report` 在 Run 3 才执行，它的 prompt 正确包含 `judge.output` 和 `human_fix.output`（resolve_inputs 从 completed 取）。

### 8.4 场景 C：崩溃恢复

假设 Run 1 在 `judge` 执行到一半时进程被 `kill -9`：

- `describe` 已 `save` 进 ckpt（L211 每步都存），`judge` 还没存。
- 重新跑 `execute`：`describe` 在 completed → 跳过；`judge` 不在 completed → **重新执行**。
- 这就是"每步一个存档点"的价值——崩溃只丢最近一个未完成的步。

### 8.5 PendingApproval 的传播路径

这是"exception 作为控制流"的典型用法：

```
PauseResumeHitl.__call__()                    # runtime.py:132
  └─ raise PendingApproval(step_id, prompt, context)
       │
       └─ execute() 没 catch, 继续冒泡       # runtime.py:198 抛出点
            │
            └─ drive() 的 except 捕获        # example_flow.py:161
                 ├─ ckpt.save()              # 持久化(其实每步已存, 这里再保一次 pending 为空的状态)
                 ├─ 打印 pa.step_id / pa.prompt / pa.context
                 └─ return                   # 进程退出, 等外部带 --resume 重启
```

`PendingApproval` 携带 `context`（已 resolve 的上游输出），所以 resume 提示里能给用户看到审批门的上下文预览。

---

## 9. Checkpoint 与 resume 机制

### 状态真相

`Checkpoint` 是 runtime 的**唯一状态来源**，execute 对它的读写接触点：

```
execute ─┬─ 读 ckpt.completed   (L183, 决定跳过谁)
         ├─ 读 ckpt.pending     (L189, 读外部注入的决策)
         ├─ 写 ckpt.completed   (L210, 每步结果回写)
         └─ ckpt.save()         (L211, 每步落盘)
```

driver（`example_flow.drive`）还额外读写：load（L133）、写 pending（L138）、写 seed completed（L142）、except 里 save（L162）。

### 原子写

```python
def save(self):                          # types.py:131
    payload = {...}
    tmp = f"{self._path}.tmp"
    Path(tmp).write_text(json.dumps(payload, ...))
    os.replace(tmp, self._path)          # POSIX 原子 rename
```

先写 `.tmp` 再 `os.replace` 原子替换——即使写到一半被杀，原文件要么是旧版要么是新版，不会出现半写状态。

### resume 语义总结

| 场景 | 机制 |
|---|---|
| 进程崩在 step N 执行中 | step N 没存档 → 重跑时 N 重新执行；N 之前的都跳过 |
| pause 模式在审批门退出 | 审批门没存 completed → 重跑时重新问（或读 pending） |
| `--resume step "决策"` | 决策进 pending → 重跑到该步时 pop 消费 |

**核心不变量**：`completed` 里的步永远不需要再执行；`pending` 里的决策只消费一次。

---

## 10. execute 的控制点清单

"execute 控制具体流程"具体体现在这 6 处：

| # | 控制点 | 行号 | 控制了什么 |
|---|---|---|---|
| 1 | 顺序 | L181 | `topo_order` 给序，execute 按序遍历 |
| 2 | 跳过 | L183 | 已 completed 的跳过 → resume 幂等 |
| 3 | 分发 | L188/L200 | `human/gate` 走 HITL，`agent/skill` 走 runner |
| 4 | HITL 优先级 | L189 | `pending` 决策 > handler |
| 5 | 存档 | L211 | 每步 `save()`，崩了不丢 |
| 6 | 错误门 | L202/L74/L49 | spec 缺失 / 上游未就绪 / 环 → raise 中止 |

---

## 11. 设计权衡与边界行为

| 设计选择 | 取舍 | 当前实现 |
|---|---|---|
| 异常 vs 返回值传暂停 | 异常能从任意深度冒泡，无需特殊返回 plumbing | `PendingApproval` 是 Exception |
| pending vs handler 优先 | 让外部注入和实时输入共存 | pending 最高优先 |
| 每步存 vs 批量存 | 每步存更安全（崩了只丢一步），代价是 IO 多 | 每步 `save()` |
| 串行 vs 并发 | 串行简单可靠，并发省墙钟时间 | **当前串行**（roadmap: 并发） |
| `query()` vs `ClaudeSDKClient` | query 简单无状态，client 支持中断 | 默认 query，需中断才升级 |

**边界行为备忘**：

1. **串行**：`for sid in order`（L182）纯串行，无并发。
2. **没有 flow 完成的显式状态**：跑完所有 step 就 return；中途 raise 就停。
3. **pending 只消费一次**：`pop` 后进 completed，下次该步已 completed 跳过。
4. **默认 handler 是 pause**：不传 `hitl` 且走到无 pending 的 HITL 步 → 必然 raise 退出（L197）。
5. **`__seed__` 是数据依赖不是排序依赖**：`_deps` 的 `if upstream in idset` 把它排除在拓扑之外。
6. **handler 每次循环新建**：L197 `else PauseResumeHitl()` 每次新建实例（轻微低效，无状态可忽略）。

---

## 12. 当前限制与演进方向

| 项 | 现状 | 演进 |
|---|---|---|
| DAG 并发 | 串行 topo | `asyncio.gather` 并行无依赖分支 + 全局并发闸 |
| skill registry | `execute` 预留 `skill_registry` 参数，未实现解析 | 注册表 `name -> AgentSpec` |
| 结构化 human 输入 | 自由文本 | `HumanSpec.output_schema` (pydantic) 校验 |
| trace 可观测 | 每步收了 trace，无聚合 | trace 聚合 + 导出 |
| 自定义 HITL | 协议已就位，仅 2 个内置实现 | WebhookHitl (推队列 + 等回调) |
| 多轮 session | `Step.session_id` 透传，未接 session store | session store 复用 SDK session |

---

## 附：文件职责速查

| 文件 | 职责 |
|---|---|
| `types.py` | 数据结构：Step / Flow / StepOutput / Checkpoint / HumanSpec / PendingApproval |
| `agents.py` | AgentSpec + AgentRunner（SDK query）+ MockRunner（离线） |
| `runtime.py` | 调度核心：topo_order / resolve_inputs / render_prompt / execute / HitlHandler |
| `example_flow.py` | 可跑示例：flow 定义 + drive 驱动 + CLI |
| `__init__.py` | 公开 API 导出 |
