# 管理功能对照表

新版管理台保留手工操作，管理助手与手工页面遵守相同权限。下表每行对应一项功能；反引号内为管理功能登记表的 `name`。查询可直接执行，改动先出确认卡，点击后才生效。登记表新增功能时须同步本表，后端测试会检查覆盖。

| 原位置 | 功能 | 新位置（菜单 / 标签页） | 管理助手功能名 |
| --- | --- | --- | --- |
| 同步与起草进度 | 今天要处理的事、运行状态 | 首页 | `get_today` |
| 同步与起草进度 | 同步记录、起草计数与失败原因 | 首页 | `get_progress` |
| 同步与起草进度 | 开始起草、重试失败员工（需先打开总开关） | 数据与 AI / 模型、首页 / 初始画像 | `start_initial_drafting` |
| 同步与起草进度 | 预览超期未过目人数 | 首页 / 初始画像 | `get_initial_handover` |
| 同步与起草进度 | 确认批量送审 | 首页 / 初始画像 | `hand_over_initial_profiles` |
| 同步与起草进度 | 重试失败的同步 | 首页 / 同步记录 | `retry_sync_run` |
| 争议 | 列出待处理争议 | 人员 / 争议；首页待办 | `list_appeals` |
| 争议 | 查看争议详情 | 人员 / 争议；人员详情 | `get_appeal` |
| 争议 | 允许重新提交或维持原判断，填写说明；不能处理自己提出的申诉 | 人员 / 争议；人员详情 | `resolve_appeal` |
| 人员变动 | 上传任意格式材料或描述，核对后确认新增、调整和明确离职 | 管理助手 | `draft_roster_from_material` |
| 名单 API | 上传及应用名单（仅 API） | 无界面入口；应用接口供人员弹窗确认单人变动 | NOT_FOR_ASSISTANT：界面已删除，名单变动走 draft_roster_from_material |
| 员工与账号 | 开关员工档案固定字段（可撤销） | 人员 / 员工与账号 / 档案字段 | `update_employee_fields` |
| 名单历史 | 查看导入历史 | 管理助手；审计日志 | `list_roster_imports` |
| 按材料整理画像 | 上传对话附件或直接描述，预览后创建待本人过目请求 | 管理助手 | `draft_profiles_from_material`（两人及以上二次确认） |
| 员工与账号 | 搜索员工、筛选部门及账号状态 | 人员 / 员工与账号 | `list_accounts` |
| 组织架构 | 查看部门层级、经理和在职人数，点部门查看人员 | 人员 / 组织架构 | `get_org_chart` |
| 员工与账号 | 查看正式画像及可见证据引用 | 人员 / 人员详情 → 查看员工画像 | `get_employee_profile` |
| 员工与账号 | 单人入职，含岗位序列、工作地点、入职日期（需开启） | 人员 / 新增员工 | `onboard_employee` |
| 员工与账号 | 调岗、改职位和上级，含岗位序列、工作地点、入职日期（需开启） | 人员 / 人员详情 | `transfer_employee` |
| 员工与账号 | 单人或批量离职 | 人员 / 员工与账号、人员详情 | `offboard_employees` |
| 员工与账号 | 发放账号、重新生成待首次登录账号文件 | 人员 / 员工与账号 | `issue_accounts` |
| 员工与账号 | 重置密码 | 人员 / 员工与账号、人员详情 | `reset_password` |
| 员工与账号 | 单人或批量停用账号 | 人员 / 员工与账号、人员详情 | `disable_accounts` |
| 员工与账号 | 启用账号 | 人员 / 员工与账号、人员详情 | `enable_account` |
| 员工与账号 | 查看个人修改记录 | 人员 / 人员详情 | `list_audit_events`（按员工筛选） |
| 角色 | 查看管理员与经理授权 | 人员 / 角色 | `list_roles` |
| 角色 | 授予角色和部门范围 | 人员 / 角色、人员详情 | `grant_role` |
| 角色 | 撤销角色 | 人员 / 角色、人员详情 | `revoke_role` |
| 账号对应 | 列出、筛选账号和候选员工 | 人员 / Jira 账号匹配 | `list_identities` |
| 账号对应 | 确认候选或选择员工关联（含所选候选） | 人员 / Jira 账号匹配、人员详情 | `link_identity`（多项可放同一确认卡） |
| 账号对应 | 解除关联并撤销该账号独有证据 | 人员 / Jira 账号匹配、人员详情 | `unlink_identity` |
| 自动起草 | 查看企业总开关 | 数据与 AI / 模型 | `get_drafting_settings` |
| 自动起草 | 打开或关闭企业自动起草（可撤销） | 数据与 AI / 模型 | `set_auto_drafting` |
| 起草补充说明 | 查看正文、版本和最近修改人时间 | 数据与 AI / 模型 | `get_drafting_note` |
| 起草补充说明 | 保存或清空正文；撤销时追加上一版正文 | 数据与 AI / 模型 | `update_drafting_note` |
| 数据来源 | 读取脱敏的 Jira 配置 | 数据与 AI / 数据来源 | `get_sources` |
| 数据来源 | 保存 Cloud / Data Center 凭据、日期、描述和定时同步周期 | 数据与 AI / 数据来源 | `update_jira_connection` |
| 数据来源 | 测试已保存的 Jira 连接 | 数据与 AI / 数据来源 | `test_jira_connection` |
| 数据来源 | 立即同步 | 数据与 AI / 数据来源 | `start_jira_sync` |
| 数据来源 | 下载工作记录模板 | 数据与 AI / 数据来源 | NOT_FOR_ASSISTANT：模板下载由助手给页面链接 |
| 数据来源 | 上传并导入 CSV / Excel 工作记录 | 数据与 AI / 数据来源 | `import_work_records`（先在上传框传文件） |
| 外部 AI 助手 | 读取开关、允许的客户端 | 连接 / 外部 AI 助手 | `get_mcp_setting` |
| 外部 AI 助手 | 开关外部助手、保存客户端名单 | 连接 / 外部 AI 助手 | `update_mcp_setting` |
| 模型 | 读取类型、模型名和可用状态 | 数据与 AI / 模型 | `get_model_setting` |
| 模型 | 保存模型、推理强度、启用开关和密钥 | 数据与 AI / 模型 | `update_model_setting` |
| 模型 | 测试已保存的模型连接 | 数据与 AI / 模型 | `test_model` |
| 模型 | 发起、查看、完成、取消 ChatGPT 授权，断开连接 | 数据与 AI / 模型 | NOT_FOR_ASSISTANT：在模型页面操作 |
| 内置机器人 | 读取提问指南、聊天保留期 | 数据与 AI / 内置机器人 | `get_bot_settings` |
| 内置机器人 | 保存指南和保留天数 | 数据与 AI / 内置机器人 | `update_bot_settings` |
| IM 入口 | 读取消息平台设置 | 连接 / IM 入口 | `get_im_settings` |
| IM 入口 | 保存飞书开关、邮箱绑定和凭据 | 连接 / IM 入口 | `update_im_setting` |
| IM 入口 | 测试已保存的连接、查看长连接心跳 | 连接 / IM 入口 | `test_im_connection` |
| IM 入口 | 企业微信、Telegram 设置 | 连接 / IM 入口 | `update_im_setting` 仅支持飞书，其余平台按登记项说明给网页链接；当前只支持飞书联调 |
| 审计日志 | 按时间、操作、操作者或对象查日志 | 更多 / 审计日志；各设置页修改记录 | `list_audit_events` |
| 首次设置 | 查看设置进度 | 管理助手 / 首次设置引导 | `get_setup_status` |
| 首次设置 | 修改企业名 | 管理助手 / 首次设置引导 | `update_org_profile` |
| 首次设置 | 标记步骤完成 | 管理助手 / 首次设置引导 | `complete_setup_step` |
| 首次设置 | 跳过可选步骤（包括暂不配置模型） | 管理助手 / 首次设置引导 | `skip_setup_step` |
| 设置 / 能力词典 | 查看、导出当前词典 | 能力与规则 / 能力词典 | `get_capability_dictionary`（导出按钮留在网页） |
| 设置 / 能力词典 | 下载 YAML / Excel 模板 | 能力与规则 / 能力词典 | NOT_FOR_ASSISTANT：模板下载由助手给页面链接 |
| 设置 / 能力词典 | 选择内置词典 | 管理助手 / 首次设置引导 | `apply_capability_template` |
| 设置 / 能力词典 | 上传、预览并发布词典 | 能力与规则 / 能力词典 | `import_capability_dictionary` |
| 起草建议 | 查看名称、说明、理由和证据条数（助手不读取证据标题或正文） | 能力与规则 / 能力词典 | `list_capability_proposals` |
| 起草建议 | 忽略建议，撤销时检查状态和同名建议 | 能力与规则 / 能力词典 | `dismiss_capability_proposal` |
| 设置 / 能力词典 | 网页编辑、发布新版本，可选择重新起草 | 能力与规则 / 能力词典 | `update_capability_dictionary` |
| 设置 / 审批规则 | 读取经理确认规则及历史 | 能力与规则 / 审批规则 | `get_approval_policy` |
| 设置 / 审批规则 | 改全企业、分类规则和员工过目期限 | 能力与规则 / 审批规则 | `update_approval_policy` |
| 设置 / 定期提醒 | 读取提醒周期及证据较旧期限 | 能力与规则 / 定期提醒 | `get_update_settings` |
| 设置 / 定期提醒 | 保存提醒周期及证据较旧期限 | 能力与规则 / 定期提醒 | `update_update_settings` |

