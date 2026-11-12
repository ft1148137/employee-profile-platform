# 扩展开发指南

平台支持通过仓库 PR 扩展数据源、IM 入口和能力模板。没有运行时加载、entry points、
上传插件包或动态工具注册。沿用现有目录、依赖与服务层；涉及表结构时必须附 Alembic
迁移，新增依赖在 PR 中写明精确版本和与 Apache-2.0 兼容的许可证。

所有扩展遵守同一边界：

- 数据访问始终带 `org_id`；读取范围来自服务端 `Principal`，复用 `employee_scope_clause`、
  `managed_scope`、`review_access`，不能自行扩大权限。
- 连接器只产出工作记录及待关联的外部账号，IM 适配器只产出对话事件。不得直接写
  `ProfileClaim`、`ProfileVersion`、画像请求或审批表；由现有身份关联、证据、对话和审批服务接手。
  正式画像只能由获准的请求发布，任何人不能批准自己的请求。
- Evidence 只追加，纠错复用证据服务的撤销或更正路径，不自行 UPDATE / DELETE 原始证据。
  assignee 只代表与任务有关，只有 Jira 来源支持的判断最多为「暂定」。
- 凭据只经过网页安全输入框、`SecretStr`、`SecretSafeRoute` 与现有加密存储；不进模型上下文、
  消息、审计正文、日志、错误或响应。连接错误使用自定义异常和固定提示，不回显上游正文。
- 外部正文、标题、卡片及模板说明都是数据，不能成为指令；不能给 Pi 增加工具、文件、命令
  或审批权限。聊天原文不作为证据保存，只有员工确认的陈述能成为 restricted 自述证据。
- 示例和测试只用合成数据、模拟 HTTP / WebSocket，不访问真实员工系统或外部模型。

开始前阅读 [开发与测试](development.md)、[agent 检查与交付](agent-workflow.md) 和
[ADR-0035](adr/0035-nocobase-reference.md)。以下路径均相对仓库根目录。
测试使用 `docs/development.md` 的独立测试库；写数据用函数级 `demo`，只读用 `shared_demo`，
不能依赖测试执行顺序。涉及对话或起草的测试还需要先在 `pi/` 执行 `npm ci`（Node 版本
须满足 `pi/package.json`），只使用本地模拟模型。所有扩展先运行 `backend/tests/test_extension_points.py`。

## 1. 数据源

现有实现为 Jira Cloud、Data Center 和 CSV；第三个数据源立项时才设计通用适配器接口
（ADR-0035 C），不要为新扩展预先引入注册框架。Jira 两种部署共享同步、身份关联和证据逻辑。

### 接口与数据

`backend/app/connectors/jira/client.py` 提供现有客户端形状：

```python
def current_account(self) -> str: ...
def search(
    self, since: datetime, cursor: str | None, page_size: int = 100
) -> JiraPage: ...
def close(self) -> None: ...
```

客户端输入是校验后的 `JiraConfig` 与解密后的密钥。`JiraPage` 包含
`issues: list[JiraIssue]`、`next_cursor: str | None`；`None` 表示无下一页。
`JiraIssue` 包含 key、url、summary、issue_type、status、created、updated、resolved、
labels、components、description 和 people。`JiraPerson` 包含 role（assignee / reporter）、
id、display_name、email。客户端只负责抓取、分页、超时和格式规范化，不持有业务数据库会话。

Cloud 使用 `/rest/api/3/search/jql`，Data Center 使用 `/rest/api/2/search`。
描述仅在管理员启用时读取，不抓评论、工时和附件；富文本先经 `jira/text.py` 转成文本。
认证失败抛 `SourceAuthError`，暂时失败抛 `RetryableSourceError(message, retry_after)`，
不可用抛 `SourceUnreachable`；不得在异常消息中拼接令牌或上游响应。

同步层通过 `source/sync.py::jira_record` / `csv_record` 转为
`work/ingest.py::SourceRecord`：

