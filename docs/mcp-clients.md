# 外部 MCP 客户端接入说明

员工可以让外部助手读取自己的画像、证据和能力词典，整理更新草稿，再提交给经理。
经理使用自己的账号连接后，还可以查看待审批请求、审批和按能力找人。
本文的 MCP Inspector CLI 已完成真实 HTTP 接入实测；其他客户端按下面的通用配置接入。

## 管理员先开启

1. 在网页进入「数据与 AI → 外部 AI 助手」，开启「允许外部 AI 助手」并保存。
2. 告知员工平台地址，例如 `https://profiles.example.com/mcp`。
3. 如设置客户端允许列表，名称必须与初始化时的客户端名称完全一致。本次 Inspector 报的是 `inspector`。

允许列表按客户端自报名称匹配，不是客户端身份认证。OAuth 使用注册的客户端名称。
MCP 始终按令牌所属用户的当前员工身份和企业权限执行，不接受助手指定另一个企业或员工。
没有员工身份的账号不能生成个人令牌；没有员工身份的管理员请在网页审批。

正式接入使用企业提供的 HTTPS 地址，并让客户端信任证书。
云端助手必须能访问该地址，不能连接你电脑上的 localhost 或仅企业内网可达的地址。
外部助手能看到工具返回的画像、证据及已确认自述；开启前请确认企业允许向所选厂商传输这些信息。

## 员工选择授权方式

### 个人令牌

1. 登录网页，进入「个人设置 → 连接 AI 助手」。
2. 填写「令牌名称」（例如所用客户端的名字），点击「生成令牌」。
3. 令牌只显示一次，存入客户端的凭据配置。丢失后吊销旧令牌，再生成新的。
4. 连接方式选 Streamable HTTP，地址填写平台的 `/mcp`，认证头为 `Authorization: Bearer <个人令牌>`。

个人令牌默认 90 天到期。在同一页面可吊销；改密码、停用账号、关闭企业 MCP 后，
已连接会话的下一次调用也会被拒绝。连接会话一小时后需要重新初始化。
不要把令牌发给聊天模型、贴到截图、工单或代码仓库。

### OAuth 网页授权

支持 OAuth 的客户端可只填平台 `/mcp` 地址，选择 OAuth 登录和自动注册（DCR）。
平台支持授权码与 PKCE；授权范围是 `employee`，经理也使用这个范围，业务权限仍由服务端判定。
无需填写个人令牌或手工创建客户端密钥。

客户端会跳转到企业平台：登录本人的账号，核对企业、客户端名称和将开放的信息，再确认授权。
返回客户端后即可调用工具。撤销时进入「个人设置 → 连接 AI 助手 → 已授权的客户端」；
撤销会使该授权链的访问令牌与刷新令牌失效，恢复连接需重新授权。
同一连接不要同时配置 OAuth 和个人令牌。

部署者需确保 OAuth 发现的地址与员工访问的平台地址一致，不能把内部容器地址发给客户端。
本节说明现有授权能力；本次实测使用个人令牌，未重新验证各厂商的 OAuth 界面。

## 通用客户端配置

在客户端的 MCP 连接设置中填入：

| 项目 | 值 |
| --- | --- |
| 传输 | Streamable HTTP（部分客户端写作 HTTP） |
| URL | `https://profiles.example.com/mcp`，替换为企业地址 |
| 认证 | OAuth，或个人令牌的 Bearer 请求头，二选一 |
| 初始化 | 由客户端发送 `initialize`，之后再列出、调用工具 |

支持 `mcpServers` 格式的客户端可参考：

```json
{
  "mcpServers": {
    "employeeProfile": {
      "url": "https://profiles.example.com/mcp",
      "headers": {
        "Authorization": "Bearer <个人令牌>"
      }
    }
  }
}
```

这里只展示结构；按客户端文档使用其凭据存储或环境变量功能，不要把含真实令牌的配置提交。
各客户端的变量替换语法不同，不要把占位符直接当成认证值。
OAuth 客户端只配 URL、通过网页登录，不添加上面的请求头。
若只有 stdio 或旧式 SSE 选项，需换成支持 Streamable HTTP 的客户端。

## 员工工具与更新顺序

