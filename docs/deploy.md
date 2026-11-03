# 部署、备份与维护

这份指南给负责服务器的部署者。安装后，企业管理员只需使用网页，见[管理员指南](admin-guide.md)。所有示例地址和数据均为虚构，请替换为自己的服务器地址。

## 安装前准备

- macOS：启动 Docker Desktop；Linux：安装并启动 Docker Engine。两者均需 Docker Compose **2.24.4 或以上**、Git、curl 和 bash（兼容 macOS 的 3.2）。用 `docker compose version` 检查版本。
- 确保有足够磁盘放镜像、数据库和备份。建议至少剩余 20 GiB，低于 15 GiB 不要构建。
- 为服务器准备固定的局域网 IPv4 或域名。管理员和员工的电脑必须能访问这个地址，不能填写他们各自的 `localhost`。
- HTTP 默认用 80 端口，可换成未占用端口；HTTPS 另需空闲的 443。不要停掉其他应用来抢端口。

```bash
git clone https://github.com/ft1148137/employee-profile-platform.git
cd employee-profile-platform
./install.sh
```

仓库尚未公开时，克隆需要 GitHub 仓库访问权限。生产安装不要复制 `.env.example`：让安装器生成随机密钥和数据库密码，不导入演示数据，不创建默认账号。

### 安装器每个问题怎么回答

| 问题 | 填写方法 |
| --- | --- |
| 访问地址（IP 或域名） | 如 `192.168.1.10` 或 `profiles.example.com`；不含 `http://`、端口、路径。直接回车采用检测到的地址，使用前核对 |
| 访问方式 | `1` HTTP（默认）；`2` HTTPS 自签；`3` HTTPS 域名证书，条件见下表 |
| 证书通知邮箱 | 仅方式 3 询问；填能收到证书到期通知的邮箱 |
| HTTP 端口 | 回车用 80，或填写 8080 等未占用端口；发现占用会要求换端口。HTTPS 的 443 不能由此修改 |

| 访问方式 | 前提和表现 |
| --- | --- |
| HTTP | 可信局域网，允许所选端口入站；如 `http://192.168.1.10:8080`。传输未加密 |
| HTTPS 自签 | 允许所选 HTTP 端口和 443 入站；HTTP 重定向到 HTTPS。Traefik 提供自签证书，浏览器会提示证书不受信任，核对地址后再继续 |
| HTTPS + Let's Encrypt | 真实域名、公网 DNS 指向服务器、外网可达 80/443，HTTP 端口选 80，填写通知邮箱。签发依赖公网条件；内网 IP 不能申请 |

无人值守安装可用：

```bash
EPP_ADDRESS=192.168.1.10 EPP_HTTP_PORT=8080 ./install.sh --non-interactive
# 自签方式改为 EPP_ACCESS=https-selfsigned
# 域名证书改为 EPP_ACCESS=https-letsencrypt，并设置 EPP_ACME_EMAIL
```

同一台机器多套部署要使用不同目录、项目名和端口，例如 `COMPOSE_PROJECT_NAME=epp-test EPP_HTTP_PORT=18080`。HTTPS 各套仍会争用 443，不能仅靠项目名隔离。

### 初始化链接与局域网访问

安装完成会打印访问地址和 `…/setup#token=…`。把**完整的初始化链接**单独交给企业管理员。它 72 小时有效、只能用一次，不要发到群里或截图公开；令牌只存哈希，放在 URL 的 `#` 后面避免进入服务器访问日志。

管理员按[网页向导](admin-guide.md)创建企业和管理员账号。链接过期或丢失且企业尚未创建时，再运行 `./install.sh` 会使旧链接失效并打印新链接；企业已创建时只提示登录，不会重建企业。重跑会保留已有 `.env` 和密钥，不会按新输入重新配置。