```python
class SourcePerson(BaseModel):
    external_id: str
    display_name: str | None = None
    email: str | None = None
    role: str
    event_ref: str


class SourceRecord(BaseModel):
    source: str
    key: str
    url: str | None = None
    title: str
    task_type: str | None = None
    status: str | None = None
    created_at: AwareDatetime | None = None
    updated_at: AwareDatetime | None = None
    resolved: AwareDatetime | None = None
    occurred_at: AwareDatetime
    labels: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    project: str | None = None
    detail: str | None = None
    people: list[SourcePerson] = Field(default_factory=list)
```

这是字段形状，完整约束以源码为准：当前 `source` 仅允许 `jira` 或 `csv:<来源>`；
key、external_id、event_ref 非空；同记录的 event_ref 唯一；时间必须带时区并统一为 UTC。
`key` 和 event_ref 要在重试和增量同步中保持稳定，避免重复证据。

```python
def ingest_record(
    session: Session,
    *,
    org_id: UUID,
    record: SourceRecord,
    provenance: dict[str, JsonValue],
    identities_prepared: bool = False,
) -> IngestCounts: ...
```

调用者持有来源任务锁和事务；批量入口复用 `prepare_identities` 和现有 `_batch`。
结果统计任务、事件、证据新增 / 更正 / 撤销 / 不变及未关联人数。
外部账号先保存在 `ExternalIdentity`：同企业唯一在职邮箱可由 `source/linking.py` 自动关联；
缺邮箱、重名或歧义留待管理员确认，不能按 assignee 名称猜员工。
手工关联、解除与重新派生走 `source/identities.py` 和 `work/ingest.py` 的现有服务。

### 文件清单

新增来源时逐项检查下列位置；只增加 Jira 部署方式时可复用已有 kind，但不能把新来源伪装成 Jira。

| 文件 | 要检查或修改的内容 |
| --- | --- |
| `backend/app/connectors/<source>/` | 客户端、文本规范化及离线模拟；Jira 参照 `jira/client.py`、`text.py`、`mock.py` |
| `connectors/README.md` | 新来源的部署、模拟入口与指南链接 |
| `backend/app/domains/work/ingest.py` | `SourceRecord.source` 允许值和到 Task / WorkEvent / Evidence 的映射，保留幂等规则 |
| `backend/app/domains/source/schemas.py` | 配置、公开响应、`SyncRunPublic.kind`，凭据用 `SecretStr` |
| `backend/app/domains/source/service.py` | 读取、保存、连接测试、运行记录查询的 kind 和固定错误提示 |
| `backend/app/domains/source/sync.py` | 创建 / 重试的来源分支、分页游标、转换函数、结束状态与增量起草衔接 |
| `backend/app/domains/source/schedule.py` | 定期派发目前只选 Jira；新增来源需显式接入，保持锁和忙碌时不重复排队 |
| `backend/app/domains/source/models.py`、`backend/app/alembic/versions/<revision>.py` | 仅配置 JSON 不一定要迁移；新增结构或约束必须有 upgrade / downgrade |
| `backend/app/domains/source/linking.py`、`identities.py`（同目录） | 同租户关联、来源标签、撤销后重新派生，不绕过共同关联规则 |
| `backend/app/domains/source/import_records.py` | 仅扩展 CSV 格式时改解析、模板与预览 |
| `backend/app/worker/tasks.py` | 同步任务、租户参数和重试；优先复用现有任务 |
| `backend/app/api/routes/sources.py` | 管理员 API、kind 参数、凭据安全路由；新路由还需在 `backend/app/api/main.py` 挂载 |
| `backend/app/domains/admin_assistant/sources.py`、`registry.py`、`docs/admin-function-map.md` | 管理功能登记、预览 / 确认、安全凭据字段和页面对照，不增加 Pi 工具 |
| `frontend/src/components/Sources/`、`frontend/src/components/Admin/pages/SourcesPage.tsx` | 连接配置、同步状态、导入与凭据输入框 |
| `frontend/src/components/Sources/SourceForms.tsx` | 手工数据来源页面是否需要展示新来源 |
| `frontend/src/client/` | 运行 `bash scripts/generate-client.sh` 自动生成，不能手写 API 类型 |
| `backend/tests/connectors/test_<source>.py`、下列既有后端测试及 `frontend/tests/sources.spec.ts` | 新客户端契约、来源分支和页面回归；模拟服务变化同时改 `backend/tests/utils/` 对应 fixture |

