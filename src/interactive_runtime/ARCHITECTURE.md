# interactive_runtime 架构与代码流程

> 步内工具级 HITL runtime：agent 在一个 session 内执行时，每次调工具经 `can_use_tool` 上报到应用层问用户，CLI 全程保持活着。
>
> 和 `../flow_engine/ARCHITECTURE.md`（步间 HITL）互补。阅读顺序：先看 §1–§3 建立整体观，再读 §5（can_use_tool 机制）和 §6（PreToolUse hook）理解细节。

## 1. 这个 runtime 是什么

`src/interactive_runtime/` 是独立 runtime，专门做**步内工具级 HITL**，基于 `ClaudeSDKClient`（不是 `query()`）。

解决一个问题：

> `flow_engine` 的步间 HITL 发生在 agent step 整步返回之后（CLI 已退出），满足不了"agent 跑到一半、想调工具时让人审批/选方案"。步内 HITL 需要 CLI 在 agent 执行期间保持活着，把每次工具调用上报到应用层。

核心机制：`ClaudeSDKClient` + `can_use_tool` 回调 + PreToolUse hook。CLI 从 connect 到 disconnect 保持活着；工具调用时经 stdio 控制协议上报 → 应用层回调 → 决策返回 → CLI 继续。

## 2. 步间 vs 步内 HITL（和 flow_engine 对比）

| 维度 | flow_engine 步间 HITL | interactive_runtime 步内 HITL |
|---|---|---|
| 触发位置 | 两个 step 之间 | 一个 step 内部，agent 调工具时 |
| CLI 状态 | 已退出（`query()` 一次性） | 保持活着（`ClaudeSDKClient`） |
| 谁触发 | flow 调度器（确定性） | agent 自己（动态） |
| 上报路径 | 不经 CLI — runtime 层 `input()` | CLI → SDK 控制协议 → 应用层回调 → 返回 CLI |
| SDK 接口 | 不用 SDK 权限机制 | `can_use_tool` 回调 + PreToolUse hook |
| 典型场景 | 审批整个 step 结果 | 审批每个工具调用 / 让用户从方案选 |

## 3. 分层架构

```
┌──────────────────────────────────────────────────┐
│  InteractiveSession    src/interactive_runtime     │
│   ClaudeSDKClient 封装 + can_use_tool 注入          │
├──────────────────────────────────────────────────┤
│  Claude Agent SDK      ClaudeSDKClient            │
│   connect/query/receive_response + hooks          │
├──────────────────────────────────────────────────┤
│  Claude Code CLI       agent loop (保持活着)        │
│   工具调用 → control_request 上报 → 等决策 → 继续    │
└──────────────────────────────────────────────────┘
```

## 4. 组件地图

```
example.py
  ├─ approve: InteractiveSession(spec, TerminalApprovalHandler(), force_ask_all=True)
  └─ choose:  InteractiveSession(spec, TerminalChoiceHandler())   # include_propose_tool=True
                       │
                       ▼
session.py: InteractiveSession
  ├─ __aenter__: ClaudeSDKClient(options) + connect(None)
  │              options: permission_mode=default, can_use_tool=handler, hooks=...
  ├─ run: client.query(prompt) + receive_response()
  └─ __aexit__: disconnect (CLI 退出)
                       │
          ┌────────────┴───────────┐
          ▼                        ▼
   handlers.py                tools.py
   ├─ ToolApprovalHandler 协议  ├─ propose_options (@tool MCP)
   ├─ TerminalApprovalHandler   └─ build_mcp_server
   ├─ TerminalChoiceHandler
   ├─ force_ask_propose_options  (PreToolUse hook, choose 用)
   └─ force_ask_all             (PreToolUse hook, approve 用)
```

## 5. `can_use_tool` 机制（核心）

### 回调签名（SDK `types.py:254-256`）

```python
CanUseTool = Callable[[str, dict, ToolPermissionContext], Awaitable[PermissionResult]]
```

