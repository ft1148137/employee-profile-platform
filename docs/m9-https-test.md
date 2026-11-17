# M9 HTTPS 部署实测

日期：2026-11-18（Asia/Shanghai）。任务：NERE-781，父任务 NERE-639。

结论：自签 HTTPS 安装、现有 Bearer 登录和退出链路、页面、模型未配置响应、MCP、备份与同提交升级均可用。按技术规划 11-18 的范围调整，**登录 Cookie 的 Secure 属性记为「当前认证机制不适用」**；平台使用 localStorage 中的 Bearer token，不需要引入 Cookie 认证。邮件记为「未实现邮件通道，不适用」。域名证书仅核对配置，没有申请。Reviewer 要求补齐的 HTTP 80 安装约束与浏览器证据见本文末尾补测，原安装、备份、升级实测未重复。

## 环境和隔离

| 项目 | 实测值 |
| --- | --- |
| master 基线 / 升级目标 | `20fe9adb9f57e1fbdac9afe0a0a4bb47d201ae0c` |
| 主机 | macOS 26.4.1（25E253），arm64 |
| Docker Engine / CLI | 29.1.3 / 29.1.3 |
| Docker Compose | v2.40.3-desktop.1 |
| 开始时可用磁盘 | `df -g ~`：23 GiB |
| 安装目录 | 开发工作区旁的临时克隆 `../nere781-https/`，直接克隆 master |
| Compose 项目 | `nere781`；隔离验证容器属于 `nere781-review` |
| 端口 | HTTP 18781、HTTPS 443；仅测试额外发布数据库 `127.0.0.1:15781` |
| 数据 | 安装器自带的合成演示企业；未挂载 ChatGPT 授权，未调用外部模型 |

启动前 `lsof -nP -iTCP:<端口> -sTCP:LISTEN` 和 `docker ps --format '{{.Names}} {{.Ports}}'` 均确认 443、18781、15781 空闲。已有 nere497 始终未操作。

生产叠加文件默认不发布数据库。本次为遵守本机隔离规则，使用仓库外的 `nere781.ports.yml`：

```yaml
services:
  db:
    ports: !override
      - "127.0.0.1:15781:5432"
```

在临时克隆中执行指定的安装命令，额外通过环境变量 `COMPOSE_FILE` 附加上述文件，设置 `POSTGRES_PORT=15781`、`API_PORT=18781`：

```bash
COMPOSE_PROJECT_NAME=nere781 EPP_ACCESS=https-selfsigned \
  EPP_ADDRESS=localhost EPP_HTTP_PORT=18781 \
  ./install.sh --demo --non-interactive
```

`COMPOSE_FILE` 的测试覆盖值为 `compose.yml:deploy/compose.prod.yml:deploy/compose.https-selfsigned.yml:deploy/compose.mock-jira.yml:../nere781.ports.yml`。安装、升级、备份和清理沿用它。每次启动完成后先执行 `docker compose -p nere781 port db 5432`，输出均为 `127.0.0.1:15781`，再执行后续验证；容器间数据库通信使用本项目网络中的 `db:5432`。

## 安装和容器

首次构建在 9.8 秒时失败：Docker Hub 的 `auth.docker.io/token` 返回 EOF，未进入启动步骤。同配置重试成功，耗时 **34.1 秒**；使用了主机已有构建缓存，不代表首次下载的耗时。安装器保留首次生成的 `.env`，未更换密钥。

`.env` 中两个非敏感配置如下；密码、token 和密钥不附入记录：

```dotenv
FRONTEND_HOST=https://localhost
COMPOSE_FILE=compose.yml:deploy/compose.prod.yml:deploy/compose.https-selfsigned.yml:deploy/compose.mock-jira.yml
```

`docker compose up -d --wait` 成功，安装结束和升级后状态一致：

| 服务 | 状态 |
| --- | --- |
| db、pi、backend、worker、mock-jira | running / healthy |
| proxy、im、backup | running；配置未定义健康检查 |
| prestart | exited 0，迁移与演示初始化完成 |