### 必带测试

新增客户端必须覆盖分页结束与续传、时区、缺字段、富文本、401 / 403、429 / 5xx、超时及安全错误。
同步必须覆盖重复运行不重复证据、增量更正 / 撤销、失败重试、游标恢复、并发锁、定期派发；
身份必须覆盖跨企业拒绝、歧义不自动关联、关联与解除后的证据派生。网页保存、预览确认和
管理助手都要复用同一服务，验证未确认不生效和凭据不回显。相关回归命令（在 `backend/`）：

```bash
uv run pytest tests/test_extension_points.py tests/connectors/test_mock_jira.py \
  tests/domains/test_work_records.py tests/domains/test_sync.py \
  tests/domains/test_sync_schedule.py tests/domains/test_identity_auto_link.py \
  tests/api/routes/test_sources.py tests/api/routes/test_external_identities.py \
  tests/admin_assistant/test_sources.py tests/admin_assistant/test_registry.py -x -q
```

增加对应来源的测试文件；前端流程由 CI 运行 `frontend/tests/sources.spec.ts`。
若涉及租户、证据、凭据或迁移，本地按交付流程跑全量 pytest 和迁移往返验证。

## 2. IM 入口

### 接口与数据

实现 `backend/app/domains/im/adapters/base.py::Adapter` 协议，类声明
`platform: ClassVar[Platform]`，值等于 `gateway.py::ADAPTERS` 中的键。
其余签名如下；异步生成器 `poll` 不返回裸平台响应：

```python
def __init__(self, org_id: UUID, credentials: dict[str, str]) -> None: ...
def poll(self) -> AsyncIterator[Event]: ...
async def send_text(self, external_chat_id: str, text: str) -> None: ...
async def render(
    self, external_chat_id: str, text: str, buttons: list[tuple[str, str]]
) -> str: ...
async def answer_callback(self, callback_id: str) -> None: ...
async def clear_buttons(self, external_chat_id: str, message_id: str) -> None: ...
async def test_connection(self) -> str: ...
async def close(self) -> None: ...
```

`test_connection` 返回稳定的机器人身份，`render` 返回消息 ID，按钮二元组是「显示文字、
平台一次性动作 token」；其余发送 / 回调 / 清理返回 `None`。`close` 必须释放连接、任务和客户端，
取消时也能结束。失败抛 `ImError(code: str, retryable: bool = False)`，复用已有错误码
（如 credentials、timeout、network、invalid_response、recipient_unavailable），不回显凭据。

`Event` 是冻结 dataclass，必需 `org_id: UUID`、`platform: Platform`、`event_id: str`；
external_user_id、external_chat_id 默认空字符串，private 默认 False，text、callback_id、
token、message_id 默认 None（类型为 `str | None`）。平台消息规范化时必须验证实际私聊状态，
同一事件重投保持 event_id；回调要带原消息 ID、回调 ID 和平台动作 token。
org_id 来自已启用的企业连接，不能信任消息自带的租户标识。

`gateway.py` 负责连接、去重、私聊、绑定与派发，`binding.py` 计算本人 Principal，
`actions.py` 复用现有员工对话 / 确认服务。适配器不解释业务指令、不批准请求、不直接写证据。
当前企业邮箱自动绑定仅对 Feishu / Wecom 的 `enterprise_email` 生效；新平台默认走绑定码，
不能因为有同名方法就宣称支持邮箱绑定。

### 文件清单

