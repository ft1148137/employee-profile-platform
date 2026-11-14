# 本地 CSV Connector 示例

按 [扩展开发指南](../../../docs/extending.md#1-数据源) 的数据契约读取合成工作记录。
只演示已有 CSV 格式的适配，不是新增正式数据源：不登记 SourceKind、不加迁移或页面入口。
代码使用 MIT 许可证（见仓库根目录 [LICENSE](../../../LICENSE)），不增加依赖。

## 读取文件

从仓库根目录准备开发环境，然后在 `backend/` 执行：

```bash
cp -n .env.example .env
cd backend
uv sync --frozen
PYTHONPATH=.. uv run python -m examples.connectors.sample_csv.connector
```

输出一条 `SourceRecord` 的 JSON；只读本地文件，不访问数据库、网络或模型。
导入现有服务需要 `.env` 中的应用设置，但这个命令不连接其中的数据库。
`PYTHONPATH=..` 用于找到仓库内、未安装为包的 `examples`。
在 Python 中可用 `SampleCsvConnector(Path("文件.csv")).read()` 读取其他合成 CSV。

`read()` 先用 `preview_work_records` 校验整个文件（最多 5 MB、20000 行），有错误就抛
`InvalidWorkRecords`，不会悄悄导入有效的部分；再用 `csv_record` 转换为 `SourceRecord`。
格式与网页工作记录导入相同：必填 `record_id`、`source`、`occurred_at`、`actor_account`、`title`，
支持的其他列见 `work_records.csv` 和 `backend/app/domains/work/records.py::COLUMNS`。
请显式提供带时区的时间；示例的北京时间 09:00 转为 UTC 01:00。

`source=sample_csv` 变成 `csv:sample_csv`；`record_id` 是稳定任务 key，账号是稳定 event_ref，
文件名和行号都不参与去重。同一记录更正时保留来源、记录编号和账号。
没有评论、工时或附件字段；未知列由已有解析器忽略并给出 warning。
CSV 不携带邮箱，不会按同名员工自动关联，需先由管理员关联外部账号才会派生证据。

## 跑通同步

先按 [开发与测试](../../../docs/development.md#后端测试) 准备**本任务独立** PostgreSQL。
下面是 NERE-623 的完整命令，在仓库根目录执行（其他任务按自己的编号修改项目名、端口和覆盖文件名）：

```bash
set -e
df -g ~
export COMPOSE_PROJECT_NAME=nere623 POSTGRES_PORT=15623 API_PORT=18623
# 任一命令发现端口被占用就停止，不使用别人的数据库。
if lsof -nP -iTCP:15623 -sTCP:LISTEN; then exit 1; fi
if docker ps --format '{{.Names}} {{.Ports}}' | grep ':15623->'; then exit 1; fi
cat > ../nere623.ports.yml <<'EOF'
services:
  db:
    ports: !override
      - "15623:5432"
EOF
export DATABASE_URL="postgresql://postgres:$(sed -n 's/^POSTGRES_PASSWORD=//p' .env)@localhost:15623/app"
docker compose -p nere623 -f compose.yml -f compose.dev.yml -f ../nere623.ports.yml up -d --wait db
docker compose -p nere623 -f compose.yml -f compose.dev.yml -f ../nere623.ports.yml port db 5432
# 必须显示 15623；不需要启动后端或模型服务。
cd backend
uv run pytest tests/connectors/test_m5_sample_connector.py -x -q
```

测试在自动创建的隔离测试库中自行造两个合成企业、员工和一个已关联账号，不改演示企业。
测试调用者持有事务和企业行锁，向 `ingest_record` 传入明确的 `org_id`、记录及来源元数据，
验证 Task / WorkEvent / Evidence，最后回滚。
企业行锁只用于这个离线示例；正式同步仍走现有任务锁、批处理和重试服务。

检查内容包括：首次同步、未关联账号不猜员工、其他企业同账号不串数据、重复同步不新增证据、
修改 CSV 后追加更正证据并保留原文与审计、再次同步更正文件仍幂等、非法文件拒绝。
证据状态按现有服务标为 superseded，原文、标题和 hash 不覆盖，不直接写画像或审批数据。
验证结束（含失败）后，在仓库根目录清理本项目资源；不删其他任务的容器或卷：

```bash
docker compose -p nere623 -f compose.yml -f compose.dev.yml -f ../nere623.ports.yml down -v --remove-orphans
docker image ls -q --filter label=com.docker.compose.project=nere623 | xargs -r docker rmi -f
docker image prune -f
docker builder prune -f --reserved-space 5gb
rm ../nere623.ports.yml
df -h ~
```

本机剩余空间不足时，先按任务的磁盘规则处理，不构建镜像、不重启 Docker。