参数：`tool_name`, `tool_input`, `ctx: ToolPermissionContext`（带 `title`/`display_name`/`description`/`tool_use_id` 等 UI 文案，`types.py:198-230`）。

返回（`types.py:234-252`）：
- `PermissionResultAllow(behavior="allow", updated_input=None, updated_permissions=None)` — `updated_input` **改写工具输入**
- `PermissionResultDeny(behavior="deny", message="", interrupt=False)` — `message` 回传 agent

### CLI 保持活着的生命周期

```python
async with InteractiveSession(spec, handler) as s:   # __aenter__: ClaudeSDKClient + connect(None)
    text = await s.run(prompt)                        # client.query(str) + receive_response()
# __aexit__: disconnect → CLI 子进程退出
```

- `connect(None)` 走空 stream（`client.py:108-118`），CLI 启动但等消息
- `client.query(str)` 发 prompt（`client.py:287-315`）
- `receive_response()` 流式收消息，到 `ResultMessage` 自动结束（`client.py:571-610`）

### 控制协议往返（工具调用时的上报链路）

```
agent 想调工具
  → CLI 经 stdio 发 control_request(can_use_tool) 给 SDK
  → SDK 在独立 child task 里 await 你的 handler(tool_name, input, ctx)
  → handler 返回 Allow / Deny / Allow(updated_input=...)
  → SDK 经 control_response 把决策写回 CLI
  → CLI 用 updated_input 执行工具（或跳过 if deny）
```

回调在独立 child task（SDK `_internal/query.py:247-285`），消费方 `receive_response` 自然暂停等决策——不需要你自己加阻塞。

### 关键约束

- `can_use_tool` **只在权限规则判到 "ask" 时触发**（`types.py:1895-1911`）。被 `allowed_tools`/`permission_mode`/settings allow 规则放行的调用不进回调。
- `permission_mode` 必须 `default`，不能 `bypassPermissions`（完全绕过，`types.py:1662-1673`）。

## 6. PreToolUse hook：让工具真正进回调（踩坑核心）

### 问题：哪些工具不进回调

`default` 模式下被自动 allow、不进 `can_use_tool` 的工具：

1. **自定义 MCP 工具**（如 `propose_options`）— CLI 默认放行 MCP 工具
2. **只读工具**（Glob/Grep/Read）— 无副作用，默认放行
3. **只读 Bash 命令**（`find`/`ls`/`cat`）— Claude Code 按命令风险分类，只读放行

所以"步内 HITL"**不会自动触发**——必须靠 PreToolUse hook 强制。

### 修复：PreToolUse hook 返回 "ask"

SDK 机制（`types.py:220`）：PreToolUse hook 返回 `permissionDecision="ask"` 会**强制把该工具调用路由到 `can_use_tool`**。

两个 hook（`handlers.py`）：

- `force_ask_propose_options`（L156-171）— 只对 `propose_options` 返回 ask（choose 场景）
- `force_ask_all`（L184-203）— 对所有工具返回 ask（approve 场景）

由 `InteractiveSession` 的 `force_ask_all` / `include_propose_tool` 参数选（`session.py:94-97`）：

| 参数 | hook | 场景 |
|---|---|---|
| `force_ask_all=True` | `build_force_ask_hooks()` | approve — 所有工具走 ask |
| `include_propose_tool=True` | `build_propose_hooks()` | choose — 只 propose_options 走 ask |

### hook 返回格式（关键，踩过坑）

```python
{
    "hookSpecificOutput": {              # ← 必须嵌在 hookSpecificOutput 里!
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": "...",
    }
}
```

`permissionDecision` 必须嵌在 `hookSpecificOutput` 字段里（SDK `types.py:558`），**不是顶层**。顶层格式 CLI 不认，`ask` 不生效。

## 7. 三个踩坑（关键经验）