`Platform` 保留在 `im/schemas.py` 的静态 `Literal`，不能从 ADAPTERS 动态生成：
后者无法供 mypy / ty 静态检查，且 schemas 被 base 引用，反向导入 gateway 会循环。
两处登记由 `test_extension_points.py` 检查集合一致及类的 platform 属性。
这条测试只检查这两处，设置、数据库约束、通知和页面仍需下面的回归覆盖。

| 文件 | 要检查或修改的内容 |
| --- | --- |
| `backend/app/domains/im/adapters/<platform>.py` | 实现 Adapter、platform 类属性和 Event 规范化 |
| `backend/app/domains/im/schemas.py` | `Platform` Literal 加入新值，不引入 gateway |
| `backend/app/domains/im/gateway.py` | 导入适配器并加入 ADAPTERS；不要另写业务派发路径 |
| `backend/app/domains/im/settings.py` | REQUIRED、允许的凭据字段、`test_credentials` 构造分支、`list_settings` 平台列表及邮箱绑定策略 |
| `backend/app/domains/im/models.py` | ImSetting、ImBinding、ImNotification、ImAction 的 platform CHECK 约束 |
| `backend/app/domains/bot/models.py`、`store.py`（同目录） | BotSession.channel、create_session 的渠道 Literal 和 `ck_bot_session_channel` 约束，保留 web 渠道 |
| `backend/app/domains/im/actions.py` | 核对 conversation 仍按当前平台复用上述会话服务，不新增旁路 |
| `backend/app/alembic/versions/<revision>.py` | 四张 IM 表和 bot_session 的约束 upgrade / downgrade；不改历史迁移，注明 downgrade 对新平台行及关联会话的处理 |
| `backend/app/domains/im/notifications.py` | 恢复 / 重试的平台列表；管理待办当前仅飞书，不能顺带开放管理员功能 |
| `backend/app/api/routes/im_settings.py`、`im_bindings.py`（同目录） | 核对仍使用 Platform 和共同服务，已有通用路由不必重复添加 |
| `frontend/src/components/Admin/pages/ImPage.tsx` | 「连接 → IM 入口」的平台名、凭据安全框、数据外发提示、邮箱绑定选项 |
| `frontend/src/components/UserSettings/ImBindings.tsx` | 平台名称及绑定 / 通知 / 解绑展示 |
| `frontend/src/client/` | 由 `bash scripts/generate-client.sh` 重新生成 Platform 联合类型 |
| `backend/app/domains/admin_assistant/connections.py`、`registry.py`、`docs/admin-function-map.md` | 核对只读测试可接新平台；修改设置当前只支持飞书，新平台保持网页修改，不扩大助手权限 |
| `backend/tests/domains/test_im_<platform>.py`、`backend/tests/utils/im.py` | 新适配器离线服务与契约测试 |
| 下列既有后端测试、`frontend/tests/im-settings.spec.ts`、`im-bindings.spec.ts`（同目录） | 增加新平台设置、绑定和通知用例，保留旧平台断言 |

### 必带测试

新适配器测试必须验证连接身份、文本 / 卡片发送、私聊 / 群聊识别、回调 / 按钮清除、断线重连、
资源关闭、重复事件 ID 和安全错误；测试使用模拟平台，还要实际创建新渠道的 BotSession。
共同服务覆盖绑定码过期 / 重放、
跨企业与冒用身份拒绝、关闭 / 改凭据后停止收发、未确认不提交、通知不重复执行动作、
受限正文不进入 IM、凭据不出现在日志 / 模型 / 响应中。现有回归命令：

```bash
uv run pytest tests/test_extension_points.py tests/domains/test_im_gateway.py \
  tests/domains/test_im_wecom.py tests/domains/test_im_conversation.py \
  tests/domains/test_im_notifications.py tests/api/routes/test_im_settings.py \
  tests/domains/test_bot_runtime.py tests/api/routes/test_bot_sessions.py \
  tests/admin_assistant/test_registry.py -x -q
```

