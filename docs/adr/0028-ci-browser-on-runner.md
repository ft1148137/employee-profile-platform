# ADR-0028：CI 浏览器运行在 runner 上并复用服务镜像

状态：接受（NERE-277）

## 背景

两轮已有 M4.1 PR 的热缓存测量为 401 / 414 秒，仍超过五分钟。Playwright 三片的构建步骤为 87–118 秒；日志有 GHA manifest 导入和 CACHED，主要开销在大镜像层恢复、OCI 导出和 Docker 加载。随后 Compose 启动又重复构建及导出这些镜像。

飞书 SDK 首次导入加载约一万个模块。冷字节码在 coverage 跟踪中编译会放大该用例开销。M2 验收仍需完整的 37 个任务、73 项工作事件和 12 名员工，不能通过减少这些断言来提速。

## 决定

保留三个 runner、三个独立数据库、每片一个 Playwright worker 和原汇总门禁。使用已有 Bun 依赖在 runner 上安装匹配锁定 Playwright 版本的 Chromium，直接访问容器后端发布的前端；backend、worker、mock-jira 仍在 Compose 网络中运行，Jira 测试地址仍由后端解析。

每片只 Bake backend / pi 镜像，`up --no-build --wait` 启动服务；普通 Compose smoke 同样复用 Bake 结果。生产安装与升级保留真实构建流程。本地浏览器容器的 Dockerfile 和 Compose 服务保留。

backend 在 coverage 前预编译依赖字节码，不更换覆盖率引擎、不排除 SDK 用例或应用代码。M2 模拟 Jira 的 TestClient 使用上下文管理，跨请求复用 ASGI portal。没有新增依赖或运行时组件，没有改变任何断言、重试、flaky 检查或性能门槛。

## 代价与边界

Chromium 安装与系统依赖安装有成本，但省去了整套浏览器镜像的缓存恢复、导出和加载，以及第二份前端依赖安装。没有增加镜像 artifact 生产者和分片依赖链：构建一次再共享会引入上传、三次下载及重新加载，在现有服务镜像仍需更新时不保证关键路径获益。

热缓存后台两轮均为四个 worker；model 组先串行执行，但之后该 worker 继续领取其他任务，末尾与其他 worker 的差距约十秒，因此保留现有 loadgroup 隔离。Runner 性能和大量数据库/fixture 操作仍会影响耗时，不能用单轮结果承诺稳定低于五分钟。

详见 [测量记录](../ci-performance.md)；PR 的实际数据写入 issue 结果评论。
