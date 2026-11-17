# 发布内容草稿

**草稿，等大古决定公开发布时再放到根目录和仓库设置。** 本文件不代表已公开发布。根目录不恢复 `README.md`，不设置 GitHub 仓库简介、topics 或其他仓库设置。

内容基于 `20fe9adb` 的现有指南，仅描述已实现的行为；发布前需对照最终版本更新。本 PR 已补齐 Apache-2.0 官方全文、保留模板 MIT 全文并新增根 NOTICE，见[许可证核验](licenses.md)；公开发布和制品分发仍须满足各第三方许可。下面 README 内的相对链接按本草稿所在的 `docs/` 编写；搬到根目录时须把文档链接加上 `docs/`，指向仓库根的 `../CONTRIBUTING.md`、`../LICENSE`、`../NOTICE`、`../licenses/` 则去掉 `../`。

## README 全文草稿

### 企业员工画像与跨部门协同平台

可自托管的企业员工画像与协同平台，让跨部门资源协同从「靠印象和关系」转向「基于可信能力与事实」。以可追溯的证据维护员工能力画像，帮助员工更新、经理确认，以及在授权范围内找人和讨论部门需求。

A self-hosted platform for evidence-based employee profiles and cross-department collaboration, with employee review, manager approval and tenant-scoped access.

### 核心流程

1. 管理员建立企业、部门、员工和能力词典，配置 Jira Cloud / Data Center 或导入 CSV 工作记录，按需启用模型和起草周期。
2. 系统结合工作记录和能力词典起草画像，先交员工本人核对。员工也可通过网页、内置助手、外部 MCP 助手或 JSON 导入提出变更。
3. 经理逐项批准、退回或拒绝；批准在同一事务发布新的画像版本。企业关闭某类能力经理确认时，本人确认后发布为「暂定」。员工可以查看结果和申诉。
4. 管理员和有权经理可直接修改他人画像，修改后通知本人；助手操作需要有权用户点击确认卡。任何人不能审批自己的请求，也不能直接修改自己的画像。
5. 在授权范围按能力检索已发布画像。企业开启「需求与容量」后，可跨部门提问、提需求和答复，查看周级容量、安排部门内分配、处理延期申请，并模拟和调整协调方案。方案需由人核对，模型不能代替业务确认。

管理员可配置网页及 IM 入口；飞书、企业微信、Telegram 的真实平台接入边界见[管理员指南](admin-guide.md)。当前指南记录的 IM 验证主要使用假服务，部署前须实测真实权限和收发。协调 Agent 的真实模型评测与限制见[M8 评测](m8-coordination-eval.md)，不承诺每次都生成可用方案。

### 隐私和权限原则

- 每行业务数据带企业上下文，服务端统一计算授权范围。部门经理的管辖范围包含获授权部门及下级，不向其他部门开放个人画像和受限证据。
- 证据只追加，纠错保留撤销或更正记录；已发布版本冻结判断和证据引用。
- 未知不等于不会。Jira 的 assignee 只能说明参与关系，只有 Jira 来源支持的判断最多为「暂定」；confidence 不是概率。
- 默认不保留画像更新聊天原文，只保存员工确认的受限陈述。各类助手会话和附件的具体保留、删除规则见[员工指南](employee-guide.md)及[管理员指南](admin-guide.md)；IM 平台和终端可能保存外部副本。
- AI 和 MCP 按当前用户身份、权限调用同一服务层。企业可停用模型；起草需要模型，停用后仍可使用手工页面。模型、IM 连接由管理员显式开启，启用前核对数据外发范围。

### 不做什么

不做绩效、薪酬或晋升判断，不默认采集私人资料，不抓取 Jira 评论、工时和附件，不接 SSO。协调方案是供人判断的建议；本草稿不把尚未交付的原子承诺、借调和自动调配写成已有能力。

### 部署

使用 Docker Compose 自托管。服务器管理员按[部署、备份与维护指南](deploy.md)运行安装器并把一次性初始化链接交给企业管理员；安装后的日常配置、员工更新与经理审批都在中文网页完成。登录名为员工邮箱，没有公开注册。

部署指南包含 HTTP、HTTPS 自签与域名证书的前提、备份恢复和升级步骤。正式环境不导入演示数据，示例中的企业和账号均为合成数据。

### 文档

- [文档索引](README.md)
- [企业管理员指南](admin-guide.md)
- [员工指南](employee-guide.md)
- [经理指南](manager-guide.md)
- [外部 MCP 接入](mcp-clients.md)
- [已发布画像导出](profile-export.md)
- [架构与权限](architecture.md)及[架构决策](adr/README.md)
- [开发环境](development.md)及[贡献指南](../CONTRIBUTING.md)
- [依赖许可证核验](licenses.md)

### 许可证

本项目按 [Apache-2.0](../LICENSE) 授权。部分代码源自 full-stack-fastapi-template，保留[原始 MIT 版权及许可全文](../licenses/full-stack-fastapi-template-MIT.txt)，来源说明见 [NOTICE](../NOTICE)。第三方组件保留各自许可证和版权；分发制品时按[核验报告](licenses.md)处理声明、源码及其他再分发义务。

## GitHub 仓库简介草稿

可自托管的企业员工画像与跨部门协同平台：基于可信证据维护能力画像，支持员工更新、经理审批、按能力找人及部门需求协同，采用企业隔离和服务端权限控制。

## 建议的 topics

`employee-profiles`、`evidence-based`、`cross-department-collaboration`、`self-hosted`、`fastapi`、`react`、`typescript`、`postgresql`、`docker-compose`。

这些只是待维护者确认的内容，不在本次任务中写入仓库设置。
