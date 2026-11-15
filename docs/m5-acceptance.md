# M5 验收记录

核对基线：`master e1e67c542ce047f669838770ccf3c41cc624db73`，2026-11-15；对应父 issue NERE-21，记录任务 NERE-628（S17）。M5 按 E26 只交付功能、文档和回归，不发布。S1～S14 及审查修复均已合并，S15、S16 已由技术规划核对为 done。

本记录对照合并后的代码、自动测试和 issue 结果，不重复运行业务测试。自动测试证明确定性行为；外部 MCP 实测、模拟角色操作各有自己的验证边界，不能互相替代。测试路径均从仓库根目录起算，PR 链接均指向本仓库。

## 依据与时间口径

| 出处 | 本记录采用的内容 |
| --- | --- |
| NERE-21「M5 规划」，2026-11-14 15:50 UTC | S1～S17 分工、覆盖率放在员工页、各入口工具边界、迁移链及不做事项 |
| NERE-21 第 1 阶段核对与收尾，11-14 21:38、21:54 UTC | S1～S6、NERE-629、NERE-630 的 PR、检查通过及审查结论 |
| NERE-21 第 2 阶段核对与收尾，11-14 22:34、22:46 UTC | S7～S11、NERE-631 的 PR、检查通过及读取权限核对 |
| NERE-623、NERE-624、NERE-625 结果评论 | 插件指南核对、外部客户端实测、角色与运维指南交付 |
| NERE-626 11-14 22:44、22:48、22:50 UTC 结果及澄清 | nere497 升级、贺长青回归与两次补测，以最后补测为准 |
| NERE-627 11-15 02:03、02:06 UTC 结果，02:58 UTC 核对通过 | 十位经理逐项结果、部门行补测、模拟操作及越权抽查限制 |
| NERE-21 11-15 02:49 UTC 权限开通评论；NERE-627 02:58 UTC 核对 | `im:resource` 于北京时间 10:47 开通，S16 未复测 |

上述评论时间使用 UTC，便于对应平台原始记录；北京时间为 UTC+8。例如 S16 的 02:03、02:06 分别是北京时间 10:03、10:06。

## 第 1 阶段：遗留问题 1.1～1.6

