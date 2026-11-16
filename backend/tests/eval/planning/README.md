# Planning 规则引擎评测案例

`cases/` 中每个 YAML 是一个独立的合成企业案例。姓名和部门从 `examples/test-enterprises/` 的两份花名册读取；案例之间不共享企业数据。测试通过真实 Planning API 建 case、读取和校验方案，随后删除该案例的企业及用户。

## 字段

| 字段 | 含义 |
| --- | --- |
| `id`, `category`, `enterprise` | 案例标识、统计类别、模拟企业目录名 |
| `roles` | A 方经理、B 方经理、A 方员工的花名册工号 |
| `people` | 候选人的工号、发布等级、每周其他工作比例；可设 `weekly_other`、`missing_weeks`、`confirmed_days_ago`、`hire_days_ago`、`inactive` |
| `unavailability` | 工号及相对本周周一的起止日偏移 |
| `assignments` | 已有分配；`start_week: null` 是旧分配 |
| `demand` | 可覆盖默认的 `headcount: 1`、`allocation_percent: 50`、`weeks: 2` 和 `minimum_level: routine` |
| `expect.options` | 必须出现的方案种类、结论和可选的 finding code |
| `expect.absent_kinds`, `newcomer_required` | 不应出现的方案及新人方案是否必须出现 |
| `expect.denied`, `stale_after` | A 方角色的 404；建 case 后令指定员工离职，旧详情及新增方案均返回 409 |
| `expect.small_borrow`, `legacy_start` | 借调人数隐藏和旧分配起点断言 |
| `custom` | 自定义方案指定员工及期望的结论、finding code |

能力等级使用企业词典的 `needs_support`、`routine`、`independent_complex`。`weeks` 从运行日所在自然周起算，确认时间相对运行时刻，避免固定日期随时间失效。

## 运行

先按 NERE-698 的独立 PostgreSQL 端口 `15698` 设置 `DATABASE_URL`，再在 `backend/` 目录运行：

```bash
uv run pytest tests/eval/planning/test_planning_eval.py -q
uv run python scripts/eval_planning.py --case direct-01
uv run python scripts/eval_planning.py --report ../docs/m7-planning-baseline.md
```

报告只在全部案例通过后写入。每个案例比较 Planning 调用前后的全部 `capacity_*`、审计和通知行，并检查借调方案不含其他部门的个人标识或姓名。
