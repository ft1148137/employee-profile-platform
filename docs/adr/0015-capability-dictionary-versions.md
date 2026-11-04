# ADR-0015：能力词典换版与历史判断

状态：采纳（NERE-91，2026-11-04）。

管理员上传 YAML 或 Excel，自定义能力与等级。每次保存创建递增版本；组织行锁串行化保存，保留数据库的单 active 版本唯一索引。确认必须携带预览时的版本号，过期返回 409。旧版本沿用 `retired`，表示已被新版本取代，不引入同义状态。

Claim 和 ProfileVersion 不迁移、不改写。读取当前画像时按能力 key 对应当前定义；旧 key 移出词典时仍在只读区域展示。等级 key 已删除时读取原定义的描述并标注「等级已调整」。发布版本仍按冻结的 schema 与 claim 引用读取，不复制证据正文。改 key 等于删旧、新增；不做能力合并或回滚。

同一事务按请求 id 顺序锁定本企业 draft、pending_owner 请求，按 key 重映射定义。删除的 key 同时移除改动项及引用关系，保留证据本体；请求即使变空也保留状态。受影响请求 revision 加一，审计并通过 S6 notify 写 dictionary_changed 通知。pending_review 保持原样，留给 M4 审批时处理。

失效等级不自动置空、不自动换级。预览警告，提交复用 S6 `_validate` 校验当前能力 key、等级 key 与本人目标；员工重新选择或明确清空后才能提交。旧 revision 返回 409。词典激活持组织锁再取请求锁；员工操作仅持请求锁，先提交成功的请求转入 pending_review 后不再重映射。

`ai_inference: false` 并入既有 excluded_capability_keys，保留原排除项与暂时不在词典中的排除 key；上传 true 不重新开启既有敏感能力。恢复推断由原自动起草设置页完成。导出反映当前有效排除设置。勾选重新起草仅排队已有 pending_owner 初始请求，继续遵守部门关闭、员工退出与模型必填规则，不调用真实模型验证。

不增加依赖。Excel 复用名单解析器；文件限制 2 MB、1000 项能力、每项 20 个等级，Excel 解压上限 20 MB。YAML 与 Excel 使用同一输入模型及校验。通知迁移将 kind 从 varchar(14) 扩为 varchar(18)，替换 CHECK；降级先删除 dictionary_changed 通知，再恢复旧列宽与约束，其他通知保留。