HTTPS 叠加文件清空 backend 宿主端口；唯一应用入口为 proxy，发布 18781→80、443→443。

## 逐项结果

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| HTTP 跳转 | 通过 | `curl -I http://localhost:18781/` 返回 `308 Permanent Redirect`，`Location: https://localhost/`；代理修复后复测相同 |
| 自签证书与登录页 | 通过 | `openssl s_client` / `openssl x509` 显示 subject 与 issuer 均为 `CN=TRAEFIK DEFAULT CERT`，有效期 2026-11-17 至 2027-11-17；agent-browser 忽略证书警告后打开 `/login`，显示「邮箱」「密码」「登录」 |
| 演示账号登录 | 通过 | 使用安装器生成的随机演示密码登录 `employee.platform@demo.example.com`；登录接口 200，网页进入 `/assistant`；管理员演示账号也能登录 |
| 登录 Cookie 的 Secure 属性 | 当前认证机制不适用 | 技术规划已批准沿用 Bearer token；登录响应头明确没有 `Set-Cookie`，证据见补测响应头。不用 `document.cookie` 为空推断 HttpOnly Cookie，也不要求认证改造 |
| HTTPS Bearer 链路 | 通过 | 补测登录 POST 与受保护的 `/api/v1/users/me` GET 均走 HTTPS、返回 200；前者无 Authorization，后者存在该字段，只记录是否存在 |
| 退出登录 | 通过 | 点击「退出登录」后返回 HTTPS `/login`，localStorage 的 `access_token` 已删除；补测无 Authorization 的受保护请求返回 401，随后网页重新登录 POST 200 且不带旧 Authorization，详见补测 |
| 「需求与容量」 | 通过 | 默认关闭时显示「功能已关闭」；管理员从「能力与规则 → 需求与容量」启用后，`/capacity` 正常显示「待我审批」「团队负荷」「我提的需求」「收到的需求」，相关 API 返回 200 |
| 助手消息和代理响应 | 通过（模型未配置分支） | 员工在 `/assistant` 发一条合成消息，网页约 1 秒内显示「机器人需要先配置模型，你也可以在画像页直接修改」；修复后通过 HTTPS POST `/api/v1/bot/sessions/{id}/turns?org_id=…`，200、`Content-Type: application/x-ndjson`、`Cache-Control: no-store`、`X-Accel-Buffering: no`，约 140.3 ms 收到 final 事件及相同提示。该分支只有一个 final 事件，不据此声称测过真实模型多段输出；代理保留 `flushinterval=-1ms` |
| 通知里的网页地址 | 通过（有效通知实际打开） | 补测在 HTTPS 收件箱点击已有「初始画像待过目」通知的「查看画像请求」，打开本人画像页，显示「待你过目：系统起草的初始画像」；画像与变更请求接口均 200，完整地址见补测。原全零 ID 方案示例仅验证链接构造，不作有效方案已打开的证据；没有新增真实 IM 投递要求 |
| 邮件链接 | 未实现邮件通道，不适用 | 按技术规划调整，不增加邮件、SMTP 或投递验收 |
| HTTPS MCP | 通过 | `curl -k -D - https://localhost/mcp` 返回 HTTP/2 401；`WWW-Authenticate: Bearer resource_metadata="https://localhost/.well-known/oauth-protected-resource"`，没有连接错误或降级为 HTTP |
| 飞书回调 | 不适用（已核对） | 当前飞书入口使用出站 HTTPS / WSS 长连接，无公网 HTTP 回调路由，不应填写虚构的 `https://localhost/...` 回调。真实飞书入口和通知未测试 |
| 手动备份 | 通过 | `./backup.sh` 返回 0，生成 312684 字节的 `.dump`；本项目 db 内 `pg_restore -l` 成功解析，列表 638 行 |
| 同提交升级 | 通过 | 临时克隆干净时执行 `./upgrade.sh origin/master`，34.1 秒、退出码 0，升级前自动备份成功，共两份 dump；HEAD 仍为基线提交，HTTPS 健康接口 200，五个带健康检查的服务均 healthy |
| Traefik 多项目隔离 | 原配置有风险，修复后通过 | 见下节；修复后部署中创建带冲突路由标签的 `nere781-review` 容器，本项目 HTTPS 健康接口仍返回 200 / `true`，foreign 容器可直连而未接管代理 |

