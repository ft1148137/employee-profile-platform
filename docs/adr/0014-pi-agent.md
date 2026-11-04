# ADR-0014：Pi 调用边界、订阅凭据与会话并发

状态：采用（NERE-85）；2026-11-04。S7（NERE-90）、S7b（NERE-92）、S14（NERE-97）按本决策实现生产集成。本 PR 只有研究样例，不改变现有起草、设置或 LiteLLM。

## 调用方式与证据

选 `@mariozechner/pi-ai` + `@mariozechner/pi-agent-core`，另建 Compose 内网 Node 服务，无宿主端口。Python 保留身份、事务、证据过滤、排队、限频和凭据所有权；Node 只负责模型与 agent loop。每轮创建独立 Agent，不能用全局 Agent，也不能通过模型参数切换租户。

放弃 coding-agent RPC 和 coding-agent SDK：它们面向编程会话，引入工具、扩展、工作目录和登录文件管理；即使清空工具，也扩大需要审计的路径。只用 pi-ai 手写循环虽可行，但重复 agent-core 的工具校验、事件和取消逻辑。Python 每轮启动 CLI 会增加启动成本并丢失统一的并发控制，所以生产用常驻服务。

S7 的协议采用 HTTP + NDJSON：`POST /v1/turns`，请求包含协议版本、随机 run_id、模式、白名单模型配置、access token/API key、工具定义、已裁剪历史和本次输入；响应为 `text`、`tool_call`、`done` 或脱敏 `error`。内部服务密钥只从部署环境变量读取，所有路由认证，禁止访问日志记录请求体或 Authorization。Node 不接受数据库地址、员工凭据或任意回调 URL。

工具等待时，Python 在同一响应流读到 `tool_call` 后，向 `/v1/turns/{run_id}/results/{call_id}` 回传结果；Node 将它绑定到原请求，随机 ID 不替代内部认证，重复、未知、已取消和跨 run 的结果拒绝。只能回给创建 run 的后端调用方。Node 无后端访问令牌，不主动回调 Python。生产默认每轮最多 8 次模型调用、最多 16 次工具调用，超限返回可重试的明确错误，不执行多余工具。

总期限默认起草 120 秒、对话 60 秒，包含工具等待；上游断连、期限和显式 `DELETE /v1/turns/{run_id}` 都调用 `agent.abort()`，清除待回传结果并释放并发槽。Python 必须等待终止确认；失联时服务端期限兜底。取消不回滚已提交的业务事务，写工具沿用服务层幂等及 revision 校验。S7 要测超时、断连、重复结果和容量上限。

起草沿用 ADR-0013：Worker 先筛 active / standard 证据和 E12 员工、能力范围，用 R1、R2 替换证据 ID，以整数替换经验组；不发送员工邮箱、org_id、数据库主键。自由文本仍可能含身份信息，这不是自动脱敏承诺。Pi 只收到本次输入，只有 `submit_draft`，不连数据库、不读证据接口。

工具参数由 Pydantic 输出模型的 `model_json_schema()` 生成，Pi 校验后回传，Python 再严格校验能力 key、等级、R 引用、去重和长度。不能只信 Pi 的校验：其 validator 会做类型转换，错误还可能包含原始参数。工具错误必须替换为固定脱敏错误，不将异常正文直接返回员工或日志。输出继续走 `postprocess` 和 `write_initial_request`，产生 `pending_owner`，从不发布画像或改原始 Evidence。

## 工具与容器边界

agent-core 初始工具为空，只显式装载平台工具；依赖树没有 coding-agent，不读项目技能、扩展或 AGENTS.md。起草只装 `submit_draft`，对话只装员工工具定义与结果转发函数。不存在 bash、exec、read、write、edit、grep、find、ls 的执行函数；模型伪造这些名字时，agent-core 产生工具错误，不进入执行回调。不能把安全寄托在提示词上。

样例运行镜像为 distroless Node 22、非 root、无 shell；Compose 配置只读根文件系统、无挂载、移除全部 capabilities、no-new-privileges、PID 和内存限制。样例测试完全断网；它分别验证工具拒绝、只读文件系统和 shell 不存在。镜像构建阶段可以有 shell/npm，运行阶段没有。