服务默认监听全部网卡。请在服务器防火墙只对需要访问的网络放行所选 HTTP 端口，HTTPS 另放行 443；不需要开放数据库端口。macOS 在系统设置中允许 Docker 接收入站连接。另一台电脑用服务器的 IP 测试登录页；VPN、访客 Wi-Fi 隔离、路由器或公司防火墙也可能拦截连接。

### 配置文件与登录限流

安装生成的 `.env` 权限为 600，保存密钥和部署配置；不要提交到 Git，也不要覆盖。`COMPOSE_FILE` 使后续命令沿用生产配置；终端中不要导出另一套 `COMPOSE_FILE`、密钥或数据库密码覆盖它。修改数据库密码仅改 `.env` 不会修改现有数据库密码。

同一登录名在 15 分钟内失败 5 次会被限流；密码重置会清除该账号旧失败的影响。同一来源 IP 默认失败 300 次后，该 IP 下所有登录（包括密码正确的）也会被限流。配置项及默认值为 `LOGIN_MAX_FAILURES=5`、`LOGIN_MAX_FAILURES_PER_IP=300`、`LOGIN_LOCK_MINUTES=15`。

**Docker Desktop、NAT 或反向代理可能把不同人的来源合成同一个 IP。** 本机 Docker Desktop 中，localhost 和局域网访问均可能显示为 `192.168.65.1`。多企业模拟测试建议在 `.env` 增加 `LOGIN_MAX_FAILURES_PER_IP=1000`，保持每账号 5 次限制。生产按实际网络与人数决定，不能把提高阈值当成修复密码问题。修改后执行：

```bash
docker compose up -d
```

## 备份

自动备份按 `.env` 的 `TZ=Asia/Shanghai`、`BACKUP_TIME=02:00` 每天运行，`BACKUP_KEEP=14` 保留最近 14 份，**手动备份也占份数**。修改时间或份数后执行 `docker compose up -d`，使 backup 容器重建。

```bash
./backup.sh                 # 立即备份，打印文件完整路径
docker compose logs backup # 查看自动备份错误
```

备份在仓库 `backups/`，如 `app-20261103-020000.dump`。目录权限 700，文件权限 600；使用 `pg_dump -Fc`，成功后才从 `.partial` 改名并清理旧文件。备份包含受限证据原文，当前不加密、不自动复制到异地，部署者应定期安全转存。数据库备份不包含 `.env` 和 HTTPS 证书卷，需另行保管；丢失数据库密码会影响恢复。

## 恢复

恢复会清空当前 public schema 并还原备份：备份之后新建的表和数据都会删除，数据库恢复为备份时的状态。先把需要保留的当前备份复制到安全位置，避免被保留份数策略清理。

```bash
./backup.sh
./restore.sh backups/app-20261103-020000.dump
# 按提示输入：确认恢复
```

脚本先检查备份，再停止应用、任务队列与自动备份。完整解析备份后，在一个事务中清空 public schema 并导入数据库；失败时数据库保持恢复前的样子，成功后重新执行迁移并启动健康检查。恢复失败时应用保持停止，查看 `docker compose logs`，排查备份与数据库后再执行恢复。**不能将新版本数据库备份恢复到旧代码。**

## 升级与回退

升级前确认 Git 工作区干净，保管 `.env` 并记录当前提交 `git rev-parse HEAD`。

如果 `.env` 里手动写过 `LOGIN_MAX_FAILURES_PER_IP=30`，升级后需自行改成 300（或按人数调整），再执行 `docker compose up -d`；没有写过这一项的部署，升级后自动使用 300。安装和升级脚本都会保留已有 `.env`。

```bash
./upgrade.sh                # 当前跟踪分支执行 git pull --ff-only
./upgrade.sh origin/master  # fetch 后切到远端 master，成为 detached HEAD
```

脚本依次获取代码、备份、构建镜像、迁移和重启，最后检查健康状态。指定提交或标签安装后，下次升级也显式传 `origin/master` 或目标标签。没有生成升级前备份时不会执行迁移。