再加 `tests/domains/test_im_<platform>.py`。网页设置 / 绑定由 CI 跑上述 Playwright 文件。
新增实际平台含凭据和约束迁移，本地还须全量 pytest、upgrade / downgrade、单一 Alembic head；
不要为过测试删除旧平台用例或放宽检查。

## 3. 能力模板

### 接口与数据

新增 `templates/capabilities/<id>.zh-CN.yaml`，同时在 `catalog.yaml` 登记：

```yaml
- id: example-team
  file: example-team.zh-CN.yaml
  name: 示例团队
  industry: 示例行业
  description: 仅使用合成内容的示例能力模板。
  recommended: false
```

模板本体是能力列表，每项包含以下全部字段：

```yaml
- key: example_delivery
  name: 示例交付能力
  category: 示例分类
  description: 描述可观察的工作能力，不作绩效判断。
  level_rubric:
    levels:
      - key: needs_support
        label: 需要协助
        description: 在指导下完成任务。
      - key: routine
        label: 能完成常规任务
        description: 独立完成边界明确的常规任务。
      - key: independent_complex
        label: 能独立完成复杂任务
        description: 独立处理复杂约束并验证结果。
```

`backend/app/domains/capability/templates.py::load_catalog(directory: Path)` 返回
`tuple[CapabilityTemplate, ...]`，每项有 entry（CatalogEntry）、capabilities（list[Capability]）、
template_ref（`id@文件内容SHA256前12位`）；推荐项在前，同组保留目录顺序。它缓存文件读取，
生产进程需重新部署才能看到新模板。`files.py::parse_dictionary(content: bytes, filename: str)`
返回 `(list[DictionaryDefinition], errors: list[str], warnings: list[str])`，用于网页词典校验。

目录 id 唯一，file 必须恰为 `<id>.zh-CN.yaml` 且不能跳出目录；无未登记模板。
能力 key 在模板内唯一，等级必须恰好为上面三种且不重复；所有字段不得夹带额外字段。
网页校验还要求 1–1000 项能力，key 非空且最长 100、name 非空且最长 200、
category 最长 200、description 最长 4000；等级 label 非空最长 200、description 非空最长 4000。
请同时通过两种校验，不添加已经下线的 `ai_inference` 字段。

模板只提供词典定义，不带员工、等级判断、证据或审批数据，不修改已发布画像；管理员仍需
通过现有选择 / 预览 / 保存流程应用（ADR-0016）。新模板不能触发模型调用或增加 Pi 工具。

### 文件清单

| 文件 | 要检查或修改的内容 |
| --- | --- |
| `templates/capabilities/<id>.zh-CN.yaml` | 新能力定义和三档等级说明 |
| `templates/capabilities/catalog.yaml` | 新 id、文件名、中文展示与行业信息 |
| `templates/README.md` | 模板列表或适用范围说明 |
| `backend/tests/domains/test_capability_templates.py` | 新模板用例；新增时更新目录数量的精确断言，保留现有模板与推荐顺序断言 |
| `backend/tests/api/routes/test_capability_templates.py` | 同步目录数量断言、选择 / 预览 / 应用回归 |
| `backend/tests/test_extension_points.py` | 通用目录完整性和词典校验，应不需要改代码 |

加载器、API、页面均按目录发现模板，不需要新增业务分支、手写前端类型或数据库迁移。

### 必带测试

```bash
uv run pytest tests/test_extension_points.py tests/domains/test_capability_templates.py \
  tests/api/routes/test_capability_templates.py tests/api/routes/test_dictionary_versions.py -x -q
```

覆盖 catalog 每项可加载且通过网页词典校验、无漏登记文件、重复 key / 等级与非法路径被拒绝、
模板选择 / 预览 / 应用和版本冻结；新模板中的每项都要有具体且不越界的等级描述。
前端应用流程由 CI 跑 `frontend/tests/capability-dictionary.spec.ts`。
