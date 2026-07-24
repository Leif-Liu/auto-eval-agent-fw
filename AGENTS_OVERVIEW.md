# Agent 流程总览

> 项目里实现的几种 agent 调用流程，统一格式：**功能 / 接口 / 流程**。
> 详细设计见各模块 `ARCHITECTURE.md`，测试见 `code_reader_agent_testing.md` / `flow_engine_testing.md`。

## 1. code_reader_agent — 一次性读项目架构

### 功能
用 SDK MCP 工具（`list_files`/`read_file`/`search_code`/`get_project_architecture`）让 agent 自主读项目，输出架构总结。无 HITL（`bypassPermissions`），一次性跑完。

### 接口
- **SDK**：`claude_agent_sdk.query()`（顶层函数，一次性 agent run）
- **工具**：`create_sdk_mcp_server` 注册 4 个文件工具（in-process MCP）
- **配置**：`ClaudeAgentOptions(permission_mode="bypassPermissions", mcp_servers={...})`
- **入口**：`python -m src.code_reader_agent`（default）/ `python -m src.code_reader_agent mix`（分派子 agent）

### 流程
```
query(prompt, options)
  → spawn CLI 子进程 (stream-json over stdio)
  → CLI 跑 agent: 模型推理 → 调文件工具(自主, bypass) → 工具结果回模型 → 再推理
  → ResultMessage → 架构总结
  → CLI 子进程退出
```
agent 自主用工具读项目（`get_project_architecture` → `list_files` → `read_file`），多轮工具调用后给总结。mix 模式：`extra_agents` + Agent tool，主 agent 分派子 agent（runtime `code-reviewer` + 磁盘 `architecture-flow-analyzer`）。

---

## 2. flow_engine — 多 agent DAG + 步间 HITL + checkpoint

### 功能
多 agent/skill 按 DAG 编排协作完成 flow；**步间 HITL**（`gate`/`human` step）让人在 step 之间决策；每步原子 checkpoint，崩了能 resume。

### 接口
- **SDK**：`query()`（`AgentRunner`，每步一次性）
- **调度**：`execute(flow, ckpt, runner, hitl)`（`runtime.py`）
- **HITL**：`HitlHandler`（`InteractiveHitl` 实时 / `PauseResumeHitl` 退出+resume）
- **状态**：`Checkpoint`（`completed` + `pending`，原子写）
- **入口**：`python -m src.flow_engine.example_flow mock|real [--hitl interactive|pause] [--resume STEP DECISION]`

### 流程
```
execute(flow)
  for sid in topo_order(flow):
    if sid in ckpt.completed: continue          # resume 跳过已完成
    ctx = resolve_inputs(step.inputs, completed)  # ContextBus: "judge.output" 引用
    if step.kind in (human, gate):
      pending 有? pop 消费 : 调 hitl handler      # 步间 HITL
    else:
      runner.run(spec, prompt)                    # query() 跑 agent step
    ckpt.completed[sid] = out; ckpt.save()        # 每步原子存档
  return completed
```
步间 HITL：agent step 整步跑完（CLI 退出）后，在 flow 层插 `gate`/`human` step，runtime 自己交互（**不经过 CLI**）。崩溃/审批门 → checkpoint 暂停，`--resume` 跳过已完成、读 `pending` 决策继续。

---

## 3. interactive_runtime / InteractiveSession — 步内工具级 HITL

### 功能
agent 在一个 step 内执行时，每次调工具经 `can_use_tool` 上报到应用层问用户（审批/选方案/改写 input），**CLI 全程保持活着**。PreToolUse hook 强制 MCP/只读工具走 ask。

### 接口
- **SDK**：`ClaudeSDKClient`（双向，CLI 活着）+ `can_use_tool` 回调 + PreToolUse hook
- **handler**：`ToolApprovalHandler`（`TerminalApprovalHandler` 审批 / `TerminalChoiceHandler` 选方案 + `updated_input`）
- **hook**：`force_ask_all`（approve）/ `force_ask_propose_options`（choose）
- **工具**：`propose_options` MCP（提方案）
- **入口**：`python -m src.interactive_runtime.example approve|choose`