| 工具 | 用途与主要输入 |
| --- | --- |
| `get_my_profile` | 查看本人正式画像，无参数 |
| `list_my_evidence` | 查看本人证据，可按 `kind` 筛选；`skip`、`limit` 分页 |
| `get_capability_dictionary` | 读取当前词典与等级，无参数 |
| `get_my_requests` | 查看本人请求；`status` 为 `open` 或 `all` |
| `draft_update_request` | 保存草稿；`items` 必填，继续编辑时带 `request_id` |
| `preview_request` | 按 `request_id` 读取改动、警告和 `revision` |
| `submit_request` | 按 `request_id`、预览的 `revision` 提交 |
| `withdraw_request` | 按 `request_id` 撤回本人的草稿或待审批请求 |
| `get_pending_questions` | 查看本人待确认问题，可分页 |
| `answer_question` | 按 `question_id` 和 `answer` 保存本人确认的回答 |
| `appeal_decision` | 申诉本人被拒绝的改动；参数以工具 schema 为准 |

先读词典，使用其中的 `capability_key`、等级 key 和本人证据 ID。
草稿条目包含 `op`、`capability_key`、`statement`，按需添加 `level`、
`owner_statement`、`objection`、`uncertainty`、`evidence_ids`。
`op` 支持 `add`、`modify`、`retract`、`attach_evidence`；
应与当前画像状态匹配，已有能力改动使用 `modify`。

保存草稿后调用预览，展示改动及依据，再用最新修订号提交。
预览返回 `base_revision` 时，提交一并携带；旧初稿与经理新修改冲突时，重新预览并让本人核对。
外部助手可以直接提交，不需要再到网页点击提交。
每次提交都记录来源 `mcp` 和客户端名称，员工会收到提醒。
企业开启经理确认时进入待审批；关闭某类能力的经理确认时，本人确认后可按规则发布为暂定。

经理批准前可以撤回待审批内容；已经发布的项不会因撤回而回滚。
撤回后的请求不能原地重新提交；需要新建草稿、重新预览再提交。
回答待确认问题只追加 restricted（受限）的自述证据，不直接修改正式画像。
只保存本人确认的陈述，不收集外部助手的聊天原文。
证据纠错走撤销或更正记录，保留审计；不能覆盖或删除原始证据。

### 输入上限

| 字段 | 上限 |
| --- | --- |
| 新写或修改的陈述 `statement` | 200 字 |
| 每条的说明文字，如 `owner_statement`、`objection`、`uncertainty` | 各 4000 字 |
| 一次草稿的 `items` | 50 条 |

超出时返回中文提示，请缩短或分批整理。旧的超长陈述保持原文、只修改等级时，不会被新上限挡住。
待确认问题的 `answer` 单独限制为 1–10000 字且不能全为空白。
证据列表和问题列表默认每页 50 条，`limit` 最大 100；用 `skip` 继续读取。
工具的完整参数、枚举和必填字段以连接后的 `tools/list` 返回为准。

## 经理工具：审批与找人

经理仍使用本人的授权，不借员工令牌。工具列表可见不代表有执行权限。
普通员工不能替经理批准，任何人不能批准自己的请求，外部助手不能绕过审批规则。

| 工具 | 用途 |
| --- | --- |
| `list_pending_reviews` | 列出当前用户有权处理的待审批请求 |
| `get_review` | 按 `request_id` 读取改动前后、依据及审批用 `revision` |
| `decide_review_items` | 按请求、修订号和逐项 `decisions` 提交决定 |
| `get_team_overview` | 汇总可见员工的画像状态及本人待审批数 |
| `find_people` | 按能力查找管辖范围内的在职员工，只读 |

审批前，助手必须向经理逐项复述决定与原因，经理明确同意后才调用 `decide_review_items`。
每项带 `item_id`、`decision`（`approved`、`changes_requested`、`rejected`）；
退回和拒绝必须填写 `reason`。修订过期时重新读取、重新确认，不能盲目重试。
批准和发布在同一事务完成，审批同样记录 MCP 来源及客户端。

`find_people` 必填 `capability_key`，可选 `minimum_level`、`statuses`、
`evidence_from` / `evidence_to`、`department_id`、`department_capability_only`。
企业启用相应档案字段时，还可按 `job_family`、`work_location`、入职日期范围筛选。
日期写成 `YYYY-MM-DD`；状态可用 `confirmed`、`tentative`、`disputed`。
按等级筛选默认不匹配未定级判断；需要时设置 `include_ungraded=true`。
结果按 `skip` / `limit` 分页，每页最多 100。

部门经理只能搜索获授权部门及全部下级部门；企业管理员可查本企业。
找人结果含陈述、等级、状态、证据数、最近证据时间、发布人和发布时间，不含证据标题和正文、
说明或受限正文。未知不等于不会；找人不做绩效评分、排班或调配。
只有 Jira 来源支持的判断最多为暂定，confidence 也不是概率。

## 需求与容量工具