这里保证的是模型没有文件或命令工具；Node 自身为加载程序仍可读镜像文件。只读文件系统不阻止读，非 root 也不是 Node 代码的完整沙箱，因此运行镜像内不能有业务文件、数据库密码或刷新令牌。生产只在开发模式按下面约定挂载 Codex 单个文件，不能挂整个 HOME。

生产需要访问模型，因此不能照搬样例的 `network_mode: none`。S7 对 base URL 做管理员配置和校验，拒绝重定向到未授权地址；部署出口需通过防火墙/代理只放行管理员允许的模型地址，订阅登录辅助路径另放行授权地址。本 PR 没有实现生产出网白名单；普通 Compose 的 internal 网络不能同时解决公网模型访问和按域名放行。S7 必须记录实际出口方案及 DNS/重定向测试，不能把 URL 校验宣称为网络隔离。Ollama 只放行明确配置的内部目标，不开放任意私网请求。

## ChatGPT 订阅与刷新所有权

直接使用 `streamSimple(model, context, {apiKey: accessToken, transport: 'sse'})`，或 Agent 的 `getApiKey` 返回 access token。Codex provider 从 access token 的 JWT 中提取 account id；不需要 refresh token。**推理路径不调用 `getOAuthApiKey`**：后者会自动刷新。假 fetch 测试证明直接调用只访问 responses，不访问授权/刷新地址，也不读取登录文件。

Pi 此版本的 `loginOpenAICodex` 是 OAuth authorization code + PKCE，不是设备码：监听 `localhost:1455/auth/callback`，支持 `onAuth` 展示链接和 `onManualCodeInput` 粘贴重定向 URL。服务器部署时不能假设管理员浏览器的 localhost 指向容器；S7b 用网页展示链接并收完整重定向 URL，经后端提交给专用登录辅助代码。要求校验完整 state，拒绝仅有 code 的输入（Pi 默认允许不带 state，平台需收紧），绑定发起管理员和企业、单次消费、15 分钟过期；不能暴露 Pi 的回调监听端口。

同一 Pi 辅助进程一次只做一个登录，避免固定回调端口冲突；其他管理员收到「已有连接流程，请稍后再试」，刷新不占此交互登录槽。S7b 要用模拟授权服务器验证链接、state、取消、过期和并发登录，真实网页登录尚未验收。若上游拒绝该服务器手工回传流程，S7b 在 issue 说明后再选设备码实现，不能退回容器刷新本机登录。

底层 OAuth 函数返回 `{access, refresh, expires, accountId}`，自身不保存文件；CLI 才管理登录文件。平台不用 CLI auth.json：API 与 Worker 共用企业 `model_setting` 中的 Fernet 加密凭据，保留 M2 前提，不复制到 Pi 卷。登录/刷新辅助路径与模型工具清单分开，模型不能调用它。

刷新由 Python 统一协调：在新短事务中 `SELECT ... FOR UPDATE` 锁住该 org 的模型设置，重新读取密文；剩余有效期不足 5 分钟时，将 refresh token 只交给 Pi 认证辅助路径调用 `refreshOpenAICodexToken`，立即将返回的整组轮换令牌加密提交。API、Worker、对话都走该函数；单进程锁不能替代数据库锁。刷新提交后释放锁，再发起模型请求，不能持有锁等待整轮模型或员工工具。

刷新请求 15 秒超时；已发送后结果丢失时不盲目重试旧 refresh token，标记需重连。断开、禁用和刷新必须用同一行锁，避免断开后刷新结果重新写回。认证辅助错误不能打印 upstream body（Pi 的错误可能含完整响应），只返回固定错误码；凭据不进入历史、工具参数、日志、错误信息或响应卡片。S7b 必测并发刷新仅一次、超时不重试、断开竞态和失败时脱敏。