## 发现的部署问题和修复复测

原先两种 HTTPS 配置仅设置 `exposedbydefault=false`，会读取同一 Docker 上**所有** `traefik.enable=true` 容器；服务名固定为 `backend`，路由名固定为 `backend-https`。别的项目若采用同名路由、不同规则，就可能产生重复定义或误接入。即使 443 不能同时绑定，另一个项目的服务标签仍能被当前代理发现。

本 PR 在两份 HTTPS 叠加文件中增加 `Label(com.docker.compose.project, 当前项目名)` 过滤（实际表达式使用反引号），路由与服务名加 `${COMPOSE_PROJECT_NAME}` 前缀，并显式指定路由关联的服务。没有添加网络服务或依赖。

将两份修复后的叠加文件复制到临时克隆，执行 `up -d --wait --no-build` 后复测。另建自己的 `nere781-review` 项目，只启动一个不发布宿主端口的 Python HTTP 容器，加入主项目网络，并故意声明冲突的 `nere781-backend-https` / `nere781-backend` 标签。backend 内直连 `http://foreign:8000/` 为 200，说明容器确实可达；经本项目 HTTPS 请求健康接口仍为 200 / `true`（foreign 的 HTTP 文件服务不会返回该 JSON 布尔值），访问日志使用 `nere781-backend-https@docker`。传入 `Host: foreign.example.com` 后健康接口仍由本项目处理，未误用 foreign 的路由。

原安装文档还将 `.env.example` 的开发演示密码写成所有演示安装的密码。实测 `install.sh --demo` 会生成随机密码，默认不创建平台超管；已在部署指南更正。

## 域名证书：只核对配置

安装器的 `EPP_ACCESS=https-letsencrypt` 选择 `deploy/compose.https-letsencrypt.yml`。使用合成域名 `profiles.example.com` 和邮箱 `admin@example.com`，只运行 `docker compose config --format json`；**未启动域名证书代理，未发起任何 ACME 申请**。

