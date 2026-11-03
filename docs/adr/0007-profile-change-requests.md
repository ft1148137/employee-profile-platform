# ADR-0007：画像请求与正式判断分离

状态：已采用，NERE-51（M2-S1）；合并前由大古审阅。

## 背景与决策

M0 把候选判断和已发布判断存入同一张 Claim 表，审核状态可能与版本和进度不一致。新增租户内的 ProfileChangeRequest、ChangeItem、ChangeItemEvidence，提案的判断、解释、不确定说明及证据引用放在请求里。正式 Claim 仅存已发布判断，删除 Claim 和 ProfileProgress 上的 review_status；旧版本快照保持不变，读取时忽略旧 review_status。after 包含 statement、level、assessment_status 和可空 confidence，供后续批准发布使用。

请求只允许 draft → pending_review/withdrawn，pending_owner → draft/pending_review，pending_review → approved/changes_requested/rejected/withdrawn；其余状态为终态。本次不提供提交或批准接口，也不发布请求。

进度按 pending_owner → pending_employee、pending_review → pending_manager、当前判断 disputed → disputed、有发布版本 → published、其余 none 的优先级推算。批量读取当前判断和待处理请求，不随员工列表页大小增加查询次数。没有进度行时也返回其余三项默认值。当前判断排除被取代记录；同能力多条时选 created_at、id 最晚者，与画像读取一致。

## 数据迁移与回退

未被任何历史版本引用且不为 accepted 的 Claim 转为 manual 请求，candidate/pending_employee/pending_manager/rejected 分别映射 draft/pending_owner/pending_review/rejected。请求和改动项沿用 Claim 的 id、created_at；updated_at 等于 created_at，author 和 generated_by 原样复制 generated_by。after 保存四个判断字段，explanation 对应改动项，uncertainty 为空，证据关系原样迁移。同能力有保留的当前 Claim 时为 modify 并引用它，否则为 add。版本引用过的 Claim 保留。

待转换 Claim 的证据链接 note 非空时升级报错，指出 Claim id，事务回滚；不静默丢弃数据，不扩充证据协议。

降级把有 after 且属于 draft/pending_owner/pending_review/rejected 的改动项还原为 Claim：沿用改动项 id、created_at、能力和 explanation，员工取请求；判断字段取 after，缺失 confidence 时为空。状态反向映射，generated_by 为空时取 author。modify 的 supersedes_claim_id 取 target_claim_id，其余为空；证据链接保留 evidence_id、relation，note 为空。保留的 Claim 一律 accepted，进度按上述规则回填。

正常旧数据的上述字段和链接可以逐字段往返。三种替代关系会规整且不回退：保留 Claim 指向被转换 Claim 的指针置空；被转换 Claim 无指针但同能力有当前 Claim 时改指向它；指向非当前或被转换 Claim 时改指向保留的当前 Claim，没有则为空。

回退保留判断字段和证据引用，不恢复新请求的分组和 origin：每个改动项变为一条旧 Claim，再升级分别成为 manual 请求。种子导入按 origin 和 generated_by.seed_ref 识别请求，旧演示库迁移过来的四条 manual 请求不会重复；对已回退的新 initial_sync 请求，重新导入会补回初始请求。