本机测试可保留只读挂载方案：仅 `compose.chatgpt-local.yml`、仅 development，把 `~/.codex/auth.json` 只读挂载到 Pi 所需容器，不读取 `~/.claude`；容器只提取 access token、账号、expires，写入权限 0600 的临时文件（独立 tmpfs），不含 refresh token；传 `apiKey` 后删除临时文件。不足一小时有效期拒绝调用，请用户在本机正常刷新。绝不导入 OAuth 自动刷新、写回本机或落企业数据库。本 PR 只测合成 JWT，不读取本机授权。

有一个需要纠正的上游事实：Pi 0.73.1 **实际提供 Anthropic OAuth**，不能声称 Pi 不支持 Claude 订阅。按 E16，平台只允许 `openai-codex` 订阅；Anthropic 只收 API key，后端对 provider/credential kind 做封闭枚举校验，不暴露通用 OAuth registry。S7b 增加拒绝 Anthropic 订阅的接口测试；这是一项平台限制，不对上游能力或条款作推测。

## 提供方与推理强度

下表是锁定版本的配置映射，支持指工具调用模型的协议能力，不代表已用真实模型测通。模型 ID 由管理员选定，不使用浮动模型别名升级锁定包。

| 平台选项 | Pi provider / api | 地址与凭据 |
| --- | --- | --- |
| OpenAI | `openai` / `openai-responses` | 目录内模型，API key |
| ChatGPT 订阅 | `openai-codex` / `openai-codex-responses` | access token，强制 SSE，避免 WebSocket 会话缓存 |
| Anthropic | `anthropic` / `anthropic-messages` | API key，禁止订阅 |
| DeepSeek | `deepseek` / `openai-completions` | 目录内模型，API key |
| 通义千问 | 自定义 `qwen` / `openai-completions` | 此版无 alibaba provider；百炼区域 base URL，末尾 `/compatible-mode/v1`，API key |
| Kimi | `moonshotai` 或 `moonshotai-cn` / `openai-completions` | `https://api.moonshot.ai/v1` 或 `.cn/v1`；Coding 产品单列 `kimi-coding` / `anthropic-messages` |
| OpenRouter | `openrouter` / `openai-completions` | `https://openrouter.ai/api/v1`，API key |
| OpenAI 兼容地址 | 自定义 provider / `openai-completions` | 明确 model id、baseUrl、contextWindow、maxTokens、cost、reasoning、compat |
| 本地 Ollama | 自定义 `ollama` / `openai-completions` | 明确内网 `http://ollama:11434/v1`，仅工具模型；协议需要时传非敏感占位 key |

Agent 用 `thinkingLevel`：`off/minimal/low/medium/high/xhigh`；底层 `streamSimple` 用 `reasoning`，off 映射为不传。按模型的 reasoning 能力及 provider compat 开放档位，不支持的强度报中文错误，不静默当成已生效；例如 Kimi 部分目录模型 `supportsReasoningEffort=false`。S7 用假 HTTP 服务检查各选项的实际出站字段，S13 再做真实模型基线。

## 额度、错误与续跑

provider 通过流的 error 事件及最终 `AssistantMessage.stopReason=error/aborted`、`errorMessage` 返回失败；不能只捕获 JS throw，也不能把空输出算成功。Codex 的 `usage_limit_reached/usage_not_included/rate_limit_exceeded` 会转换成人类文字，resets_at 被四舍五入为分钟，结构化正文不保留。SSE 路径还会对部分 429/5xx 做最多三轮退避重试，必须计入总期限，不能再在外层无限叠加重试。

S7 在每次调用的 `onResponse` 捕获状态与允许的响应头（只选 Retry-After、配额 reset 头），不记录全部 headers；优先 Retry-After（秒数或 HTTP 日期），其次可解析 reset 头，再其次文字里的估计分钟，完全未知按 ADR-0011 估算 30 分钟并标 `estimated=true`。不能承诺从 Pi 取得原始 resets_at 或可靠区分 5 小时/每周窗口；若产品要精确时间，须在 S7b 给固定版本 SSE fetch 加请求级响应拦截，配套并发测试，不用进程全局替换 fetch。