| 核对项 | 结果 |
| --- | --- |
| 合并配置 | 通过；基础文件 + 生产叠加 + 域名证书叠加能解析，backend 无宿主端口 |
| 邮箱 | `--certificatesresolvers.le.acme.email=admin@example.com`；来自必填 `ACME_EMAIL` |
| 存储 | `nere781_traefik-certificates` 项目卷挂到 `/certificates`，ACME 存储为 `/certificates/acme.json` |
| HTTP-01 | 原配置使用 TLS challenge；本 PR 改为 `--certificatesresolvers.le.acme.httpchallenge.entrypoint=http`，http 入口 `:80` 发布到宿主 80，HTTPS 为 443 |
| 路由 | `Host(profiles.example.com)`，TLS resolver `le`，项目名前缀，显式关联项目内 backend |
| 重定向 | HTTP 入口重定向到 HTTPS，保留 HTTP-01 挑战入口；两者兼容，见 [Traefik ACME 文档](https://doc.traefik.io/traefik/reference/install-configuration/tls/certificate-resolvers/acme/) |

项目过滤使用 Traefik 官方支持的 [Docker provider constraints](https://doc.traefik.io/traefik/reference/install-configuration/providers/docker/)。

本机没有用于实测的公网域名和服务器，合成域名不能证明 DNS、网络开放、证书签发、续期或云端客户端信任。实际申请留给大古在授权的公网部署中完成，需真实 DNS、公开可达的 80/443 和通知邮箱；不要把本机 HTTP 18781 当成 ACME 的公网 80。

## 本地回归与清理

`deploy/test_https_config.py` 在修复前失败（四组项目隔离断言及 HTTP-01 断言），修复后 `uv run pytest deploy/test_https_config.py -x -q`：2 passed、4 subtests passed。只解析配置，不创建容器或申请证书，覆盖两种 HTTPS 模式、两个项目名、项目过滤、路由和服务绑定、流式转发、无后端端口、邮箱、挑战入口与项目证书卷。

后端 ruff、mypy、ty，前端 biome、TypeScript 类型检查及 production build 均通过。没有改动页面，浏览器使用专用的 agent-browser 会话完成上述人工流程；没有本地重复全量 pytest 或 Playwright。

`bash scripts/preflight.sh` 通过：同步检查、唯一迁移 head `c696001bc001`、pre-commit 和独立端口环境均通过；脚本没有选择受影响的后端 pytest 文件，新增的部署配置测试已单独执行。环境中的 `DATABASE_URL`、`POSTGRES_PORT` 均显式使用 15781，不连接已清理的数据库。

验证结束后已先删除 `nere781-review` 的隔离验证容器，再执行主项目 `down -v --remove-orphans`、按本项目标签删除 backend / pi 镜像、`docker image prune -f` 和 `docker builder prune -f --reserved-space 5gb`。主项目 `docker compose -p nere781 ps --all` 只剩表头；两个项目的容器查询均为空，nere781 数据卷和镜像查询为空，临时克隆已删除，浏览器专用会话已关闭。没有删除其他任务的资源或基础镜像。

清理后 `df -h ~`：

```text
Filesystem      Size    Used   Avail Capacity iused ifree %iused  Mounted on
/dev/disk3s5   228Gi   180Gi    19Gi    91%    5.8M  202M    3%   /System/Volumes/Data
```

磁盘由同时运行的任务共享，该数值仅为清理完成时快照。以上原始安装输出可能含随机演示密码，只保留本记录中的脱敏结果，不提交日志、`.env` 或备份。

## Reviewer 补交：80 端口约束与缺项证据

2026-11-18 补测，网页运行基线为 master `978378dcfa24d33d971992551f6daccd451736cb`。在新的临时克隆 `../nere781-https-supplement/` 中只启动 `nere781` 的 db、pi、prestart、backend、proxy，使用自签 HTTPS 和全新合成演示数据；没有重跑安装器、备份、升级或真实证书申请。仍先核对 443、18781、15781 空闲，并在启动后确认数据库映射 `127.0.0.1:15781`。浏览器使用独立会话 `nere781-supplement`。

### 安装器端口失败路径

`install.sh` 在域名证书新安装时要求 HTTP 80；80 被占用即停止，不再提示换 8080。已有 `.env` 重跑通过 Compose 解析当前 `COMPOSE_FILE` 与 `HTTP_PORT`，同样拒绝非 80 或其他服务占用；如果是本项目已运行的 proxy 发布了 80，则允许重跑。HTTP 和自签模式的端口规则不变。这里的本机占用检查不代替公网 DNS、80/443 防火墙和证书签发验证。

`deploy/test_https_config.py` 使用假的 Docker、curl 与端口探测命令运行失败路径，**不启动容器、不申请证书**。新增安装测试修复前失败，修复后检查以下 11 组场景；原两模式 × 两项目配置检查继续保留。

| 路径 | HTTP 端口与占用 | 结果 |
| --- | --- | --- |
| 新安装 / 已有 `.env` | 域名证书 8080 | 均明确失败，无 build / up；新安装不生成 `.env` |
| 新安装 / 已有 `.env` | 域名证书 80 被其他服务占用 | 均明确失败，不建议换 8080，无 build / up |
| 新安装 / 已有 `.env` | 域名证书 80 空闲 | 均通过端口校验，继续到假的 build / up |
| 已有 `.env` | 域名证书 80 由本项目 proxy 占用 | 通过，允许重跑 |
| 新安装 / 已有 `.env` | HTTP、自签模式自定义 18781 | 四组均通过，保持原行为 |

已有配置的测试同时将输入环境变量改成 `EPP_ACCESS=http`、`EPP_HTTP_PORT=80`，确认安装器检查保存的实际配置，不能借新的输入值绕过校验。`check_health` 使用 `curl -k` 只证明服务可用；本机未验证公网可信证书签发。

### HTTPS 登录与受保护请求

浏览器在 `https://localhost/login` 输入演示账号；仅记录 URL、状态和请求头字段是否存在：

| 实际请求 | 状态 | Authorization |
| --- | --- | --- |
| `POST https://localhost/api/v1/login/access-token` | 200 | 不存在 |
| 登录后 `GET https://localhost/api/v1/users/me` | 200 | 存在 |

同一部署登录响应的脱敏响应头如下。`Set-Cookie` 明确不存在；未保存或展示响应 token，也没有以 `document.cookie` 为空作为 HttpOnly Cookie 的证据。

```text
URL: https://localhost/api/v1/login/access-token
Status: 200
Content-Type: application/json
Content-Length: 261
Server: uvicorn
Set-Cookie: 不存在
```

### 退出后实际请求

从已登录页面点击「退出登录」，浏览器进入 `https://localhost/login`，显示退出提示；`localStorage.access_token` 不存在。随后在同一浏览器发送一次不带凭据的受保护请求探针，再由网页表单重新登录，实际捕获如下：

| 顺序 | 请求 | 状态 | Authorization |
| --- | --- | --- | --- |
| 1，退出后浏览器探针 | `GET https://localhost/api/v1/users/me` | 401 | 不存在 |
| 2，网页重新登录 | `POST https://localhost/api/v1/login/access-token` | 200 | 不存在，没有携带旧 token |
| 3，重新登录完成后 | `GET https://localhost/api/v1/users/me` | 200 | 存在，已进入新的登录状态 |

浏览器观察器只保留请求方法、URL、Authorization 是否存在和响应状态，不保留请求头值、登录请求体或响应体。这里只验证现有 Bearer 链路，不新增 Cookie 认证要求。

### 有效收件箱通知链接实际打开

点击主导航的收件箱，打开 `https://localhost/inbox`，对应 `/api/v1/orgs/{org_id}/me/inbox` 返回 200。点击已有合成通知「初始画像待过目」中的「查看画像请求」，实际解析并打开：

```text
https://localhost/employees/01a1ebb2-8426-7704-9fa7-b144a38783ed
```

页面显示本人 E02 的「待你过目：系统起草的初始画像」。同一员工的 HTTPS `.../employees/{employee_id}/profile` 与 `.../employees/{employee_id}/change-requests?status=all` 均返回 200；这是实际有效通知的目标，没有用不存在的方案 ID 充当打开成功。没有创建额外方案、发送真实 IM 或增加邮件通道。

### 补交验证与清理

补交验证：`uv run pytest deploy/test_https_config.py -x -q` 为 **3 passed、15 subtests passed**；`backend/tests/test_install_config.py` 为 **8 passed**。后端安装测试初次因未设置开发造数环境变量而在夹具初始化时报错，补齐 `FASTAPI_ENV=development`、`DEMO_USER_PASSWORD` 等本机测试配置后通过，没有修改测试或降低检查。后端 ruff / mypy / ty、前端 biome / TypeScript / build、`shellcheck -x install.sh` 均通过。

补交的 `bash scripts/preflight.sh` 也通过，仍使用独立端口 15781；脚本没有自动选出后端 pytest 目标，上述相关测试已显式执行。

新增部署测试仍需显式运行，尚未进入 CI；按 Reviewer 的非阻塞建议，本次不修改 CI 共享文件，也不另建 issue，留阶段核对取舍。没有将 CI 的其他检查成功等同于已自动执行该文件。

补测后已执行 `docker compose -p nere781 down -v --remove-orphans`、按项目标签删除镜像、悬空镜像清理和 `docker builder prune -f --reserved-space 5gb`。主项目 ps 为空，容器、卷和镜像标签查询均为空；临时克隆已删除，浏览器会话已关闭。未操作 nere497 或其他任务资源。

补测清理后 `df -h ~`：`/dev/disk3s5 228Gi 181Gi 18Gi 91%`，为新的共享磁盘快照；不替换上次清理时的 19 GiB 记录。未重测完整安装、备份、升级、真实模型多段流、外部消息投递或公网证书签发。
