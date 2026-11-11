# ADR-0029：PR 路径过滤、后端分片与浏览器准备复用

状态：接受（NERE-328，大古 11-11 决定）

## 背景

普通 PR 的完整门禁约需 6–9 分钟；后端测试步骤约 275 秒，Playwright 每片重复构建服务镜像和生成客户端。master 的共享并发组会取消前一次合并的验证。大古要求 PR 按相关路径执行，master 和每晚执行全量，保留全部测试与严格度。

## 决定

共享两份 YAML 路径规则，现有 paths-filter action 直接读取。`docs/`、根目录 Markdown、各目录 README 和 issue 模板变更不启动后端、浏览器及 Compose 重任务；仅 Pi 目录变化不启动浏览器。不能忽略全部 Markdown：`templates/profile-import/SKILL.md` 是 API 发布的运行时资源，仍触发测试。生产安装覆盖 Compose、Dockerfile、安装/升级/备份/恢复、部署配置和自身 workflow；未识别的路径保守执行。没有 workflow 顶层 paths 过滤，原有八个必需检查名保留。汇总门禁只有在过滤成功，且测试全部成功或按过滤明确跳过时通过；空输出、过滤失败、测试失败和取消都不能通过。

所有工作流在 master push 和每日 UTC 19:17（北京时间次日 03:17）执行全量。PR 按编号共用并发组并取消旧运行；其他事件按 run ID 隔离，保证连续 push 时运行中和排队中的验证都不会被后续 push 替换。不引入 merge queue，不改变 agent 自动合并流程。

后端用 pytest-split（MIT）按 least_duration 算法拆为三片；没有历史耗时时按数量均分，新增用例自动参与。每片保留 xdist loadgroup、独立数据库及迁移模板；roster_performance 六个场景只在第一片额外串行执行。汇总检查三份数据存在后合并 coverage，仍要求总覆盖率至少 90%，不对单片要求全局覆盖率。JUnit artifact 用于核对用例总数和耗时。

Playwright 前置 job 生成一次客户端、构建一次 backend / pi 镜像，导出压缩 artifact，三片各自加载后用 --no-build 启动。Chromium 按操作系统、架构与锁文件缓存；缺失时恢复安装，不跳过浏览器测试。每片仍有独立 Compose、一个 worker，保留 --fail-on-flaky-tests 和失败 trace。

## 代价与边界

相比 ADR-0028，接受镜像 artifact 上传、下载和额外 job 调度成本，以减少三次重复构建及准备。分片耗时和端到端关键路径分别测量；前置 job 增加依赖链，不能将分片节省直接当作整轮节省。镜像 artifact 只保留一天，报告保留七天。首次无 Chromium 缓存会更慢。

不更改业务代码、数据库结构、证据协议、权限、测试断言、重试或覆盖率来源。迁移测试已经按 worker 共用模板并按用例克隆数据库，继续保留隔离语义；后续根据 --durations=25 实测再决定是否需要进一步优化准备。

共享规则使用本地 checkout 中的 YAML，不使用 self-repository action：当前 runner 在下载仓库 action 归档时会被仓库已有的断链符号链接阻断。没有修改这些链接或放宽 zizmor 检查。
