# ADR-0006：部门经理的管辖范围包含下级部门

状态：采用。修订 [ADR-0003](0003-authz-and-tenant-context.md) 的部门经理范围。

## 背景

NERE-6 的 E8（2026-11-03 大古确认）明确：部门经理的管辖范围为获授权部门及其全部下级部门。现有所有读取权限，包括受限的自述和经理评价正文，都按这个范围生效。

## 决策

在构造 Principal 时，用一条递归 CTE 沿 `department.parent_department_id` 向下展开经理授权部门。起点和递归每层都限定 `org_id`；使用 `UNION` 去重，避免重叠授权重复及层级环导致无限递归。没有经理授权时不查询。

`get_org_principal` 和 `/me/memberships` 共用 `managed_scope`，返回展开后的 `managed_department_ids`。读取接口沿用 `can_view_employee`、`employee_scope_clause` 和 `can_view_evidence_content`，不各自计算范围。每次请求使用当前结构，不保存或缓存展开结果，不加表或迁移。名单重新导入、部门换上级之后，下一次请求自动生效。

企业管理员、员工、系统 Worker 及写操作的权限不变；员工列表的部门筛选仍只匹配所选部门本身。

## 代价与风险

有部门经理授权的每次组织请求多一条查询；`/me/memberships` 对每个有经理授权的企业分别查询。

授权给上层部门等于开放整棵子树，包括 restricted 正文；现有授权在上线后立即按新范围生效，管理员应按最小范围授权。M4 的审批路由、M5 的按能力找人都按这个范围设计，今后更改语义需要同时核对这些模块。

无新增依赖、无数据库结构变更。