### 流程
```
async with InteractiveSession(spec, handler, ...) as s:   # __aenter__: ClaudeSDKClient + can_use_tool + hooks, CLI 活着
  final = await s.run(prompt)                              # query + receive_response
# __aexit__: disconnect, CLI 退出
```
工具调用时（CLI 活着）：
```
agent 调工具
  → PreToolUse hook 返回 "ask"                   # 强制走 can_use_tool（MCP/只读工具默认被 allow）
  → can_use_tool(handler) 被调                  # 应用层交互/改写
  → handler 返回 Allow(updated_input=...) / Deny
  → SDK 回写 control_response
  → CLI 用 updated_input 执行工具
  → 工具结果回模型 → 可能再推理 → ResultMessage
```

---

## 4. interactive_runtime / SkillsSession — 自主 + skills

### 功能
agent 自主跑（无 HITL）+ 加载项目 skill（`feature-dev-eval-agent-fw`）跑流程（七阶段）。CLI 活着（可流式/中断/多轮）。`bypassPermissions` 工具全放行。

### 接口
- **SDK**：`ClaudeSDKClient` + `permission_mode="bypassPermissions"` + `skills=`
- **无** `can_use_tool`、**无** hooks
- **入口**：`python -m src.interactive_runtime.example_skills "task" [--skill NAME|all]`

### 流程
```
async with SkillsSession(spec, trace=True) as s:    # __aenter__: ClaudeSDKClient + bypass + skills, CLI 活着
  final = await s.run(task)                          # query + receive_response
# __aexit__: disconnect
```
agent 自主用 skill 跑：
```
client.query(task)
  → 模型看 skill 列表 → 用 Skill 工具加载 SKILL.md
  → 按 skill 七阶段跑(Discovery→...→Implementation→...→Summary)
     每阶段: 推理 → 调工具(read/write/bash/Agent...) → 结果回模型 → 再推理
     (bypass: 工具全放行, 无审批)
  → ResultMessage → final
```
⚠️ **风险**：`Implementation` 阶段会真改代码（bypass 不审批）。安全验证：只读任务 / git 保护 / 换步内审批。

---

## 对比速查表

| agent | SDK 接口 | HITL 层面 | CLI 状态 | checkpoint | 典型用途 |
|---|---|---|---|---|---|
| code_reader_agent | `query()` | 无（bypass） | 一次性退出 | 无 | 读项目/给总结 |
| flow_engine | `query()` × 多步 | 步间（gate/human） | 每步退出 | ✅ 每步 | 多 agent DAG 编排 + 步间审批 |
| InteractiveSession | `ClaudeSDKClient` | 步内（can_use_tool） | 全程活着 | 无 | 工具调用时审批/选方案 |
| SkillsSession | `ClaudeSDKClient` | 无（bypass） | 全程活着 | 无 | 自主跑 skill 流程 |

## 选型指南

- **读项目 / 一次性总结** → `code_reader_agent`（query + bypass）
- **多 agent 按确定流程协作 + 步间审批 + 可恢复** → `flow_engine`（query + 步间 HITL + checkpoint）
- **agent 跑到一半调工具时要人审批/选方案** → `InteractiveSession`（ClaudeSDKClient + can_use_tool）
- **agent 自主跑 skill 流程（七阶段等）** → `SkillsSession`（ClaudeSDKClient + bypass + skills）

选型轴：要不要步间编排 / 要不要步内 HITL / 要不要 CLI 活着 / 要不要可恢复 / 要不要 skill。

## 详细文档

- code_reader_agent：`code_reader_agent_testing.md`
- flow_engine：`src/flow_engine/ARCHITECTURE.md` + `flow_engine_testing.md`
- interactive_runtime：`src/interactive_runtime/ARCHITECTURE.md`（§5 can_use_tool / §6 PreToolUse / §13 SkillsSession）