安全输入、上传、确认、放弃、撤销和会话恢复由管理员本人操作。它们在 NOT_FOR_ASSISTANT 中分别标为「密钥由创建人通过安全输入框直接提交」「管理员通过上传框提交文件，不由模型传入内容」「变更集只能由创建人在确认卡上操作」「助手自身的会话接口」。测试草稿连接也只能由创建人在确认卡上操作。助手没有点击确认或批准画像的工具。

## 只能由部署人员执行的命令

在实际部署目录中使用原来的配置和 Compose 项目名。不要把备份、凭据或命令输出粘贴给助手。

| 操作 | 命令 | 说明 |
| --- | --- | --- |
| 备份 | `./backup.sh` | [备份与保管](deploy.md#备份) |
| 恢复 | `./restore.sh backups/<备份文件>.dump` | 会覆盖当前数据，先备份；[恢复步骤](deploy.md#恢复) |
| 升级 | `./upgrade.sh origin/master` | [升级与回退](deploy.md#升级与回退) |
| 重置演示环境 | `docker compose down -v`，随后 `docker compose up -d --wait` | 删除此项目数据库，只用于可丢弃的演示环境；[重置说明](deploy.md#演示停止重置与冒烟测试) |

完整操作步骤见[管理员指南](admin-guide.md)。

对话附件上传和原文下载属于助手自身的会话接口（NOT_FOR_ASSISTANT），仅本人通过鉴权接口操作。文件提取文字随当前对话轮作为数据交给模型，不提供模型上传、下载或读取任意文件的工具；原有带预览的业务导入上传保持独立。

M4.2 的登记表与接口对照由 `backend/tests/admin_assistant/test_registry.py` 校验。真实模型与确定性评测任务共用 `backend/tests/eval/admin_assistant/tasks/`，新增覆盖开关、三个档案字段、组织架构、起草建议、补充说明、对话材料与对抗请求。旧 `import_profiles` 已删除，`draft_profiles_from_material` 只准备待本人过目的初始请求。详见 [M4.2 验收](m4.2-acceptance.md)。