Python 把统一的 quota error 映射回现有 `ModelRateLimited` 约定（实际异常名以服务代码为准），保留 ADR-0013 的事务回滚、已完成人员保留、剩余 pending、schedule_at 续跑和进程中断恢复。对话层返回中文稍后再试卡片及恢复时间是否估算；暂停期限以企业模型设置为键共享，避免每个员工重复撞额度。Pi 错误原文不直接进入 API 响应。

## 多人对话、隔离与调度

一个 Node 事件循环可以同时处理多个独立 Agent 的网络等待，不需要每员工一个进程。初始可逆默认：每进程最多 4 个在途 turn、每企业最多 2 个；这只是容量限制，不是吞吐承诺，S14 用假服务延迟压测再调整。每个 `(org, employee, session)` 同时只允许一轮，后端用数据库锁/租约和 revision 判定，不能仅靠 Node Map。

Python 负责跨进程调度：起草仍用 Procrastinate，聊天采用有界即时准入；预留 1 个 Node 槽给起草，起草队列非空时不接新聊天。只有 ChatGPT 订阅的企业，只要有起草待运行/运行中就暂停该企业新对话；不抢断已经执行的写工具。每员工每小时 30 轮由 Python 共享数据库计数，不能放浏览器或单进程内存。超载返回 busy，不在 Node 无限排队。

每轮请求重新携带后端裁剪过的历史，Node 完成/取消即删除上下文与 pending tool results；服务重启不保存会话。上下文、消息数组、工具执行闭包、缓存 sessionId 都不能跨员工复用，sessionId 用服务端不可预测 ID；订阅固定 SSE。样例在同一进程同时等两个 Python 工具，反序回传，断言两份 transcript 不含另一人的返回值。

会话业务状态归 Python 对话核心：按父规划，元数据与加密活动历史在数据库，默认闲置 2 小时删除，企业显式保留最长 30 天；执行工具不保存未确认的自述 Evidence。网页/未来 IM 均通过同一核心带入历史，多进程可恢复。该保留策略是父规划的默认假设，S14 要实现清理并注明，不能称聊天永久不落库。

Node 只有工具名、schema 和参数。Python 按已认证请求保存的 Principal 执行共享员工服务；忽略模型声称的 employee_id/org_id，schema 禁止这些字段。返回 Node 前再次限制结果范围。机器人工具表排除 `submit_request` 和 `withdraw_request`，写入只起草候选/预览；确认按钮带 revision 回到 Python，校验本人、企业和预览后调用服务，不能让模型代按确认。MCP 的直接提交权限不自动给机器人。

## 版本、许可证与复现

锁定 `@mariozechner/pi-ai@0.73.1`、`@mariozechner/pi-agent-core@0.73.1`，均 MIT；提交 `pi/package-lock.json`（105 个安装包全部有 integrity），用 `npm ci --ignore-scripts`，不安装 coding-agent。此命名空间已废弃、上游改为 `@earendil-works/*`；本次故意固定可在 Node 22 上复现的旧命名空间，不自动迁到 1.x，升级单开 PR 验证本 ADR 的协议与安全约束。

逐个检查锁文件对应包的 package.json：MIT 51、Apache-2.0 35、BSD-3-Clause 12、BSD-2-Clause 4、ISC 2、0BSD 1，无 GPL/AGPL 或未知许可证。CI 检查锁文件许可证白名单、完整性字段和没有 coding-agent；保持项目 Apache-2.0 与模板 MIT 声明。此结论针对 npm 依赖，不等同于整个部署镜像的法律审计。

Dependabot 给 `/pi` 单独配置 npm，忽略 `*`（含传递依赖），没有放宽现有 job。样例镜像在本机 Linux arm64 实测 `docker image inspect ... --format '{{.Size}}'` 为 67,145,640 字节（约 64.0 MiB，Docker 本地口径），并非其他架构的承诺；构建基础镜像与运行镜像均按多架构 digest 固定，升级走独立审查，不由 Dependabot 自动更新 Pi。

本地复现（根目录；Node 22、Python 3，仅标准库，不需要模型、数据库或授权）：

