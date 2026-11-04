# ADR-0015：共享员工服务、预览修订号与自述证据

状态：采用（NERE-89）；2026-11-04。

网页、MCP、JSON 导入与内置机器人共用 change_request.employee。服务函数第一个参数是服务端 Principal，不接受员工或企业 ID；没有员工身份或请求不属于本人时返回 404。适配器负责注入可信的 via / client_name，并在成功后提交事务。网页固定 web；机器人只拿到 bot_allowed 工具，模型不能提交或撤回。

草稿整体替换改动项，已有请求的 revision 加一；起草重跑和状态变化也加一。预览、编辑、提交和撤回都锁定请求并刷新 ORM 缓存。提交必须携带预览 revision，过期返回 409。新增草稿从 revision=1 开始。服务只生成候选，不发布 ProfileClaim 或 ProfileVersion。草稿仅本人可见；修改中的初始请求继续算待员工处理。

输入严格禁止额外字段；按当前词典 key 校验能力和等级，按本人当前判断定位修改、撤回和补证目标。词典、当前判断和证据访问批量查询，读取和校验的 SELECT 次数不随改动项数量增长。证据追加与撤销沿用现有逐条审计协议，其写入次数随自述数量增长。

依据与异议在提交时分别通过 append_evidence 写为 restricted self_report，并附 supports / contradicts 关系。Evidence provenance 仅记录入口、客户端和请求 ID，不含聊天历史。撤回使用 retract_evidence，保留原始内容和引用并留下审计。提交、证据、审计及本人收件箱在调用者同一事务中。管理员查看请求也隐藏依据、异议原文，沿用受限证据的正文权限。

InboxItem 使用租户复合外键，只能查询和标记本人的条目。question_id 本阶段只有可空字段，由 NERE-95 补充外键。历史请求 revision 回填为 1；降级保留请求与证据，把 import 来源归为旧版 manual，移除本阶段新增字段和收件箱。

未引入新依赖。放弃各入口自行实现权限、请求写入和工具 schema，以免它们的行为分叉。
