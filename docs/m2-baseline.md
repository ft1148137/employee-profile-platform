# M2 规则起草基线

规则方式已删除（NERE-86 / E16）。以下数字仅保留为 M2 历史记录，不代表当前模型方式的准确率。真实模型基线由 M3-S13（NERE-101）补测。

日期：2026-11-04（北京时间）。实现基线：`master` `821e17e4bf801f65a413f2261139e5ac0d7135ca`；数据集：栖光智联 `qiguang-aiot`；词典：`aiot-hardware`；方式：关键词规则，模型关闭。

运行 `uv run --project backend python examples/test-enterprises/evaluate_drafts.py qiguang-aiot`，需先启动隔离数据库并迁移。脚本读取仓库 `.env`（环境变量优先），创建 `eval-qiguang-aiot-随机串`，仅导入 org/roster 登记的身份、36 条模拟 Jira 和 32 条 PLM，起草后在 finally 删除临时企业。未使用 personas 的 account_truth、自述、经理评价或预置判断。

统计单位为「在职员工、能力 key」：26 名在职员工、71 项 ground truth；离职员工不参与分母。64 个起草项中 34 个命中 ground truth。这里只判断能力有无，不核验建议等级，misleading 仍归入其标注的真实能力集合并另列风险。

| 证据状态 | 被起草 / 真实能力 | 比例 |
| --- | --- | --- |
| documented | 20 / 22 | 90.9% |
| partial | 9 / 17 | 52.9% |
| undocumented | 0 / 25 | 0.0% |
| misleading | 5 / 7 | 71.4% |

| 指标 | 结果 |
| --- | --- |
| 额外改动项（不在真实能力集合） | 30 / 64，46.9% |
| undocumented 保持未知 | 25 / 25，100.0% |
| 精确率 | 34 / 64，53.1% |
| 召回率 | 34 / 71，47.9% |

misleading 被起草的 5 项：QG002/pcb_layout、QG005/pcb_layout、QG007/firmware_integration、QG011/rknn_conversion、QG020/ecn_bom_management。指派关系、评审角色和关键词共现会放大推断，不能把这些结果作为已确认能力。partial 漏掉 8 项、documented 漏掉 2 项，规则不能从未写出的实际贡献或历史经验恢复能力。

FW-205 与 PLM-0028 的重复经历没有互相写明编号，规则不会按标题相似度合并，会增加经验计数；此基线不为提高数字而改关键词或去重协议。

模型方式准确率：未测。早期规划称本机/CI 无可用模型；本轮仅按后续要求用真实 ChatGPT 对 5 名合成员工做起草与溯源验证，没有做模型准确率评测，见 [验收记录](m2-acceptance.md)。

微软公开说，早期用户验证 AI 推断技能的准确率在 80% 以上（[来源](https://techcommunity.microsoft.com/blog/Microsoft365CopilotBlog/people-skills-expanding-skills-intelligence-to-more-users-and-surfaces-across-mi/4497646)）。口径不同：微软数字是员工自己确认推断结果的比例；本基线逐条比对 `personas.yaml` 的真实能力，最接近的是精确率。两者不能直接比较，也不作为本项目通过门槛。

## 模型方式复测

在仓库根目录的 `.env` 配好数据库，完成迁移后，从 `backend/` 运行（密钥从 `MODEL_API_KEY` 环境变量读取，不写入命令行）：

```bash
PYTHONPATH=. uv run python ../examples/test-enterprises/evaluate_drafts.py qiguang-aiot --model openai/模型名
# 本地或通用兼容接口可加 --kind openai_compatible --base-url http://模型主机:端口/v1
```

脚本临时建立合成企业、模型设置和无登录用途的账号，退出时清理。它仅把工作记录和词典发给模型，不把 personas 的真实能力交给模型；评分仍使用保留的真实能力。假模型冒烟测试只验证流程，不能作为准确率结果。