| # | 坑 | 现象 | 修复 |
|---|---|---|---|
| 1 | hook 返回顶层格式 | hook 触发了，但 `permissionDecision` 不生效，工具仍自动 allow | 包在 `hookSpecificOutput` 里（`types.py:558`） |
| 2 | MCP 工具名带 `mcp__` 前缀 | `tool_name == "propose_options"` 不匹配（实际是 `mcp__interactive-tools__propose_options`） | 用 `contains` 匹配（`"propose_options" in tool_name`） |
| 3 | 只读工具/MCP 自动 allow | `bash find` / Glob 不进回调，handler 永不触发 | PreToolUse hook 强制 ask（`force_ask_all` / `force_ask_propose_options`） |

## 8. 两个场景端到端

### approve（权限批准）

```
example.approve → InteractiveSession(spec, TerminalApprovalHandler(), force_ask_all=True)
  │
  ├─ __aenter__: ClaudeSDKClient + hooks=build_force_ask_hooks()
  ├─ run(prompt):
  │    agent 调 Bash(find ...)
  │      → PreToolUse hook(force_ask_all) 返回 ask
  │      → can_use_tool(TerminalApprovalHandler)
  │      → print [approve] + allow? (y/N) >    ← 用户交互
  │      → y → PermissionResultAllow()
  │      → CLI 执行 bash → 结果
  │    → ResultMessage → final text
  └─ __aexit__: disconnect
```

实测：`allow? (y/N) > y` → bash 执行 → "34 Python files"。

### choose（方案选择）

```
example.choose → InteractiveSession(spec, TerminalChoiceHandler())  # include_propose_tool=True
  __aenter__: ClaudeSDKClient + mcp_servers={propose_options} + hooks=build_propose_hooks()
  run(prompt)  —— 时序展开 ↓
    client.query(prompt)         CHOOSE_SPEC系统提示词+run_choose用户提示词，发任务给 CLI
      │
      ▼  ① 模型第 1 次推理(query)
    CLI 跑大模型 → 决定调 propose_options                            ← assistant message包含tool_use
                   生成 tool_use: input={"options":["A","B","C"]}   ← options 就在第1次推理 生成
      │
      ▼  ② CLI 执行工具前，先通过hooks, 内嵌了 PreToolUse hook，所以执行工具前先执行PreToolUse hook
    force_ask_propose_options(tool_name="mcp__...__propose_options")   [PreToolUse hook, matcher=None]
      → 返回 {hookSpecificOutput: { "hookEventName": "PreToolUse"
                                    permissionDecision: "ask",          ← 强制走 can_use_tool （因为是"ask"）
                                    "permissionDecisionReason": "..."}}
      │  ③ can_use_tool 被触发(是因为PreToolUse 返回 "ask")，在独立 child task 里 await
      ▼
    TerminalChoiceHandler(tool_name, tool_input={"options":["A","B","C"]}, ctx) 被调    ← Hook到can_use_tool
      ← 此前模型已生成 options; 收到的是模型 ① 生成的 input(原封传过来)
      → print "[choose] 1.A 2.B 3.C"
      → input() 用户敲 "2"                                     ← ★ 交互在这 ★
      → 返回 PermissionResultAllow(
            updated_input={"options":["B"], "selected":"B"}    ← ★ selected 在这注入 ★
        )
      │  ④ SDK 把 updated_input 写回 CLI(control_response, _internal/query.py:414-428 转 dict, 475-483 transport.write)
      ▼  ⑤ CLI 用 updated_input（非原始 input）执行工具，针对用户的选择作出summary
    propose_options(args={"options":["B"], "selected":"B"})     ← 收到的是交互后改写的!
      → selected = args.get("selected") → "B"                   (tools.py:40)
      → return {"content":[{"type":"text","text":"User selected: B"}]}   (回 agent)
      │  ⑥ 工具结果回模型 → 模型可能再推理(query) → ... → ResultMessage    ← 根据tool返回的结果，做第二次大模型推理
      ▼
    final text
  __aexit__: disconnect
```

**关键**：

