# ADR-0010：来源记录、未关联账号与证据粒度

状态：已采用。对应 NERE-58。

## 背景

Jira 和 CSV 需要共享写入协议，重复同步不能重复计数。Jira Data Center 迁到 Cloud 后用户标识会改变，员工关联可能晚于同步。证据仍遵守 ADR-0001 和 S2 的只追加协议。

## 决策

- Task 按企业、来源、记录编号唯一；Jira 统一用 `jira`，CSV 用 `csv:<来源名>`。WorkEvent 每个 `event_ref` 一条，可以更新执行人、动作和结果。调用方串行化同一来源的同步和关联事务，服务只 flush、不 commit。
- ExternalIdentity 可以没有员工和关联时间，保存来源提供的显示名、邮箱。写入不建立或改变员工关联，即使姓名、邮箱完全相同。关联由 S10 管理；复合外键继续约束企业。降级删除未关联账号，并为已关联但时间为空的账号补降级时间，以恢复非空列。
- 每条记录、每名已关联员工一条 work_record / standard 证据；多个账号或角色合并到该员工，roles 去重排序。event_id 引用输入顺序中的第一条 WorkEvent。
- 证据正文只放记录的 key、url、title、type、status、created、resolved、occurred_at、labels、components、project、detail、roles、source_updated_at；不放外部账号、姓名、邮箱。provenance 是调用方的同步元数据，不参与内容比较。
- 比较 active 证据时忽略 source_updated_at、restored_from。相同只计 unchanged；不同调用 S2 追加更正并取代旧证据。没有 active 时追加；命中历史内容时沿历史链加入 restored_from 后追加，保持旧状态不变。CSV 无更新时间、字段改回旧值也按同样方式避免历史哈希冲突。
- 只有全部参与账号都已关联时，才撤销已不在记录里的员工证据；空参与列表也适用。存在未关联账号时保留旧证据，因为可能只是员工换了账号。取代和撤销的 system_worker 审计与数据在同一事务内写入。
- Task attributes 保存 labels、components、project、detail、resolved、people，并补存 occurred_at；SourceRecord 补充可空 resolved。后二者弥补任务输入表的遗漏，避免 CSV 没有创建时间时无法还原发生时间。时间统一成 UTC，避免相同时刻因时区表达不同产生重复。
- rederive_identity 只读取本企业、本来源且 people 包含指定账号的 Task，重跑人员、事件和证据推导，不改账号元数据和 Task。新增证据 provenance 标记 task_id 和 identity_linked；既有证据保留原 provenance。不访问外部服务，不处理解除关联。

## 结果与代价

Cloud 和 Data Center 使用相同 issue key 和来源，Task 不重复；新账号先未关联，旧证据保留。管理员重新关联到原员工后，正文无账号标识且角色不变，因此证据不增加。

未关联账号会延迟撤销，应在账号对应界面处理。Task 中保留来源账号信息供重建使用，但证据正文不复制这些信息。WorkEvent 可更新，不充当冻结的证据正文；已发布画像依然只引用证据。来源编号和 event_ref 的生成、事务重试、同步互斥由连接器和 S9/S10 负责，不引入接口或外部调用。
