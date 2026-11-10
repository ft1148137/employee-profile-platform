# ADR-0018：内置机器人对话核心与入口约定

状态：采纳。关联 NERE-106、E3、E12、E17，运行底座沿用 ADR-0014 的 S14a。

网页和以后 M4 的 IM 入口只识别员工、发送文本或动作、渲染消息。核心以每轮后端提供的 Principal 调用共享员工服务层；Pi 不取得数据库连接或身份参数，也不能执行命令或读写文件。

## HTTP 与消息

同步接口为 `POST /api/v1/bot/sessions`、`GET /api/v1/bot/sessions/{session_id}/messages`、`POST .../turns`、`POST .../actions`。沿用 `OrgPrincipalDep`，企业通过必填查询参数 `org_id` 传入，员工由登录态确定；非成员、其他员工和其他企业的会话均 404。创建返回 `{session_id}`，历史沿用 S14a 的消息列表，turns/actions 返回 `{messages: [...]}`。不流式输出。文本正文是 `{text}`，最多 10000 字。

核心消息仅四种：

| type | 内容与动作 |
| --- | --- |
| text | `text`，纯文本展示，不解释为操作 |
| preview_card | `request_id/revision/items`，每项 `before/after/warning`；after 含能力、操作、陈述、等级、依据说明、异议、不确定性；动作 `confirm_submit`、`continue_editing` |
| status_card | `id/revision/status/items`；pending_review 才有 `withdraw` |
| question_card | `id/question/reason/status`；open 才有 `answer_question` |

请求动作原样回传 `{action, request_id, revision}`；回答动作传 `{action: "answer_question", question_id, answer}`。入口按钮文案为「确认提交」「继续修改」「撤回」「回答」，不得把模型输出的文字转换为动作。

核心先验证会话归属，再用服务层验证请求/问题归属。请求动作必须匹配本会话保留期内真实展示的卡片，revision 必须仍是当前版本；不匹配为 409「内容已变化，请重新预览」。问题卡过期则 409「请重新查看问题」。会话有进行中的模型轮次时动作返回忙，避免展示途中提交。服务层的行锁和状态机防止重复提交。动作不调用模型，不消耗模型轮次、不受模型暂停影响；额度耗尽后仍可提交或撤回已有卡片。

Pi 工具清单只来自 `employee_tools.py` 中的 `bot_allowed=True`。输入模型禁止额外字段，不能注入 employee_id、org_id、via 或 client_name。提交和撤回不出现在 Pi 工具定义或适配器里。草稿和预览产生卡片；查请求也按当前状态产生卡片。仅入口确认动作调用 `employee.submit(..., via="bot", client_name="内置机器人")`。request_ids 只记录经服务层验证的本人请求，不承担授权。

## 发给 Pi 的白名单

`schemas.py` 为每种工具结果定义独立投影，只从服务 DTO 读取下表字段，绝不序列化整个服务 DTO。嵌套投影同样封闭。仅投影对象可以 model_dump。

| 工具 | 返回字段 |
| --- | --- |
| get_my_profile | items（capability 的 key/name、status、claim）、auto_draft_enabled |
| list_my_evidence | data（id/kind/title/status/redacted）、count |
| get_my_requests | data（id/revision/status/items）、count |
| get_capability_dictionary | data（key/name/description、level_rubric.levels 的 key/label/description、proactive） |
| draft_update_request | id/revision/status/items |
| preview_request | request_id/revision/items（before/after/warning） |
| get_pending_questions | data（id/question/reason/status）、count |
| answer_question | id/question/reason/status |

claim 只有 statement/level/assessment_status；请求 item 只有 op、capability 的 key/name、after claim、explanation/uncertainty/owner_statement/objection。不包含员工对象、员工或企业 ID、邮箱、来源定位 source_ref、外部链接、凭据、原始 evidence content/provenance、作者、客户端和模型内部信息。对投影字符串再替换邮箱和 HTTP(S) 链接；历史进入 Pi 前也做相同替换，防止卡片历史绕过处理。此投影不声称能识别任意自然语言中的第三方姓名，平台不读取其他员工的数据。

E12 通过提示词和投影共同表达：本人退出或部门关闭时 auto_draft_enabled 为 false；词典中敏感能力的 proactive 为 false。它们限制主动建议，员工主动提出仍可查看和起草。管理员提问指南放在系统提示词的受限指导段，不能覆盖身份、确认、权限与用途边界。系统提示词固定声明不做绩效、薪酬、晋升判断，以及 Jira、未知和 confidence 的边界。

## 事务、历史与错误

先读取保留期内的历史，再由 begin_turn 准入并保存本次输入。成功的完整 Pi 轮次才提交工具写入及核心回复；超时、额度错误、异常或未完整结束回滚草稿和工具写入。只保存用户输入和四类核心回复，不保存工具原始结果；密文、保留、清理和准入数字均沿用 S14a。动作回复同样 append_message；确认后的受限自述证据继续由共享服务层追加，撤回走撤销记录，不改证据协议。