```bash
npm ci --prefix pi --ignore-scripts --no-audit --no-fund
npm test --prefix pi
npm run demo --prefix pi
COMPOSE_PROJECT_NAME=nere85 docker compose -f pi/compose.yml build
COMPOSE_PROJECT_NAME=nere85 docker compose -f pi/compose.yml run --rm sample
COMPOSE_PROJECT_NAME=nere85 docker compose -f pi/compose.yml down -v --rmi local --remove-orphans
```

样例用注入的 `streamFn` 生成 Pi 的标准消息流，真正运行 agent-core 校验和执行，不依赖第三方「假模型」服务或录制真实员工数据。Python `demo.py` 充当后端，在固定 Node 子进程里并发三轮，调用 `submit_draft` 和两个 `get_my_profile`。NDJSON 样例使用管道以省去 HTTP 服务、认证与凭据端点；生产 HTTP 路由、数据库 Principal、调度/限频和授权流程是 S7/S7b/S14 的交付，样例不宣称已验证它们。

`draftSchema` 是以下 Pydantic 输出的冻结快照；生产换成正式输出模型，不引入第二套业务 schema：

```python
class SampleDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)


SampleDraft.model_json_schema()
```

Node 测试覆盖未知工具拒绝、schema 拒绝、取消、合成 access token 无刷新及额度元数据丢失；Docker 另外证明非 root、根目录不可写与无 shell。并发 Python demo 是 CI 的独立步骤，不修改或跳过后端、前端既有检查。

## 实现（S7a，NERE-90）

`pi/server.mjs` 实现带 Bearer 认证的 HTTP/NDJSON 协议，健康检查为 `GET /health`。请求字段为 `version=1`、UUID `run_id`、`mode=draft`、`model`、`system_prompt`、字符串 `input`、工具数组 `tools`。`model` 使用 Pi 字段 `provider/api/id/baseUrl/contextWindow/maxTokens/compat/reasoning/thinkingLevel`，另带本次 `api_key`；工具使用 `name/description/parameters`（Python 的 JSON Schema）。不接受额外顶层或模型字段。

`tool_call` 事件带 `call_id/name/arguments`；结果 POST 路径中的 call_id 需 URL 编码，正文为 `{result, is_error}`。NDJSON `done` 带 `stopReason/model_calls/tool_calls`；`error` 只带固定 `code`，额度额外带 `retry_at/estimated`。DELETE 在 agent 终止、释放名额后确认，断连与期限同样 abort；`PI_DEADLINE_MS` 只允许将默认 120000 毫秒调短。工具执行顺序固定，参数失败与未知工具的原始错误在模型下一轮读取前替换成固定文字。模型密钥与服务认证密钥不进入历史、事件或访问日志。

`backend/app/domains/model/pi_client.py` 提供 `PiModelConfig`、`ToolDefinition` 和 `run_turn`，回调接收工具名和参数，返回 JSON 值。异常回传固定工具错误并 DELETE 等待终止；断流或客户端中断也取消，失联由服务期限兜底。客户端无代理环境继承、不跟重定向，读取期限 130 秒；服务每次模型调用关闭 SDK 自动重试，由调用层决定恢复策略。额度遵循前述四级优先级。

真实 429 集成测试发现，此固定版本 OpenAI/Anthropic SDK 在非 2xx 时抛异常，Pi 的 `onResponse` 不会触发。为保留 Retry-After，服务用 Node 内置 diagnostics_channel 观察原生 fetch 响应：AsyncLocalStorage 绑定本次调用的 onResponse，WeakMap 按 HTTP request 绑定 observer，只取状态和允许的头，调用同一回调；不替换全局 fetch、不读取错误响应正文、不修改锁定包。并发测试验证各 run 的额度头互不串用。

诊断订阅只在模块加载时注册；没有本轮 observer 的请求忽略，有多个响应时以最后一个为准。此机制依赖 Node 22 内置 undici 通道，与本文件记录的锁定运行镜像 digest 一起验证；升级 Node、运行镜像或 Pi 时必须重跑并发 429、无额度头回退和网络等待超时测试。

S7a 不切换 `chat_json`，不改数据库、画像和权限。S7c 接起草、封闭提供方、出网限制和设置页；S7b 接订阅；S14 加 chat 和调度。本 PR 保留研究样例、锁文件和原有 CI 步骤，无新增 npm/Python 依赖；新增服务由 Compose 内部认证健康检查。生产集成 pytest 启动真实 Node 服务与本地流式 OpenAI 兼容假服务，fixture 为 `tests/utils/pi.py`，后续任务复用。