除企业开启「数据与 AI → 外部 AI 助手」外，还须在「能力与规则 → 需求与容量」开启模块（默认关闭），以下工具才出现在 `tools/list`。关闭模块后工具消失，已有连接也不能继续调用。重新列出工具以当前返回为准；员工和经理仍使用本人授权，不传入其他企业或员工身份。

| 工具 | 用途与主要输入 |
| --- | --- |
| `list_capacity_departments` | 选择目标部门，只列部门名称和层级，无参数 |
| `capacity_reference` | 按 `target_department_id`、`capability_key`、可选 `minimum_level` 读部门人数和负荷档位汇总；A 方看到的匹配人数为 1–2 人时隐藏人数与余量，只提示提交需求由对方经理答复，0 人仍提示暂无匹配，3 人及以上正常显示 |
| `submit_demand` | 写：提交需求；部门、能力、工作量、日期和说明见下文 |
| `list_demands` | 读本人有权查看的需求，`side` 为 `outgoing`（默认）或 `incoming` |
| `get_demand` | 按 `demand_id` 读详情、时间线和当前 `revision` |
| `get_demand_basis` | 按 `demand_id` 读本部门依据和个人负荷，仅限有权代表答复方的经理／管理员 |
| `reply_demand` | 写：按需求和最新修订号，代表 B 方答复并确认承诺日期 |
| `respond_demand` | 写：按 `demand_id`、`revision`、`action` 接受、追问或撤回，追问还要 `text` |
| `get_my_workload` | 读本人 Jira 参考值与经理确认负荷，无参数 |
| `request_workload_change` | 写：用 `requested_percent`（0–200 整数）和 `reason`（1–500 字）申请调整本人平台外其他工作占比 |
| `list_my_assignments` | 读本人分配、投入比例、完成日期及状态，无参数 |
| `request_extension` | 写：用 `assignment_id`、`requested_due_date`、`reason`（1–500 字）申请延期，新日期须晚于当前完成日期 |

「谁可以提需求」为「只有部门经理」时，普通员工的列表还会隐藏前三项工具；本人负荷、分配和申请工具仍保留。工具可见不代表能操作所有需求：A 方不能用 `get_demand_basis` 读取 B 个人信息，不能替 B 答复或替其他员工申请。

### 需求与答复的输入和确认顺序

1. 先读企业能力词典和部门列表。即时参考只给达到要求的部门人数、负荷档位人数；**参考不是承诺**，也不预测未来时间窗口的可用人手。
2. `submit_demand` 填 `requesting_department_id`、`target_department_id`、`capability_key`、可选 `minimum_level`、`headcount`（1–50）、`allocation_percent`（10–100 整数）、`window_start`、`window_end` 和 `description`（1–2000 字）。日期为 `YYYY-MM-DD`，结束不早于开始，目标与需求方部门不同；员工只能以本人部门提交，经理在管辖范围内选。
3. B 方先用 `get_demand` 和 `get_demand_basis` 核对需求与本部门依据，再起草答复。`reply_demand` 填 `demand_id`、最新 `revision`、`outcome`（`can_do` 能做、`partial` 能做一部分、`not_now` 暂时不能）、`text`（1–2000 字）。能做或部分能做时必须填 `start_date`、`end_date`；暂时不能可不填，填时须成对。日期前后须合理。
4. A 方读答复后，用 `respond_demand` 的 `action=accept` 接受，或 `follow_up` 加 `text` 追问，或 `withdraw` 撤回。暂时不能的答复不可接受；追问回到待答复，已接受／已撤回不能继续回应。

**每个写工具都必须先向当前用户复述具体内容，取得明确同意后才调用。** 尤其答复要核对结论、起止日期和对外说明，AI 不替经理承诺。MCP 在用户同意后直接调用写工具，不要求回网页点击内置助手确认卡；服务端不替外部客户端完成这次人机确认。写入记录来源 `mcp` 和客户端名称。

`reply_demand` 的正文只写部门级结论，不写 B 部门员工姓名、工号、个人画像或负荷。服务端拦截含 B 员工姓名的答复，答复时的部门级依据由服务端重算，不接受客户端自造的依据。A 方任何入口都不显示 B 方任何人的姓名，包括答复人；`get_demand`、`list_demands` 及写工具返回的 `responder_name` 对 A 方为空。客户端可显示「答复人：对方部门经理」，不能另查或推断姓名；B 方有权代表仍可看到本部门答复人姓名。修订冲突时重新读取、复述和确认，不能直接重试旧内容。

### 负荷、延期与网页边界

