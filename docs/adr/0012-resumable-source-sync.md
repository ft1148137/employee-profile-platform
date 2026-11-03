# ADR-0012：同步入队和断点事务

状态：接受

同步记录和 Procrastinate 任务必须同时存在。复用现有 Procrastinate 的 `configure_task(connection=...)`，把 SQLAlchemy Session 的 psycopg 连接传入，在创建或重试 SyncRun 的同一个事务里入队，避免分别提交导致记录永久停在 queued。真实 Worker 用例覆盖此契约。

每个连接以 PostgreSQL session advisory lock 串行执行，锁绑定专用数据库连接，跨页提交仍保留，执行结束显式释放。Worker 退出断开连接后锁自动释放，管理员可重新入队超过 30 分钟没有更新的 running 记录。不能只用 Procrastinate 的 doing 锁：退出的 Worker 会留下 doing 任务，阻挡人工恢复。

每页的业务写入、游标与计数在同一事务提交，失败只回滚当前页。Jira 水位与 succeeded 状态在最后一个事务提交；complete 游标避免末页完成后重复拉取。重试保留游标，每次人工重试重新获得最多三次自动重试额度；Retry-After 优先，否则指数等待。未知异常只展示固定中文错误，审计只保存计数。

不增加新组件或依赖，不改变既有证据协议和权限范围。