失败会打印原提交和备份路径。回退要使用**升级前的备份**，同时回退代码；先停止新版本，重新构建旧镜像再恢复，避免仍在运行的新镜像迁移数据库：

```bash
docker compose stop backend worker backup
git checkout <升级前的提交>
docker compose build
# 创建旧版本的服务与迁移容器，但先不启动应用
docker compose create backend worker prestart
./restore.sh <升级前的备份文件>
```

恢复会删除升级后新增的表和数据，随后由旧代码重新执行迁移。不要仅切代码后继续使用已升级的数据库，也不要使用新版本备份向旧版本回退。跨版本恢复应先在隔离环境演练。

## 管理员忘记密码与增加企业

普通员工由企业管理员在网页重置。最后一名管理员忘记密码时，由部署者在安装目录执行：

```bash
docker compose exec -T backend python -m app.cli reset-password admin
```

把 `admin` 换为实际登录名（不是邮箱）。命令仅打印这一次随机新密码，立即使旧会话失效并写审计，管理员下次登录必须改密。重置不会启用已停用账号。安全地单独转交新密码，不保留在共享日志里。

默认一套部署服务一家企业。确需增加企业时：

```bash
docker compose exec -T backend python -m app.cli setup-link --new-org
```

用新链接建立独立企业。每家管理员登录名、所有员工工号和已填邮箱在整套部署中都必须唯一，不能给不同企业复用账号；保护规则会拒绝跨企业发放、重置或停用共享账号。不要靠更换工号绕过保护，应先核对人员身份。

## 停止、重新启动与卸载

```bash
docker compose down        # 停止容器和网络，保留数据卷
docker compose up -d --wait # 用原目录、.env 和项目名重新启动
```

日常停止**不要加 `-v`**。卸载前先导出备份、安全保存 `.env` 和证书。确认不再需要在线数据后才执行 `docker compose down -v --remove-orphans`，这会不可逆地删除本项目数据库及证书卷。最后删除安装目录；保留的异地备份仍可恢复。不要用全局 Docker 清理命令删除其他应用的数据。

## 常见问题

| 现象 | 排查 |
| --- | --- |
| Docker 未启动、Compose 版本过低 | 启动 Docker Desktop 或 Docker 服务，更新 Compose 到 2.24.4+ |
| 提示端口占用 | 首次安装换未占用的 HTTP 端口；HTTPS 还需独占 443。已有 `.env` 不会被重跑安装覆盖 |
| 服务器能访问，员工电脑不能 | 检查 IP、端口、主机和公司防火墙、VPN、访客网络隔离；不要让员工访问 localhost |
| 初始化链接无效 | 核对完整链接和有效期；尚未建企业时重跑安装，旧链接随即失效 |
| 页面要求重新登录 | 密码被修改或重置、账号停用后旧会话失效，请重新登录；账号问题联系管理员 |
| 大家同时提示登录过多 | 检查是否共用来源 IP；等待窗口过期，按上文调整 IP 阈值。重置一个账号不能解除整个 IP 的限制 |
| 名单预览过期 | 别人已导入新名单，重新上传预览后再确认 |
| 找不到初始密码文件 | 已下载文件不可再次读取；对待首次登录账号重新生成，已使用账号单独重置密码 |
| 备份或升级失败 | 查看 `docker compose ps`、`docker compose logs prestart backend backup`；不要删除数据库卷，按错误提示恢复 |

## 验证边界

本指南对应 M1。本机使用 macOS 26.4.1 / Docker Desktop，全新克隆测试 HTTP、浏览器向导、改密、备份恢复、升级及自签 HTTPS；具体结果和截图见 [M1 验收记录](m1-acceptance.md)。Linux 安装由 PR 的 Ubuntu `test-production-install` job 验证，不能把本机通过当作 Linux 已通过。Safari 和 Let's Encrypt 公网签发没有实测，也没有验证真实企业网络或生产数据恢复。