| 条目 | 结论与 PR | 自动测试文件及覆盖内容 |
| --- | --- | --- |
| 1.1 输入上限 | 已交付，S1 NERE-612 [#267](https://github.com/ft1148137/employee-profile-platform/pull/267)。陈述 200 字、说明各 4000 字、一次 50 条；表单、助手、MCP、JSON 与直接修改复用常量，超限用中文说明。只改等级且保留已发布旧长陈述可通过；旧超长草稿可保存，送审前须缩短 | `backend/tests/api/routes/test_m5_input_limits.py`：各入口超限、边界值、旧陈述、存量草稿送审及 MCP schema；`frontend/tests/m5-input-limits.spec.ts`：字数、提示和阻止预览 |
| 1.2 恢复在职的上级 | 已交付，S2 NERE-613 [#269](https://github.com/ft1148137/employee-profile-platform/pull/269)。管理助手恢复在职时提示直属上级未设置；取最近已结束的汇报线，原上级在职且无循环才允许恢复。网页员工详情通过原调岗预览确认，不自动恢复 | `backend/tests/api/routes/test_m5_rehire_manager.py`：多人提示、材料带上级、上级离职、循环、Transfer 审计、普通员工/经理拒绝及跨企业；`frontend/tests/m5-rehire-manager.spec.ts` |
| 1.3 旧初稿先核对 | 已交付，S3 NERE-614 [#268](https://github.com/ft1148137/employee-profile-platform/pull/268)。未看最新预览返回 409，网页打开预览，助手返回新卡，核对后再送审；不静默对齐、不覆盖正式值。审查修复 NERE-630 [#274](https://github.com/ft1148137/employee-profile-platform/pull/274) 将提示时间按企业时区显示，去掉内部工具名 | `backend/tests/api/routes/test_m5_stale_draft_confirm.py`：直接提交、过期预览、MCP 和助手确认；`frontend/tests/m5-stale-draft-confirm.spec.ts`；`backend/tests/api/routes/test_m5_stale_draft_time.py`：两企业时区及结构化时间保持 ISO |
| 1.4 部门能力重试 | 已交付，S4 NERE-615 [#266](https://github.com/ft1148137/employee-profile-platform/pull/266)。失败记录 `capability_failed_at`，按部门冷却 24 小时后再调用模型，成功清除失败时间；不再每小时重复失败调用 | `backend/tests/domains/test_m5_department_defaults_retry.py`：冷却、24 小时边界、部门隔离、成功重算、迁移往返 |
| 1.5 撤回证据标题与流式错误 | 已交付，S5 NERE-616 [#264](https://github.com/ft1148137/employee-profile-platform/pull/264)。建议不展示已撤回证据标题；流式失败显示按异常类型映射的中文原因，不回传上游原文或凭据 | `backend/tests/api/routes/test_m5_proposal_titles_stream_errors.py`：有效/撤回证据、安全错误和模型失败；`frontend/tests/m5-stream-errors.spec.ts`：建议与错误显示 |
| 1.6 企业时区 | 已交付，S6 NERE-617 [#271](https://github.com/ft1148137/employee-profile-platform/pull/271)。默认 `Asia/Shanghai`，管理员设置 IANA 时区；直接修改通知、无时区工作记录和待办按企业时区处理，三条指定 shortcut 已去除。待办每小时检查、企业当地 09:00 发出 | `backend/tests/api/routes/test_m5_org_timezone.py`：设置权限与审计、通知、材料预览、时间偏移、当地日期/小时和迁移往返；NERE-629 [#272](https://github.com/ft1148137/employee-profile-platform/pull/272) 的 `backend/tests/test_m5_worker_periodic_isolation.py` 隔离手动 worker 测试的定时任务 |

## 第 2 阶段：新功能 2.1～2.4

| 条目 | 结论与 PR | 自动测试文件及覆盖内容 |
| --- | --- | --- |
| 2.1 按能力找人 | 已交付，S7 NERE-618 [#263](https://github.com/ft1148137/employee-profile-platform/pull/263)、S8 NERE-619 [#270](https://github.com/ft1148137/employee-profile-platform/pull/270)。网页、员工和经理助手（网页/飞书）、MCP 共用服务；仅管理员或管辖经理可查。支持能力、最低等级、状态、证据日期、部门对应能力及企业启用的三项档案字段；结果可打开画像，不含证据正文 | `backend/tests/api/routes/test_m5_people_search.py`：范围、筛选、分页、字段白名单、首次发布人和有效证据；`backend/tests/api/routes/test_m5_people_search_tools.py`：助手、飞书网关、MCP、身份篡改及真实 Pi 配合合成模型；`frontend/tests/m5-find-people.spec.ts` |
| 2.2 覆盖率 | 已交付，S9 NERE-620 [#265](https://github.com/ft1148137/employee-profile-platform/pull/265)。管理员「员工」和经理「我的团队」上方共用覆盖率组件，显示未知、已发布、存在争议、待本人过目、待审批及积压。展开后每部门行只计本部门在职员工，点击过滤下方名单；上方合计保持全部可见范围。网页管理助手提供同口径统计；Jira 失败仅管理员可见 | `backend/tests/api/routes/test_m5_coverage.py`：状态与百分比、空部门、经理/管理员范围、跨企业、管理助手同口径、积压及最近同步；`frontend/tests/m5-coverage.spec.ts`：角色范围和部门过滤 |
| 2.3 已发布画像导出 | 已交付，S10 NERE-621 [#273](https://github.com/ft1148137/employee-profile-platform/pull/273)。找人页与登录接口下载 CSV/JSON，导出全部可见在职员工的已发布判断，不随页面筛选或分页变化；无陈述、证据标题、原文、explanation 或受限正文。每次成功写 `profile.exported` 审计；字段协议见 [导出说明](profile-export.md) | `backend/tests/api/routes/test_m5_profile_export.py`：范围与拒绝、字段白名单、有效证据、首次批准人/时间、审计、105 条以上、空结果、BOM 及公式防护；`frontend/tests/m5-profile-export.spec.ts`：下载与页面过滤无关 |
| 2.4 Worker 监控 | 已交付，S11 NERE-622 [#275](https://github.com/ft1148137/employee-profile-platform/pull/275)。worker 定时写单行心跳；健康检查先执行 Procrastinate 检查再判心跳，超过阈值失败，启动宽限 120 秒；网页状态卡及管理员网页/飞书提醒，同次停顿只提醒一次。NERE-631 [#276](https://github.com/ft1148137/employee-profile-platform/pull/276) 修正收件箱时间为企业当地时间 | `backend/tests/domains/test_m5_worker_heartbeat.py`：心跳、超时/恢复、健康命令、状态卡、跨企业提醒及去重、IM 投递、迁移往返、安全错误；`backend/tests/domains/test_m5_worker_alert_time.py`：北京/东京显示与去重 |

### 读取权限：逐入口自动测试覆盖

读取范围复用服务端 Principal 和 `employee_scope_clause()`；经理可读获授权部门及全部下级部门，管理员可读本企业，离职员工不进入找人、覆盖率和导出。以下区分开放入口与未提供的工具，避免将“未提供”写成“该入口实测通过”。

| 功能 | 网页/API | 网页助手 | 飞书 | MCP |
| --- | --- | --- | --- | --- |
| 找人 | `test_m5_people_search.py::test_scope_privacy_and_first_publisher`：经理含下级、不含上级/兄弟部门，管理员本企业，员工 403、跨企业 404；逐字段检查无正文。Playwright 验证员工无入口、直接访问提示无权 | `test_m5_people_search_tools.py::test_web_manager_scope_and_privacy`：含兼任管理员的经理仍限管辖部门，范围外/外企业部门拒绝；`test_web_employee_and_admin_without_departments_denied`：普通员工及无管辖部门管理员无工具 | 同文件 `test_feishu_adapter_gateway_scope_and_old_binding`：真实 Feishu 适配器事件经过绑定、网关到共同服务，核对范围和脱敏；解绑、离职后的旧绑定不能再调用。平台网络与模型回复用合成实现 | 同文件 `test_mcp_scope_and_privacy`：经理/管理员范围、普通员工拒绝、外企业部门及伪造 org 拒绝；`test_mcp_departed_manager_old_token` 拒绝离职旧令牌；`test_tool_arguments_cannot_change_identity` 拒绝篡改企业、员工或管理员身份 |
| 覆盖率 | `test_m5_coverage.py::test_coverage_scope_and_statuses` 核对经理含下级、排除旁支/离职、管理员本企业及仅管理员见 Jira 失败；`test_coverage_denies_other_roles_and_tenants`：员工 403、双向跨企业 404；响应白名单无陈述、证据或凭据。Playwright 核对部门按钮范围 | 同文件 `test_coverage_admin_tool_matches_api`：`get_coverage` 与 API 一致，经理/员工拒绝，外企业管理员只得到其空企业统计，伪造 org 参数失败 | 未提供 `get_coverage`；员工和经理助手不新增覆盖率工具。S15 确认贺长青飞书不能获取全企业覆盖率符合设计，不是覆盖率飞书越权自动测试 | 未新增覆盖率工具；既有 `get_team_overview` 不等于 S9 看板。没有专门的覆盖率 MCP 自动测试，不计作该入口已验证 |
| 导出 | `test_m5_profile_export.py::test_export_scope_fields_and_audit`：经理本部门/下级、管理员本企业，排除离职、逐字段无正文且审计无数据行；`test_export_denies_other_roles_and_tenants`：CSV/JSON 均拒绝员工 403、外企业 404、未登录及个人令牌 401，无成功审计 | 未提供导出工具；不能用管理助手的其他读取能力冒充导出验收 | 未提供导出工具，不把飞书找人结果当作 CSV/JSON 导出 | 无导出 MCP 工具，个人令牌调用登录导出接口被上述测试拒绝；没有单独的 MCP 导出调用测试 |

找人各入口的响应检查不含证据标题、content、explanation、证据列表或受限正文；导出连陈述也不包含。确定性测试不证明所有自然语言问题都能正确选工具。覆盖率和导出的未开放入口符合父 issue 规划及 `docs/profile-export.md`，不扩展权限。

### 迁移与阶段审查

S4 → S6 → S11 依次引入 `b615001dc001 → b617001dc001 → b622001dc001`，各专用测试包含 upgrade/downgrade；链上只有一个 head。心跳表是系统级单行表，不含员工数据，不带 `org_id`，依据见 [ADR-0002](adr/0002-worker-and-compose-layout.md)。本记录不新增迁移。

技术规划阶段审查确认：没有证据改写路径、没有越权或数据丢失、没有删除/跳过测试或放宽门禁；NERE-630、NERE-631 两项 Should fix 均已合并修复。PR 检查通过与合并结论来自各关联 PR 和阶段核对；本记录不将 PR head 的结果当成每次 master 合并提交的 CI 结果。

## 「已定」7 条的落实

| # | 结论及出处 |
| --- | --- |
| 1 找人结果依据 | 已落实，#263、#270：展示陈述、等级、状态、证据数、最近证据时间、批准人/时间；无证据原文与受限正文。对应找人服务及工具测试的字段白名单 |
| 2 未定级判断 | 已落实，#263：最低等级筛选默认不匹配未定级，勾选「包含未定级」后可显示陈述。`test_m5_people_search.py::test_filters_and_pagination` 及 S15 收银能力 3→5 人补测覆盖 |
| 3 资质证书 | 本里程碑不做；父 issue 已定第 3 条移除证书表和证书证据两项 |
| 4 补充到原请求 | 本里程碑不做；保持等原请求审完或撤回后重提，父 issue 已定第 4 条 |
| 5 申诉经理直接修改 | 本里程碑不做；保持员工改回来再送审，申诉含义不变，父 issue 已定第 5 条 |
| 6 行业词典补支持职能 | 本里程碑不做；10 套行业词典不作本轮扩充，父 issue 已定第 6 条 |
| 7 飞书 `im:resource` | **已开通，未复测**。S15 发送合成文件时机器人回复「无法读取文件，请让管理员在飞书开放平台开通文件权限」。2026-11-15 北京时间 10:47 已为 `my_test_ai`（`cli_aa7a8cea48b8dc0b`）开通应用身份权限，属免审权限、开通即生效，无需发布新版本；S16 没有复测。出处：NERE-21 权限开通评论和 NERE-627 最终核对，不能据开通推断文件读取已通过 |

## 第 3 阶段：指南与外部接入

| 子任务 | 结论、PR 与验证边界 |
| --- | --- |
| S12 NERE-623 插件指南 | 已交付 [#262](https://github.com/ft1148137/employee-profile-platform/pull/262)。[扩展指南](extending.md) 核对三类扩展，新增 [sample_csv Connector](../examples/connectors/sample_csv/README.md) 和合成 CSV；复用既有校验、转换、入库，不登记生产数据源。`backend/tests/connectors/test_m5_sample_connector.py` 验证重复同步、更正只追加、企业隔离与未关联账号。结果评论记录连接器/ingest/扩展点 51 passed、3 个既有真实 Jira 测试 skipped；读取命令输出正确 JSON。IM Event 清单遗漏 attachment/attachment_error、IM 节仍写本地全量测试要求作为指南遗留，未在本 PR 扩大修改 |
| S13 NERE-624 外部 MCP | 已交付 [#277](https://github.com/ft1148137/employee-profile-platform/pull/277)，命令和逐步结果见 [外部 MCP 指南](mcp-clients.md)。宿主机独立 MCP Inspector 0.21.0（CLI 0.21.2）连接真实 HTTP 后端 `929b149`，nere624 数据库 15624、后端 18624，使用合成企业及本人个人令牌；未用 TestClient 代替实测、未调用模型。员工读画像/证据/词典→草稿→预览→提交→撤回→新建再提→回答问题全部成功，提交记录来源 mcp/client inspector，回答只追加 restricted 自述，正式画像未改。经理读待审/详情及找人成功；`test_mcp.py`、`test_manager_mcp.py` 18 项通过。未重验各厂商 OAuth 界面、模型自动选工具或经理提交审批决定；测试令牌已吊销，环境已清理 |
| S14 NERE-625 角色与运维文档 | 已交付 [#278](https://github.com/ft1148137/employee-profile-platform/pull/278)，管理员、员工、经理指南、部署、运维及功能表已对齐 S1～S11；合入 S13 后补上外部 MCP 索引。结果评论记录 193 个相对文件链接有效、lint/类型/build/preflight 通过；合成响应页面核对只证明文案入口，不冒充真实数据库、模型或飞书联调。未恢复根 README，无依赖变更 |

## nere497 回归：S15、S16

### S15：升级与贺长青（企业管理员）

NERE-626 记录：升级前可用 32 GiB，`upgrade.sh origin/master` 从 `e92b125` 升到 `929b149`，保留数据，生成升级前备份并完成迁移。长期环境未重新部署，未删库/卷；这些结论来自 S15 实际操作，不在 S17 再次操作环境。

| 项目 | 最终结论与依据 |
| --- | --- |
| 覆盖率 | 通过。网页在职 50 人、已发布 33、待审批 2、未知 15；部门合计一致，网页管理助手同口径。飞书不提供全企业覆盖率，按设计通过（NERE-626 澄清评论） |
| 找人 | 通过。食品安全日常管理、能独立完成复杂任务、证据起始 2026-11-14，查到郝宜川 1 人，等级/日期满足条件并可打开画像。最近一年起始 2025-11-15 的补测同样非空。原损耗能力零结果不算故障 |
| 包含未定级 | 通过。收银操作与现金管理、最低能完成常规任务，无日期筛选时由 3 人增至 5 人，新增周桂兰、田甜显示未定级；没有用该结果证明未定级人员有最近一年证据 |
| CSV / JSON | 通过。各 57 条判断，JSON 可解析，CSV 有 UTF-8 BOM，两条导出审计。Numbers 自动导出无响应，改用 Quick Look 预览，能力、等级、批准人中文正常；英文表头并未因此变为中文 |
| 后台任务 | 正常卡与最近心跳通过；共享长期环境不暂停 worker，停顿/红卡/提醒/恢复未在此环境实测，由 `test_m5_worker_heartbeat.py` 覆盖，S15 未重跑该测试 |
| 企业时区 | 北京时间显示通过；禾邻本轮未切换或保存其他时区 |
| 恢复在职提示 | 无可用场景，不制造离职/返聘；由 `test_m5_rehire_manager.py` 覆盖，S15 未重跑 |
| 飞书文件 | S15 发送合成文件失败，提示开通权限；后续已开通但未复测，以上述「已定」第 7 条为最新状态 |

S15 未新增 UX issue。结束时测试绑定已解绑、网页退出，数据库、后端、Pi、worker healthy，IM 与备份进程运行但没有容器健康检查，剩余 31 GiB。

### S16：十位部门经理

按 NERE-627 的 02:03 结果及 02:06 补测汇总。下表覆盖率数字为「待本人过目 / 待审批」，部门行仅统计本部门，不含下级；上方卡片合计始终统计全部可见范围。点击部门过滤下方员工列表，不能将上方合计当作所点部门人数。

| 经理 | 覆盖率及部门过滤 | 网页找人 / 网页助手 | CSV 与其他入口 |
| --- | --- | --- | --- |
| 乔砚秋 | 通过；东城区域运营部 5 人，0 / 0，补看部门行 | 原问题均 0；另查食品安全能力得到范围内郝宜川 | 下载通过，英文列名理解困难；网页助手越权抽查通过 |
| 鲁思勉 | 通过；滨江店 8 人、柳岸店 6 人，各 0 / 0，补看部门行；兼管两店共 14 人符合授权 | 原问题均 0 | 下载通过，英文列名理解困难；飞书标准查询 0，结束解绑 |
| 蒙晓渠 | 通过；南湖店 5 人、生鲜课 3 人，各 0 / 0，补看部门行 | 原问题均 0 | 下载通过，英文列名理解未独立验证 |
| 覃志波 | 通过；0 / 0，部门过滤通过 | 原问题均 0 | 下载通过，英文列名理解未独立验证；飞书标准查询 0，结束解绑 |
| 窦见青 | 通过；0 / 0，部门过滤通过 | 原问题均 0 | 下载通过，英文列名理解未独立验证 |
| 董铁军 | 通过；0 / 0，手机部门过滤通过 | 原问题均 0 | 下载通过，手机打开和理解列名未验证；飞书标准及确认按常规任务筛选的口语追问均 0，结束解绑；网页助手越权抽查通过 |
| 蔡雨晴 | 通过；0 / 1，部门过滤通过 | 原问题均 0 | 下载通过，英文列名理解未独立验证 |
| 霍振华 | 通过；0 / 0，部门过滤通过 | 原问题均 0 | 下载通过，英文列名理解未独立验证 |
| 孟雅琴 | 通过；0 / 0，部门过滤通过 | 原问题均 0 | 下载通过，英文列名理解未独立验证 |
| 项禾苗 | 通过；0 / 1，部门过滤通过 | 原问题均 0 | 下载通过，英文列名理解未独立验证 |

本轮按每人的原工作问题筛选「能独立完成复杂任务」和 2025-11-15 后证据，原问题全部零结果；助手说明无匹配不等于不会。仅乔砚秋补充一次非空查询，不能据零结果证明其他人的非空范围过滤。鲁思勉双店授权经技术规划确认，没有收窄成单店。

越权只抽查乔砚秋、董铁军两人在网页助手问财务部的人，均拒绝或说明只能查管辖范围，未给出财务姓名。这不代表十人或所有入口的越权路径都完成实际抽查；完整的确定性范围覆盖见上方自动测试矩阵。

这是按设定模拟的网页操作，非十位真实经理本人参加。统一助手问题补足了能力、等级和日期，不能视作自然表达或数字素养充分验收。CSV 经 Quick Look 预览中文值正常，不能证明每位经理读懂列名。截图压缩包及三人部门行补图保存在 NERE-627 结果附件，本仓库不复制聊天或业务截图。

结束时数据库、后端、Pi、worker healthy，IM/备份容器运行；飞书绑定已解除，网页退出，未改配置或删数据，剩余约 31 GiB。S16 没有复测开通后的文件权限。

## 遗留与不在 M5 范围的内容

| 项目 | 结论与后续边界 |
| --- | --- |
| NERE-632：CSV 表头与状态代码英文 | 新 UX issue，backlog，不挡验收。当前字段协议确实使用英文表头/key；下载及中文值正常不等于非技术用户能理解，待后续排期 |
| NERE-633：上方合计不随部门筛选变化 | 新 UX issue，backlog，不挡验收。部门行可查看本部门数字；上方维持全范围、无区域含下级汇总属于当前设计，技术规划已核对，不宣称已改善此体验 |
| 插件指南 IM 节 | S12 留下 Event 附件字段清单和本地全量测试文字问题；见 NERE-623 结果及 #262 描述，本次不改其他指南 |
| 找人与导出规模 | 第 2 阶段审查记录：扫描快照适合当前几十至几千人规模；导出按能力重复查询可能变慢，尚未做大企业性能验收，未引入检索表或批量优化 |
| 飞书文件开通后效果 | 已开通，未复测；不再把当前权限写为未开通，也不推断收发已成功 |
| 既有超长初稿 | 第 1 阶段审查记录：旧 JSON 导入草稿超过新上限时，送审要先改短；不截断历史数据 |
| README 与仓库信息 | 按 E26 留给发布里程碑：不新增/恢复根 README，不改仓库简介/topics；发布时再处理旧 README 备份和相关链接 |
| 许可证与发布材料 | Apache-2.0 发布及全量依赖许可证核验（含间接 LGPL 依赖）、贡献指南、对外宣传均留给发布里程碑；本记录不宣称完成核验或发布 |
| HTTPS 与其他平台 | HTTPS 部署实测留给发布里程碑；企业微信/Telegram 按 E18 搁置，本轮未测 |
| 调配及其他对象 | D7 跨部门调配继续搁置，不做绩效、薪酬、晋升判断、自动排班；证书、原请求追加、直接修改申诉、词典扩充按已定第 3～6 条不做 |

M5 功能交付与有限回归的结论由上述 PR、测试文件和 issue 记录支撑；未复测、未提供入口及 UX 遗留保留在案，不将它们写成已验证能力。
