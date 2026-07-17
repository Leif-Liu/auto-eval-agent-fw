# code_reader_agent 测试方法与结果示例

`src/code_reader_agent.py` 提供两个运行模式，通过位置参数切换：

| 模式 | 命令 | 场景 |
|---|---|---|
| `default` | `python -m src.code_reader_agent` | 默认：单 agent + SDK MCP 工具读项目架构 |
| `mix` | `python -m src.code_reader_agent mix` | 混合：runtime `code-reviewer` + 磁盘 `architecture-flow-analyzer` 同时 dispatch |

前置条件：`.env` 配好 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_MODEL`（Aliyun MaaS 网关用小写 `glm-5.2`）。

---

## 模式一：default

### 命令

```bash
python -m src.code_reader_agent
```

### 通过判据

- 末尾出现 `ResultMessage subtype=success is_error=False`
- `num_turns` > 0，`duration_ms` 合理（GLM 单轮几秒到几十秒）
- 最终打印架构总结文本（带 file:line 引用）

### 实测输出（节选）

```
[code-reader-agent] model=glm-5.2
[code-reader-agent] base_url=https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic
[code-reader-agent] mode=default
------------------------------------------------------------
[turn 001] >>> SystemMessage          subtype=init          ← CLI 握手
[turn 015] >>> AssistantMessage       · thinking            ← 模型推理
[turn 016] >>> AssistantMessage       · tool_use: mcp__code-reader__get_project_architecture
[turn 017] >>> UserMessage            · tool_result         ← arch.md 内容
[turn 201] >>> AssistantMessage       · text                ← 最终总结
[turn 202] >>> ResultMessage          subtype=success num_turns=10 duration_ms=41614 is_error=False
------------------------------------------------------------
## Architecture Summary
**1. Module layout & responsibilities.** `src/cli.py` (Click) is the entry point ...
```

> 注：`[turn N]` 是 SDK yield 的消息序号（含 `thinking_tokens` 噪声，GLM 每秒吐几条）；`num_turns=10` 才是逻辑轮次。

---

## 模式二：mix（混合 runtime + 磁盘 agent）

### 命令

```bash
python -m src.code_reader_agent mix
```

### 通过判据（3 条全满足 = pass）

1. **两个不同的 `subagent_type`** 出现在 `tool_use: Agent` 块里：
   - `code-reviewer`（runtime，来自 `extra_agents`）
   - `architecture-flow-analyzer`（磁盘，来自 `.claude/agents/`，`setting_sources` 默认 None 自动加载）
2. **两个 `subtype=task_started`**，各自独立 `task_id`
3. **末尾 `subtype=success is_error=False`** ×2-3（两个子代理各自收尾 + 主代理收尾）

### 失败模式

| 现象 | 原因 | 修复 |
|---|---|---|
| 只有 `code-reviewer`，没有 `architecture-flow-analyzer` | 磁盘 agent 没加载 | 检查 `.claude/agents/architecture-flow-analyzer.md` 存在且 frontmatter 合法 |
| `subtype=error` 或 `is_error=True` | model 名被网关拒 | 磁盘 agent frontmatter `model: sonnet` 在 Aliyun 网关 400；改成 `model: inherit` |
| `code-reviewer` 报 model 错 | runtime agent 没设 `model="inherit"` | `AgentDefinition(..., model="inherit")` |
| 超时（300s 无 success） | GLM 慢或网关抖动 | 调大 timeout 或重试 |

### 实测输出（节选，过滤了 `thinking_tokens` 噪声）

```
[code-reader-agent] model=glm-5.2
[code-reader-agent] base_url=https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic
[code-reader-agent] mode=mix
------------------------------------------------------------
          · tool_use: Agent id=toolu_a0ef3d7d... input={'subagent_type': 'code-reviewer', 'description': 'Review src/cli.py for findings', ...}
          · subtype=task_started  task_id=a6d9c703...  subagent_type='code-reviewer'
          · task_progress "Reading src/cli.py"            subagent_type='code-reviewer'
          · tool_use: Agent id=toolu_00f07e19... input={'subagent_type': 'architecture-flow-analyzer', 'description': 'List 5 methodology phases', ...}
          · subtype=task_started  task_id=ad981a48...  subagent_type='architecture-flow-analyzer'
          · task_progress ×N                                    subagent_type='architecture-flow-analyzer'
          · subtype=success  num_turns=3  duration_ms=10084  is_error=False   ← code-reviewer 收尾
          · subtype=success  num_turns=1  duration_ms=1992   is_error=False   ← architecture-flow-analyzer 收尾
          · subtype=success  num_turns=1  duration_ms=8051   is_error=False   ← 主代理收尾
------------------------------------------------------------
=== FINAL ===
<主代理对两个子代理输出的综合总结>
```

### 只看 dispatch 信号（过滤噪声）

```bash
python -m src.code_reader_agent mix 2>&1 | grep -E 'tool_use: Agent|subagent_type|subtype=task_started|subtype=success|subtype=error' | head -20
```

> 注意：`head -20` 会在 20 行后 SIGPIPE 截断管道，子代理可能没跑完。完整跑通验证 success 标志时不要带 `head`。

---

## 关键设计点

- **`extra_agents` 默认 `None`**：`_build_options(extra_agents=None)` 不传 `allowed_tools` 和 `agents` 键给 `ClaudeAgentOptions`，default 模式行为字节级不变。
- **`model="inherit"`**：runtime `code-reviewer` 和磁盘 `architecture-flow-analyzer` 都用 `inherit`，继承父进程的 `glm-5.2`，避开 Aliyun 网关对非 `glm-5.2` model 名的 400。
- **`allowed_tools=["Agent"]`** 仅在 `extra_agents` 非空时加：default 模式工具集最小化，mix 模式才允许 dispatch 子代理。
- **磁盘 agent 不在 `extra_agents` 里重复声明**：`setting_sources` 默认 None = CLI 自动扫 `.claude/agents/`，同名时程序式覆盖磁盘（`flagSettings` > `projectSettings`）。