- **options 是模型 ① query 生成的**——`TerminalChoiceHandler` 在 ③ 被调时，模型已经跑过一次推理、生成了带三个 options 的 tool_use；回调收到的是模型生成的 input（原封传过来）。
- **`can_use_tool` 的位置在"模型决策后、工具执行前"**（②→③→⑤）——所以它能拿到模型生成的 input 并改写。
- **`selected` 不是 `propose_options` 工具自己交互拿的**——工具是"哑"的 echo（`tools.py:33-46`）；交互在 ③ `TerminalChoiceHandler` 里，选择通过 `updated_input` 注入，CLI 用改写后的 input 执行工具（⑤），工具才在 `args.get("selected")` 读到。这就是 `can_use_tool` 比"allow/deny"更强的地方——它能**改写**工具输入。
- **模型可能多轮**：工具结果 ⑥ 回模型后，模型可能再推理（"用户选了 B，我来总结"）直到 `ResultMessage`；`TerminalChoiceHandler` 只在 propose_options 这次工具调用前触发一次。

实测：`pick 1-3 > 2` → 选中方案回传 agent → agent 基于选中总结。

## 9. 关键 SDK 事实（`claude_agent_sdk` 0.2.123 源码核实）

| 关注点 | 文件:行号 |
|---|---|
| `CanUseTool` 回调签名 | `types.py:254-256` |
| `PermissionResultAllow`/`Deny` | `types.py:234-252` |
| `ToolPermissionContext`（UI 文案） | `types.py:198-230` |
| `can_use_tool` 触发条件（"ask"） | `types.py:1895-1911` |
| `permission_mode` 取值 | `types.py:1776-1784` |
| PreToolUse "ask" 触发回调 | `types.py:220` |
| MCP/只读工具自动 allow（shadow） | `types.py:1662-1673` |
| `PreToolUseHookSpecificOutput` 字段 | `types.py:413-422` |
| `hookSpecificOutput` wrapper | `types.py:558` |
| `HookMatcher`（matcher/hooks/timeout） | `types.py:586-599` |
| `HookCallback` 签名 | `types.py:574-581` |
| `connect(None)` + `_empty_stream` | `client.py:108-118` |
| can_use_tool 校验（禁 str prompt） | `client.py:161-180` |
| `client.query(str)` | `client.py:287-315` |
| `receive_response`（到 ResultMessage 结束） | `client.py:571-610` |
| `__aenter__` 默认 connect() | `client.py:623-626` |
| 回调时序（独立 child task） | `_internal/query.py:247-285` |
| `updatedInput` 回写 CLI | `_internal/query.py:414-428, 475-483` |
| `@tool` / `create_sdk_mcp_server` | `__init__.py:170-236, 311-524` |
| 项目内 MCP 工具参考 | `src/code_reader_agent.py` |

## 10. 验证

### 运行时验证（两场景已实测通）

```bash
python -m src.interactive_runtime.example approve    # 权限批准: bash 调用弹 allow? (y/N)
python -m src.interactive_runtime.example choose     # 方案选择: propose_options 弹 pick 1-3
```

需 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_MODEL`（`example.py` 加了 `load_dotenv()` 读 `.env`）。

### 调试

`InteractiveSession(..., trace=True)` 打印每个 `tool_use` 到 stderr（看 agent 实际调什么工具）。

## 11. 演进方向

- **自定义 handler**（Web/IM）：`ToolApprovalHandler` 协议已就位，实现 `__call__` 即可
- **拦截更多工具**：扩展 `force_ask_propose_options` 的匹配条件，或写新的 force-ask hook
- **和 flow_engine 集成**：flow_engine 的某个 agent step 用 `InteractiveSession` 作为 runner（步间 + 步内 HITL 混用）
- **结构化审批输入**：`PermissionResultAllow.updated_input` 配合 pydantic schema 强制决策结构
- **运行中打断**：`ClaudeSDKClient.interrupt()`（`client.py:317`）支持运行中打断 agent