错误只返回固定中文 text，不透传 Pi 原文、不重试：忙为「忙，请稍后再试」；起草优先为「正在起草画像，请稍后再试」；限频为「每小时 30 轮已用完，{ISO 恢复时间} 恢复」；额度为「模型额度暂时用完，预计 {ISO 恢复时间} 恢复」；未启用模型为「机器人需要先配置模型，你也可以在画像页直接修改」；其他模型错误为「模型服务暂时不可用，请稍后再试」。ISO 时间带时区。额度错误的企业暂停仍由 S14a 写入，未知恢复时间按 ADR-0014 估算 30 分钟。

没有新增依赖或迁移。可逆默认：沿用 S14a 的历史保留作为卡片动作有效期，历史过期后员工重新预览。网页入口在 S15 交付；IM 入口在 M4 复用同一核心协议。

## IM 经理审批（NERE-131）

Pi 增加三个只读/准备工具，沿用 S3 的审批人路由与有效代理人校验。`list_pending_reviews` 只投影有权决定的请求；`get_review` 和 `prepare_review_decision` 对本人请求、跨企业及无权审批的请求返回空。没有员工身份的企业成员不能绑定 IM，只能在网页审批。

| 工具 | 发给 Pi 的字段 |
| --- | --- |
| list_pending_reviews | data（id、item_count）、count |
| get_review / prepare_review_decision | id、revision、items（before claim、after 的 id/op/capability/after claim、evidence 的 title、jira_only、approvable、approval_blocked_reason） |

claim 与 capability 继续使用上述封闭投影。没有 restricted 证据正文、员工依据说明、本人补充、异议、source_ref、来源链接、员工身份或企业信息；投影字符串与历史仍替换邮箱和 HTTP(S) 链接。准备工具校验修订号、全部未决定项、重复项、可批准状态和原因，复用决定服务的校验，不写决定或画像。退回和拒绝的原因必须从经理的话中取得，缺失则追问。

新增第五类核心消息 `review_card`：白名单审批详情、每项决定和原因、revision、随机 card_id、动作「确认决定」「继续修改」。IM 渲染只外发能力、改动前后陈述/等级/状态、依据标题、jira_only 标签，以及经理的决定和原因，提示「详情在网页审批页查看」。超过卡片长度上限则引导网页，不提供确认按钮。群聊在网关进入会话前即返回私聊提示，不读取审批数据。

入口仍用 S11b 的 22 字符短 token；token 绑定企业、入口、机器人、员工、私聊、原消息和会话，消费与决定同事务。`confirm_review` 只接受本会话保留期内实际生成过的 card_id 与动作，从保存的卡片取决定，重新验证审批权与当前 revision，再调用唯一的 `decide(..., via="im", client_name=平台标识)`（telegram / feishu / wecom）。同 revision 的不同卡片由 card_id 区分，不能替换决定。Pi 无 decide、confirm_review 或任何审批写入工具，文字「已确认」不产生动作。

网页工具调用只回复文字及「去审批页」链接，不生成审批卡片；HTTP 动作请求不接受 ReviewAction。网页组件对误收到的 review_card 也只显示审批页链接。

没有新增依赖或数据库迁移。可逆默认：审批卡沿用现有会话历史与 token 保留期限；整体意见不接受隐藏字段，经理的原因逐项显示和保存。

## 会话上下文由 Pi 管理（NERE-286）

本节取代上述文本历史拼接及「不保存工具原始结果」的存储约定：完整用户消息、模型回复、工具调用与**经过投影的工具结果**由 Pi 组织，后端将协议 v2 的状态原样加密存进 `bot_session.state_ciphertext` / `admin_assistant_session.state_ciphertext`。员工输入在进入 Pi 前替换邮箱与 HTTP(S) 链接，工具仍走封闭白名单投影和相同替换；管理助手仍使用 `{data: ...}` 包装和受限证据标题替换。安全输入框密钥不进入模型，疑似密钥输入在准入前拦截。工具原始服务 DTO、凭据和受限正文仍不能进入状态。

界面消息表继续保存文字和卡片，用于刷新和动作校验，不再参与模型上下文拼接。只有完整成功的轮次才在工具写入、回复的同一事务内保存新状态；失败、超时或回滚保留上一轮状态。状态为空的旧会话从空上下文开始，界面历史照常显示。

状态沿用 `BotSetting.retention_days`：0 天时闲置 2 小时失效，N 天时任一消息到期即使整个状态失效（后端不拆解状态）。清理消息时一起清空状态；清理任务运行前读取也会检查过期，避免过期上下文被新输入复活。上下文预算、工具配对裁剪和换模型规则见 ADR-0014；权限、证据只追加和确认卡协议保持原有规则。

上传文件是模型轮次之外的网页操作；下一轮仍从保留期内最近 20 条界面消息提取最近文件预览的 counts 与最多 20 条错误摘要，作为当前系统提示中的平台数据，不包含名单明细或其他界面历史，也不解析或修改已保存状态。系统提示与工具定义由每轮当前权限重新生成，不放入持久状态。