## 实现（S7c，NERE-102）

API key 起草和连接测试使用 `run_turn`，订阅保留原路径至 S7b。设置的 `provider` 独立存储，`model` 只存模型 ID；未知旧前缀禁用并保留密钥，降级恢复前缀（Ollama 统一为旧版支持的 `ollama_chat/`）。封闭选项与上表一致，不开放目录提供方的地址编辑。

目录内模型用锁定包的 `getModel`，不允许请求覆盖能力字段；能力路由 `GET /v1/models/{provider}/{model_id}` 同样认证且不接收凭据。保存、连接测试和每批起草各查一次能力；不支持的推理档位不静默降级。显式禁用 reasoning effort 的目录模型只开放 off。自定义提供方显式设置 reasoning，xhigh 保持原值。Python 序列化用 `exclude_unset=True`，避免默认能力字段覆盖目录。

已知工具的 schema 拒绝仍保留 Pi 的固定错误，并在下一次模型调用前回传 Python 校验边界计数；未知工具永不回传执行。Python 对不合格提交返回固定拒绝值，第三次取消并转 `ModelOutputInvalid`，有合格提交后忽略重复提交；没有提交的结束轮次也计入三次上限。结果只进入 pending_owner。后端工具结果标记 `is_error=true` 表示回调异常，Pi 在确认该结果之前立即 abort，防止客户端随后 DELETE 之前抢跑下一次模型调用；普通校验拒绝仍以 `is_error=false` 的固定 JSON 返回并允许同轮重交。

`undici@7.30.1` 从现有锁文件提升为直接依赖（MIT），没有新增安装包。全局 dispatcher 与替换全局 fetch 不同：用 AsyncLocalStorage 绑定每轮策略，每次 dispatch 独立连接防止复用连接跳过 DNS 检查，连接 lookup 校验全部解析结果，拦截器把 3xx 转错误。目录模型拒绝私网，自定义模型限定配置 origin，所有模型拒绝 link-local 和元数据地址，无策略时拒绝。测试变量仅放开回环，不进入 Compose。此机制与 429 观察器依赖 Node/undici 接口，升级 Node 或 Pi 必须重跑并发额度、DNS、重定向和出口隔离测试；系统层出口限制见部署文档。

## 上游核对来源