Jira 参考值是已同步的未完成指派任务件数，没有接入／关联时为 null，不是零；同步较旧时参考也会过旧。经理确认值是平台外其他工作占比加所有进行中平台分配投入，前者未确认时总值仍为 null。件数不换算为百分比；低于 80% 有余量、80%–100% 基本满、高于 100% 超负荷，未确认单列。负荷不是绩效，不排名。

负荷调整和延期只能申请本人的内容，同一人最多一条待处理负荷申请，同一分配最多一条待处理延期。申请不会直接改确认值、完成日期或对外承诺，也不能替本人批准。

MCP 没有分配、直接调整负荷、改完成日期、审批负荷或审批延期的工具，请有权人员到网页「需求与容量」处理。延期审批人及代理人须有当前审批权，并且是目标部门 B 及下级的在职成员，或 B 的有权代表；推迟对 A 的承诺必须由 B 的有权代表确认新日期与对外说明，并同时通知 A。

网页步骤见[员工指南](employee-guide.md#需求与容量)和[经理指南](manager-guide.md#需求与容量)。下面 Inspector 的历史实测是在模块关闭时进行的，16 个工具的记录不包含这组工具；本节工具以当前代码和 `tools/list` 为准。

## MCP Inspector CLI 实测记录

实测时间：2026-11-15（北京时间）。服务代码：`929b14933b937afe9f4414d4c9597c4e26cb0468`。
环境：独立 Compose 项目 `nere624`，PostgreSQL 映射 `15624:5432`，
backend 映射 `18624:8000`；两项均用仓库外覆盖文件的 `ports: !override` 设置。
数据库、Pi、backend、worker、IM 就绪后测试，只使用 `examples/demo-org` 的合成企业。
未配置模型供应商，没有调用外部模型，没有使用本机 AI 订阅凭据。

外部客户端为 MCP Inspector `0.21.0`（实际 CLI 包 `0.21.2`），Node.js `22.15.1`。
CLI 在宿主机独立进程运行，真实连接 HTTP 后端；没有用 TestClient 或替换服务处理函数。
安装在仓库外的测试目录，不改应用依赖。以下命令在该测试目录运行：

```bash
npm install --ignore-scripts --no-audit --no-fund \
  @modelcontextprotocol/inspector@0.21.0 \
  @modelcontextprotocol/inspector-cli@0.21.2
export MCP_URL=http://127.0.0.1:18624/mcp
# bash：分别安全输入员工、经理本人的平台令牌，输入不会回显。
read -r -s -p '员工个人令牌：' EMPLOYEE_TOKEN; echo
read -r -s -p '经理个人令牌：' MANAGER_TOKEN; echo
mcp() {
  node ./node_modules/@modelcontextprotocol/inspector/cli/build/cli.js \
    --cli "$MCP_URL" --transport http "$@" \
    --header "Authorization: Bearer $MCP_TOKEN"
}
MCP_TOKEN=$EMPLOYEE_TOKEN
mcp --method tools/list
```

令牌由平台的个人令牌接口签发，与网页「生成令牌」使用同一服务；
管理员先通过网页对应的设置接口开启 MCP，员工和经理各使用自己的登录会话生成令牌。
令牌仅在实测进程内存中使用，未写入记录文件。上面的终端用法不要开启 `set -x` 或录屏，
只在可信本机执行（请求头会传给客户端进程）。每条 CLI 命令都会初始化自己的连接。
返回的 `tools` 有 16 个工具；工具调用读取 `structuredContent`，并检查 `isError` 不为 true。
本次初始化记录的客户端名称为 `inspector`。

下面的 `REQUEST_ID`、`REQUEST_ID_2`、`QUESTION_ID` 从前一步返回值复制，
`REVISION`、`REVISION_2` 从对应预览复制，都是业务标识，不是认证凭据。
实际调用由宿主机脚本按同一 CLI 参数逐步执行并断言结果。

| 步骤 | 命令（省略已定义的函数展开） | 实际结果 |
| --- | --- | --- |
| 读本人画像 | `mcp --method tools/call --tool-name get_my_profile` | 员工 E02，12 项能力视图 |
| 读证据 | `mcp --method tools/call --tool-name list_my_evidence` | 5 条本人有效证据 |
| 读词典 | `mcp --method tools/call --tool-name get_capability_dictionary` | 12 项能力及等级描述 |

保存新能力草稿，使用以下合成陈述；取返回的 `id` 作为 `REQUEST_ID`：

```bash
mcp --method tools/call --tool-name draft_update_request --tool-arg \
  'items=[{"op":"add","capability_key":"automated_testing","statement":"能为合成 API 编写回归测试","owner_statement":"本人确认：在合成项目中编写并运行了接口回归测试"}]'
```

结果为 `status=draft`、`revision=1`。继续执行：

| 步骤 | 命令 | 实际结果 |
| --- | --- | --- |
| 预览 | `mcp --method tools/call --tool-name preview_request --tool-arg request_id="$REQUEST_ID"` | 返回改动和 `revision=1`、`base_revision=2` |
| 提交 | `mcp --method tools/call --tool-name submit_request --tool-arg request_id="$REQUEST_ID" revision="$REVISION"` | `pending_review`、`revision=2`；`submitted_via=mcp`、`client_name=inspector` |
| 撤回 | `mcp --method tools/call --tool-name withdraw_request --tool-arg request_id="$REQUEST_ID"` | `withdrawn`、`revision=3` |

实测使用的是新草稿，提交时只传请求修订号即可。旧初稿应按预览携带 `base_revision`。
再次执行上面的 `draft_update_request` 命令，取新 `id` 为 `REQUEST_ID_2`，不复用撤回请求：

| 步骤 | 命令 | 实际结果 |
| --- | --- | --- |
| 再次预览 | `mcp --method tools/call --tool-name preview_request --tool-arg request_id="$REQUEST_ID_2"` | `revision=1` |
| 再次提交 | `mcp --method tools/call --tool-name submit_request --tool-arg request_id="$REQUEST_ID_2" revision="$REVISION_2"` | `pending_review`、`revision=2`，来源和客户端仍正确 |
| 读待确认问题 | `mcp --method tools/call --tool-name get_pending_questions` | 1 条 open 问题，要求描述独立排查故障的经历 |
| 回答 | `mcp --method tools/call --tool-name answer_question --tool-arg question_id="$QUESTION_ID" 'answer=本人确认：我在合成演练中检查日志、定位超时并增加回归用例。'` | `answered`，返回 `answer_evidence_id` |
| 核对证据 | `mcp --method tools/call --tool-name list_my_evidence` | 有效证据 7 条；回答对应证据的 `visibility=restricted` |

切换经理本人的令牌：`MCP_TOKEN=$MANAGER_TOKEN`。使用第二次提交的请求：

| 步骤 | 命令 | 实际结果 |
| --- | --- | --- |
| 待审批 | `mcp --method tools/call --tool-name list_pending_reviews` | 1 条，正是第二次提交的请求 |
| 读审批详情 | `mcp --method tools/call --tool-name get_review --tool-arg request_id="$REQUEST_ID_2"` | 可读取改动和 `revision=2` |
| 按能力找人 | `mcp --method tools/call --tool-name find_people --tool-arg capability_key=backend_api minimum_level=routine` | 2 人：E01（routine）、E02（independent_complex），均在平台研发部范围内 |

最后切回员工：`MCP_TOKEN=$EMPLOYEE_TOKEN`，再次调用 `get_my_profile`。
正式画像与起始结果完全相同，待审批的改动及回答没有直接覆盖已发布画像。
测试后已吊销员工、经理两枚平台令牌，并删除本项目容器、数据卷、镜像和客户端临时目录。
终端读入令牌的用户应执行 `unset MCP_TOKEN EMPLOYEE_TOKEN MANAGER_TOKEN` 并关闭测试终端。

上述步骤全部成功。本次没有实际调用经理的批准、退回、拒绝，也没有测试模型自动选工具；
它验证的是外部客户端真实协议调用能完成本节列出的员工流程和经理读取流程。
相关自动化回归：`test_mcp.py`、`test_manager_mcp.py` 共 18 项通过，含越权、跨企业、
禁止自批、令牌失效与客户端来源记录检查。

## 连接失败时

| 表现 | 处理 |
| --- | --- |
| 401，令牌无效或已失效 | 核对账号、令牌到期和吊销情况；重新生成或重新 OAuth 授权 |
| 403，企业已关闭或未允许客户端 | 请管理员检查开关和客户端名称允许列表 |
| 404，会话失效 | 重新初始化连接；不要复用另一令牌的会话标识 |
| 工具结果 `isError=true` | 阅读中文业务错误；HTTP 200 不等于工具执行成功 |
| 修订冲突或提示先核对 | 重新预览，确认最新改动后使用新的修订号提交 |
| 查不到别人或不能审批 | 核对本人角色、管辖范围和请求状态；令牌不会扩大权限 |

Inspector 的参数说明见[官方 CLI 文档](https://github.com/modelcontextprotocol/inspector/blob/main/clients/cli/README.md)；
该页面跟随新版本更新，本节命令和实测结果对应上面固定的历史版本。