- [agent-core 文档与独立 Agent 用法](https://github.com/badlogic/pi-mono/blob/v0.73.1/packages/agent/README.md)，[工具执行与校验源码](https://github.com/badlogic/pi-mono/blob/v0.73.1/packages/agent/src/agent-loop.ts)。
- [Codex OAuth 登录与刷新源码](https://github.com/badlogic/pi-mono/blob/v0.73.1/packages/ai/src/utils/oauth/openai-codex.ts)，[OAuth registry 与自动刷新入口](https://github.com/badlogic/pi-mono/blob/v0.73.1/packages/ai/src/utils/oauth/index.ts)。
- [Codex responses 的 access token、重试与错误处理](https://github.com/badlogic/pi-mono/blob/v0.73.1/packages/ai/src/providers/openai-codex-responses.ts)，[类型与 reasoning 定义](https://github.com/badlogic/pi-mono/blob/v0.73.1/packages/ai/src/types.ts)。
- [锁定模型目录](https://github.com/badlogic/pi-mono/blob/v0.73.1/packages/ai/src/models.generated.ts)，[百炼 OpenAI 兼容协议与区域地址](https://www.alibabacloud.com/help/en/model-studio/compatibility-of-openai-with-dashscope)。
- [npm 固定包元数据](https://registry.npmjs.org/@mariozechner/pi-ai/0.73.1)，[agent-core 元数据](https://registry.npmjs.org/@mariozechner/pi-agent-core/0.73.1)，[Pi MIT 许可证](https://github.com/badlogic/pi-mono/blob/v0.73.1/LICENSE)。

## 实现（S14a，NERE-97）

S14a 只交付运行底座；员工工具、提示词、卡片、确认动作和对话 HTTP 入口由 S14b（NERE-106）接入。没有新增依赖。企业设置没有可复用的通用设置表，新增每企业一行的 `bot_setting`，管理员通过 `GET/PUT /orgs/{org_id}/bot-settings` 读写。指南上限 4000 字，保留天数 0–30，缺行时返回空指南及 0 天。

`bot_session` 使用 UUID v4，只有员工、入口、时间、轮数、请求 UUID 数组和租约；`bot_message` 通过带企业的复合外键关联会话，级联删除只涉及聊天消息，不涉及 Evidence。消息正文为 `core/secrets.py` Fernet 加密的 JSON；密钥仍从部署环境变量读取，不新增密钥来源。迁移降级会删除聊天数据和机器人设置，保留既有业务表。

给 S14b 的 Python 接口：

- `store.create_session(db, principal, channel="web")` 提交会话元数据；`get_session(db, principal, session_id)` 和 `list_messages` 只允许本企业本人，其他员工及企业一律 404，沿用 `visible_employee`。
- `append_message(db, session, role, payload)` 接收不透明 JSON，写密文并 flush，更新最后活动时间；提交事务由调用方负责。`list_messages` 返回 `id/role/payload/created_at`，不返回密文。
- `load_history(db, session)` 返回 `HistoryMessage(role, text)`，text 是原 JSON 文本；只读取最近 20 条，合计最多 24000 字，超长时保留最近文本尾部。入口应在 `begin_turn` 写入本次用户消息之前取历史，再将当前输入单独交给 Pi。内部接受 session 的函数只使用经过 `get_session` 校验的会话。
- 产生请求后，由 S14b 校验请求归属，并给 `session.request_ids` 赋新数组保存；该字段不承担授权。

默认闲置两小时的会话不再通过读取接口暴露旧消息；再次追加时先清除过期历史，避免重新活跃后恢复旧内容。`bot.cleanup_messages` 每五分钟清理一次密文：0 天按会话闲置两小时删除全部消息，正数按每条消息创建时间删除。元数据保留。没有异步外发或原文日志。

`admission.begin_turn(db, principal, session, user_payload)` 是上下文管理器：企业级事务 advisory lock 串行检查，随后按本人会话行 `FOR UPDATE`；取得 75 秒租约、按用户消息统计最近一小时的 30 轮限制、检查企业 2 轮上限，写入用户消息和轮数后一起提交。网络等待期间不持行锁。上下文正常退出提交调用方写入，异常回滚本轮尚未提交的写入；用户输入及轮数已在准入时提交，不因模型失败退回计数。退出按租约时间匹配释放，过期轮次不能释放接管者的租约。

拒绝类型为 `BotBusy`、`BotRateLimited(retry_at)`、`BotDraftingPriority`、`BotQuotaPaused(retry_at, estimated)`。查询现有 `procrastinate_jobs` 的函数从起草服务抽出，起草流程不变：本企业订阅模型遇到排队或运行中的起草时优先返回 `BotDraftingPriority`；部署存在排队起草时所有新聊天返回 `BotBusy`。API key 企业可与运行中的起草共存。模型返回 PiBusy 时转 BotBusy；额度错误持久化到企业模型设置，下一轮立即拒绝，未知恢复时间估算 30 分钟，已有更晚暂停不缩短。没有重试。

Pi 请求允许 `mode="chat"` 及可选 `history=[{role,text}]`；历史只允许 user/assistant、最多 20 条和 24000 字，draft 携带 history 一律拒绝。每轮仍是全新 Agent，完成后清空，不跨 run 缓存。对话服务期限 60 秒（含后端工具等待），`PI_CHAT_DEADLINE_MS` 只可调短；Python 读取超时为 70 秒，draft 仍为 130 秒。Node 每进程总上限 4 轮，chat 上限 3 轮，超出立即 busy，始终留一个名额给起草。

合成延迟负载验证（本机 Mac，不能据此推算生产吞吐）：Node 8 批、工具等待 30ms，接受 32 轮（24 聊天、8 起草），拒绝 16 轮，耗时 318ms；真实 PostgreSQL 连接 8 批、两名员工各占一轮并等待 30ms，接受 16 轮、拒绝 16 次重复或超额请求，耗时 604ms。小时限制另测 30 轮成功、第 31 轮拒绝，以及剩一轮额度时两个连接争抢只通过一个。并发反序工具回传不串历史；伪造命令和文件工具无法执行；缩短期限后超时释放名额。保留 4 / 2 / 30 默认值，不增加配置项。

## 实现（S7b，NERE-92）

认证辅助路径与 turn 使用同一个内部 Bearer 密钥，封闭为 `/v1/auth/openai-codex/logins` 的 POST 开始、`/{login_id}` 的 POST 回传和 DELETE 取消。Pi 进程只有一个登录槽，15 分钟到期拒绝等待的手工输入；请求只记录固定路由名、状态和耗时，错误只返回 `login_busy/login_not_found/login_failed`。登录使用锁定包的 `loginOpenAICodex`，授权请求出网限定 `https://auth.openai.com`，取消信号中止正在交换令牌的请求。1455 仅监听容器内部回环，不发布。

Python 在企业行锁内保存加密的 login_id、URL、state 和管理员 ID；回传只收完整的 `http://localhost:1455/auth/callback` URL，code 和 state 必须各一份，state 必须相同。平台只在 Python 校验一次，Pi 原样转交上游库。其他管理员不能读取链接或消费该流程，其他企业拿不到记录。本人回传无论成功失败都消费记录。Pi 重启后回传提示重新开始。

凭据保存为加密的 `{access, refresh, expires, accountId}`，expires 为 Pi 的 Unix 毫秒；旧格式和密钥更换不迁移，提示重新连接。API/Worker 经 `chatgpt.access_token` 的独立短事务取 access，释放锁后才运行 turn；模型配置带仅供 Python 使用的企业 ID，不序列化给 Pi。401 标记需要重新连接，推理只给 access token、固定 SSE，不调用 OAuth 自动刷新接口。新配置默认选择锁定目录支持的 `gpt-5.4`，保留已有模型字段不做迁移。

刷新和本机只读挂载另由 NERE-104 交付；在它合并前，距离过期不足 5 分钟即要求重新连接，开发用挂载文件已删除，不能使用本机登录。原模型 SDK、设备码和临时令牌文件路径已删除，无新增依赖。额度沿用 Pi 的统一映射与起草续跑。

## 实现（S7b2，NERE-104）

`POST /v1/auth/openai-codex/refresh` 只收 `{refresh}`，使用内部 Bearer 密钥，不是模型工具。Pi 调用锁定版本的 `refreshOpenAICodexToken` 一次，出口只允许授权站点，14 秒中止网络请求；错误统一为 `refresh_failed`，不返回上游正文。Python 保留 15 秒请求超时。

API、Worker 和对话继续共用 `chatgpt.access_token`：独立事务取得企业模型设置行锁、重读密文，剩余不足 5 分钟才刷新；整组轮换令牌校验后加密提交，释放锁后才推理。保存设置复用登录/断开的锁函数，禁用、断开不能与刷新交错写回。已发出但超时、断线、结果无效或明确拒绝均持久化 `needs_relogin`，后续调用不再发送旧 refresh token；管理员轮询状态本身不会把临期令牌标为失效。

开发覆盖文件 `compose.chatgpt-local.yml` 仅把单个授权文件只读挂载给 Pi，另建 Pi 独立 tmpfs；后端/Worker 只接收 `CHATGPT_LOCAL_AUTH=true` 开关，不接收文件或凭据。Python 和 Pi 都检查 development 模式。此模式下 ChatGPT 调用使用本机授权，企业启用/禁用仍生效，不读写企业订阅凭据。Pi 每轮读取 access JWT 的账号与过期时间，拒绝有效期不足一小时；只把 `{access, accountId, expires}` 写入 0600 临时文件，按轮隔离，完成、异常或取消后删除。推理仍直接传 access，不经过 OAuth 自动刷新。没有新增依赖、迁移、证据或权限协议改动。
