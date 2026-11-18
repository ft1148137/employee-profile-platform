# 依赖许可证核验（NERE-780）

## NERE-839 运行依赖增量

OR-Tools `9.15.6755` 从开发组移入后端运行依赖，版本和锁定制品保持一致。其 Python 发行包采用 Apache-2.0，应用通过 Python 接口调用 CP-SAT。核验依据为发行包内的 LICENSE、PyPI 元数据及上游版本标签。[PyPI 发行记录](https://pypi.org/project/ortools/9.15.6755/) · [上游许可证](https://github.com/google/or-tools/blob/v9.15/LICENSE)

| 运行依赖链中的包 | 锁定版本 | Python 发行包许可证 |
| --- | --- | --- |
| absl-py | 2.5.1 | Apache-2.0 |
| immutabledict | 4.3.1 | MIT |
| NumPy | 2.5.5 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0（发行元数据） |
| pandas | 3.1.1 | BSD-3-Clause |
| protobuf | 6.33.6 | BSD-3-Clause |
| python-dateutil | 2.9.0.post0 | Apache-2.0 AND BSD-3-Clause（按包内文本） |
| six | 1.17.0 | MIT |
| typing-extensions（此前已为运行依赖） | 4.15.0 | PSF-2.0 |

Python 包许可与应用的 Apache-2.0 许可可共存。检查 Linux x86-64 CPython 3.14 wheel 时也发现 OR-Tools 随包附带 COIN-OR 原生库，NumPy 的 wheel 许可文件另列带 GCC Runtime Library Exception 的 libgfortran。项目目前提供源码和本机构建配方。分发预构建镜像前，应按实际制品核对这些原生组件的许可、声明和源码提供义务。[OR-Tools 构建依赖](https://github.com/google/or-tools/blob/v9.15/cmake/dependencies/CMakeLists.txt) · [GCC Runtime Library Exception](https://gcc.gnu.org/onlinedocs/libstdc++/manual/license.html)

## NERE-780 核验基线

核验日期：2026-11-18。代码基线：`20fe9adb9f57e1fbdac9afe0a0a4bb47d201ae0c`。覆盖根目录 `uv.lock`、根目录 `bun.lock`（前端 workspace）、`pi/package-lock.json`，以及所有 Dockerfile / Compose 引用的基础镜像。审计工具仅一次性运行，不写入依赖文件。本次没有替换或删除依赖，没有修改仓库设置。

## 结论与发布边界

核验基线的根目录 `LICENSE` 是模板 MIT 文本，版权为 Copyright (c) 2019 Sebastián Ramírez。按本 issue 更新后的范围，**本 PR 已将根 [LICENSE](../LICENSE) 改为 Apache-2.0 官方完整原文，包含 APPENDIX，保留其中占位符不填写**；原 MIT 全文原样保存在 [licenses/full-stack-fastapi-template-MIT.txt](../licenses/full-stack-fastapi-template-MIT.txt)，根 [NOTICE](../NOTICE) 写明项目许可、模板来源及版权和依赖许可入口。未改源码文件头、依赖文件或仓库设置。这是已定项目许可的整理，不是对外公开发布。

逐字验证命令（仓库根目录，两个 diff 都无输出且 exit=0）：

```bash
set -e
curl --fail --silent --show-error --location \
  https://www.apache.org/licenses/LICENSE-2.0.txt --output ../nere780-apache-original.txt
diff -u ../nere780-apache-original.txt LICENSE
git show 20fe9adb:LICENSE | diff - licenses/full-stack-fastapi-template-MIT.txt
shasum -a 256 LICENSE licenses/full-stack-fastapi-template-MIT.txt
```

实测 SHA-256：Apache 原文与根 LICENSE 均为 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`；MIT 备份与 `20fe9adb:LICENSE` 均为 `0bd4e76cdd06ab24cce2de5e74f490920897f90a22ad35e8403ed006d4560f48`。这些逐字比对不替代下述依赖及二进制交付义务。

锁文件共核对 **614 条第三方包记录**：Python 108、前端 417、Pi 89。排除本项目 `app` 和前端 workspace 自身；包括间接、开发、平台条件和可选依赖，同名不同版本分别计数。Python 和 npm 顶层包未发现 GPL、AGPL、EPL、SSPL 或最终无法确定许可的包；这不是对二进制内嵌组件的零风险保证。

非宽松项逐项列在下文：Python 的 3 项 LGPL、2 项 MPL；前端的 24 项 MPL、1 项 CC-BY 数据包；Pi 89 项均为宽松许可。按当前分离使用方式，未发现必须将项目自身改成 GPL/AGPL 的依赖，LGPL/MPL/CC-BY 组件仍受各自许可约束，不能统一改称 Apache-2.0。镜像内操作系统及内嵌库的精确版本未做运行时清点，列为**二进制再分发前待核验项**，不是「镜像整体已通过」。

## 方法与复现

在仓库根目录运行。需要 Python 3.14、uv、Bun 和 Node（Pi 要求 >=22.19.0）。本次 uv 0.9.27、Bun 1.4.4；本机 Node 22.15.1 仅用于安装读取包许可证，npm 提示 engine 不满足，未据此宣称 Pi 运行测试通过。无需 Docker、数据库或模型调用。

```bash
uv sync --all-packages --frozen
bun install --frozen-lockfile --ignore-scripts
(cd pi && npm ci --ignore-scripts --no-audit --no-fund)
uv run --with pip-licenses pip-licenses --from=mixed --format=json \
  --with-license-file --no-license-path > ../nere780-installed-python.json
bunx --bun license-checker --json > ../nere780-installed-web.json
(cd pi && bunx --bun license-checker --json > ../../nere780-installed-pi.json)
```

`pip-licenses` / `license-checker` 只是已安装包的交叉检查，不能覆盖本机没有安装的 Windows/Linux 等可选包，也可能列出本项目或临时审计工具。因此统计以锁文件为全集，按精确包名和版本访问 PyPI / npm 的发行元数据；异常再读已安装发行包中的 LICENSE。下面代码会生成逐包清单，网络错误直接失败，不把漏查当成功：

```bash
bun -e 'import lock from "./bun.lock"; await Bun.write("../nere780-bun-packages.json", JSON.stringify(lock.packages))'
python3 - <<'PY'
import concurrent.futures
import json
import pathlib
import tomllib
import urllib.parse
import urllib.request

root = pathlib.Path('.')
uv = tomllib.loads((root / 'uv.lock').read_text())['package']
bun = json.loads(pathlib.Path('../nere780-bun-packages.json').read_text())
pi = json.loads((root / 'pi/package-lock.json').read_text())['packages']

def fetch(job):
    ecosystem, name, version = job
    url = (f'https://pypi.org/pypi/{name}/{version}/json' if ecosystem == 'python'
           else f'https://registry.npmjs.org/{urllib.parse.quote(name, safe="")}/{version}')
    try:
        with urllib.request.urlopen(url, timeout=45) as response:
            data = json.load(response)
        info = data['info'] if ecosystem == 'python' else data
        return dict(ecosystem=ecosystem, name=name, version=version,
                    license=info.get('license_expression') or info.get('license'),
                    classifiers=info.get('classifiers', []), url=url)
    except Exception as error:
        return dict(ecosystem=ecosystem, name=name, version=version, error=str(error), url=url)

jobs = [('python', p['name'], p['version']) for p in uv if 'registry' in p.get('source', {})]
for value in bun.values():
    if '@workspace:' in value[0]:
        continue
    name, version = value[0].rsplit('@', 1)
    jobs.append(('frontend', name, version))
with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
    result = list(executor.map(fetch, jobs))
for key, value in pi.items():
    if key:
        result.append(dict(ecosystem='pi', name=key.split('node_modules/')[-1],
                           version=value['version'], license=value.get('license', 'UNKNOWN')))
pathlib.Path('../nere780-inventory.json').write_text(json.dumps(result, indent=2))
assert not any('error' in r for r in result), result
print('records', len(result))
PY
```

## 汇总口径

下表按完整许可证表达式分类，每条记录只计一次。`OR` 可选其中一种，`AND` 须同时满足，不能各拆成两个包。宽松许可也需要保留相应版权、许可和免责文字；Apache-2.0 还需保留适用的上游 NOTICE，BSD-3-Clause 不得以作者名义背书。

| 许可证表达式 | Python | 前端 | Pi |
| --- | ---: | ---: | ---: |
| 0BSD | 1 | 2 | 1 |
| Apache-2.0 | 9 | 14 | 35 |
| Apache-2.0 AND MIT | 0 | 11 | 0 |
| Apache-2.0 OR BSD-2-Clause | 1 | 0 | 0 |
| Apache-2.0 OR BSD-3-Clause | 2 | 0 | 0 |
| BSD-2-Clause | 1 | 2 | 0 |
| BSD-2-Clause AND Unlicense AND MIT | 1 | 0 | 0 |
| BSD-3-Clause | 18 | 4 | 11 |
| CC-BY-4.0 | 0 | 1 | 0 |
| ISC | 2 | 14 | 0 |
| LGPL-3.0-only | 3 | 0 | 0 |
| MIT | 63 | 332 | 41 |
| MIT AND PSF-2.0 | 1 | 0 | 0 |
| MIT OR Apache-2.0 | 1 | 11 | 0 |
| MPL-2.0 | 2 | 24 | 0 |
| PSF | 1 | 0 | 0 |
| PSF-2.0 | 1 | 0 | 0 |
| Python-2.0 | 0 | 1 | 0 |
| Unlicense | 1 | 1 | 1 |
| 合计 | 108 | 417 | 89 |

元数据人工补证：`mypy-extensions` 1.1.0 在 PyPI 未给 license/classifier，但安装包 `mypy_extensions-1.1.0.dist-info/licenses/LICENSE` 明确为 MIT，故不是最终「未知」；`colorama` 0.4.6 和 `jinja2` 3.1.6 的 BSD classifier 结合发行许可按 BSD-3-Clause 计；`pathspec` 1.1.1 的 MPL classifier 与包内全文一致；`python-dateutil` 2.9.0.post0 的 Dual License 按 Apache-2.0 OR BSD-3-Clause；`uvloop` 0.22.1 按 MIT OR Apache-2.0；`pywin32` 312 保留元数据的 PSF 名称，不猜测具体 SPDX 版本。`argparse` 2.0.1 是 Python-2.0 宽松许可，不能误报为 GPL。以上可从生成清单中的发行元数据 URL 和相应发行包复核。

## Python 非宽松依赖

| 包 | 锁定版本 | 许可证 | 直接/间接与用途 | Apache-2.0 共存结论与义务 |
| --- | --- | --- | --- | --- |
| psycopg | 3.3.6 | LGPL-3.0-only | 后端直接；SQLAlchemy 的 PostgreSQL 运行时驱动 | 分离导入可共存；保留库的 LGPL/GPL 文本和署名，允许替换与调试，再分发库时提供对应源码路径，见下一节 |
| psycopg-binary | 3.3.6 | LGPL-3.0-only | `psycopg[binary]` 间接；运行时 C 扩展与捆绑客户端库 | 同上；额外核对实际 wheel 内 libpq、OpenSSL 等第三方组件的版本、声明和源码义务，不能仅用 Python 包的 LGPL 标签代替 |
| psycopg-pool | 3.3.3 | LGPL-3.0-only | Procrastinate 间接；后台任务运行时连接池 | 可共存但须满足 LGPL，详见专节 |
| <https://pypi.org/project/certifi/2026.10.19/> | 2026.10.19 | MPL-2.0 | HTTP 客户端间接；运行时 CA 数据 | 独立文件可共存；再分发时保留 MPL 与署名，告知取得覆盖文件源码的方法，修改覆盖文件仍以 MPL 发布 |
| pathspec | 1.1.1 | MPL-2.0 | mypy 间接；开发类型检查的路径匹配 | 同上；不复制进应用；开发用途不免除实际再分发该包时的 MPL 义务 |

MPL 是文件级许可，允许与不同许可的文件组成 Larger Work，见 [Mozilla 官方 FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/)。后端 Dockerfile 的 `uv sync --package app` 没有 `--no-dev`，不能假定开发工具一定不在最终镜像里；发布镜像时应按实际内容核对。

## psycopg-pool（LGPL-3.0-only）专节

项目未修改或内嵌该库。`backend/app/worker/app.py` 创建 Procrastinate 的 `PsycopgConnector`，由 Procrastinate 在运行时导入独立的 `psycopg_pool` 包；依赖链为 `app → procrastinate 3.10.0 → psycopg-pool 3.3.3`。安装器在用户自己的机器构建镜像，`uv sync --frozen --package app` 安装锁定依赖。参见 [ADR-0002](adr/0002-worker-and-compose-layout.md)。

依据[上游携带的 LGPL-3.0 正文](https://raw.githubusercontent.com/psycopg/psycopg/master/psycopg_pool/LICENSE.txt) §4，本项目自身可保持 Apache-2.0，但不能限制用户修改库、为调试库修改而逆向工程。若再分发包含该库的组合物，应显著说明使用该库及其 LGPL 许可，随附 LGPL-3.0 和 GPL-3.0 全文及适用署名；有运行时版权展示时，也须给出库的版权和许可获取位置。

保持库为可替换的独立 Python 包，使兼容修改版可运行；不得通过打包、签名或许可条款封死替换。若不满足 §4(d)(1) 的共享库机制，就按 §4(d)(0) 提供最小对应源码与可重新组合的应用代码；需要时提供安装信息。分发库自身的二进制也要按其适用条款提供对应源码或有效的源码取得方式，不能只说「上游是开源的」。

**当前仓库分发源码和构建配方，不分发预构建镜像、wheel 或 node_modules。** 仅在用户本地安装和构建、内部运行，不等于项目向该用户交付含库二进制；因此当前不需要为一个尚未分发的镜像准备二进制源码交付包。若以后提供镜像、离线安装包或把库复制进仓库，则须重新履行以上分发义务；「未修改」和「动态导入」都不是免除许可证文本或源码义务的理由。

## 前端非宽松依赖

以下全部是间接依赖。`lightningcss` 1.33.0 来自 Vite，1.32.0 来自 `@tailwindcss/node`；平台包是其可选原生绑定，仅构建/开发 CSS 时用，不在浏览器运行时 import。每行独立列版本和许可；不是本机同时安装了全部平台包。

MPL 行均可与 Apache-2.0 应用共存：保留 MPL、署名和取得覆盖源码的方法，修改库的覆盖文件仍按 MPL 发布；如果以后再分发构建工具或它的原生二进制，须提供对应覆盖源码，不能以「构建工具」为由豁免。由编译器产生 CSS 不自动意味着必须将整个应用改成 MPL，仍须检查输出是否包含被许可代码。

| 包 | 版本 | 许可证 | 用途 |
| --- | --- | --- | --- |
| caniuse-lite | 1.0.30001765 | CC-BY-4.0 | 间接；构建数据 |
| lightningcss | 1.33.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-android-arm64 | 1.33.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-darwin-arm64 | 1.33.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-darwin-x64 | 1.33.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-freebsd-x64 | 1.33.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-linux-arm-gnueabihf | 1.33.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-linux-arm64-gnu | 1.33.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-linux-arm64-musl | 1.33.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-linux-x64-gnu | 1.33.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-linux-x64-musl | 1.33.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-win32-arm64-msvc | 1.33.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-win32-x64-msvc | 1.33.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss | 1.32.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-android-arm64 | 1.32.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-darwin-arm64 | 1.32.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-darwin-x64 | 1.32.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-freebsd-x64 | 1.32.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-linux-arm-gnueabihf | 1.32.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-linux-arm64-gnu | 1.32.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-linux-arm64-musl | 1.32.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-linux-x64-gnu | 1.32.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-linux-x64-musl | 1.32.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-win32-arm64-msvc | 1.32.0 | MPL-2.0 | 间接；构建工具 |
| lightningcss-win32-x64-msvc | 1.32.0 | MPL-2.0 | 间接；构建工具 |

`caniuse-lite` 为 browserslist 间接引入的浏览器兼容性数据，仅构建工具使用。CC-BY-4.0 不按软件宽松许可汇总；允许与应用共存，但再分发数据或其改编时须署名、链接许可并标明修改，不能给数据附加禁止其许可权利的限制，依据 [Creative Commons 官方说明](https://creativecommons.org/licenses/by/4.0/)。这不要求把整个应用改成 CC-BY。

## Pi Node 依赖

`pi/package-lock.json` 的 89 项（含间接）全部为上表宽松许可；直接依赖 `@earendil-works/pi-agent-core` 1.5.0（MIT）、`@earendil-works/pi-ai` 1.5.0（MIT）、`undici` 7.30.1（MIT）。`npm ci --ignore-scripts --omit=dev` 在构建阶段安装，复制到 distroless Pi 运行镜像中，由 Node import。`package-lock.json` 中的每一条 license 都已计入；没有 Pi 顶层未知项或非宽松项。

## Dockerfile / Compose 基础镜像

检索命令（包含 COPY --from 的外部工具镜像）：

```bash
rg -n '^FROM|COPY --from=|image:' backend/Dockerfile pi/Dockerfile \
  frontend/Dockerfile.playwright compose*.yml pi/compose.yml deploy/compose*.yml
```

| 镜像引用 | 来源/用途 | 主要软件许可证及结论 |
| --- | --- | --- |
| `oven/bun:1` | backend/Dockerfile；前端构建阶段直接使用 | Bun 主代码 MIT，但静态链接的 JavaScriptCore/WebKit 含 LGPL；不可将 Bun 整体标为纯 MIT。只复制前端产物到最终 Python 镜像，不复制 Bun 可执行文件；若分发构建镜像，需核对 Bun 的重链接材料、源码和声明 |
| `python:3.14` | backend/Dockerfile；后端及复用它的 prestart、worker、IM、mock Jira | Python PSF 系列许可及内嵌库各自许可；Debian 系统层含 GPL/LGPL。主程序许可可共存，整个镜像再分发需单独清点 |
| `ghcr.io/astral-sh/uv:0.9.26` | backend/Dockerfile；COPY uv/uvx 到最终镜像 | uv 为 Apache-2.0 OR MIT；保留所选许可及内嵌第三方声明；不是仅在构建时存在 |
| `node:22-bookworm-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da` | pi/Dockerfile；安装 Node 依赖的构建阶段 | Node MIT，V8、OpenSSL、npm 等各有声明；Debian 层并非统一 MIT。只复制 node_modules 到最终镜像 |
| `gcr.io/distroless/nodejs22-debian12:nonroot@sha256:13593b7570658e8477de39e2f4a1dd25db2f836d68a0ba771251572d23bb4f8e` | pi/Dockerfile；Pi 运行时 | distroless 构建项目 Apache-2.0，Node MIT；Debian 的 glibc 等仍有 LGPL，不能因无 shell 就省略系统库义务 |
| `postgres:18` | compose.yml、deploy/compose.prod.yml；独立数据库服务 | PostgreSQL License（宽松）；系统层及扩展各自许可，不和 Python 应用统一重新授权 |
| `traefik:v3.7` | 两份 deploy/compose.https-*.yml；HTTPS 反向代理 | Traefik MIT；Go 依赖及镜像系统层各自许可，发布二进制时保留其声明 |
| `adminer`（未固定 tag） | compose.dev.yml；可选数据库管理页面 | Adminer Apache-2.0 OR GPL-2.0，可选 Apache 分支；PHP 及系统层需另查。未启动它，也未把它的代码合入应用 |
| `mcr.microsoft.com/playwright:v1.64.1-noble` | frontend/Dockerfile.playwright；仅浏览器测试 | Playwright Apache-2.0；Ubuntu 系统层和 Chromium/Firefox/WebKit 各自许可，包含 GPL/LGPL，不能统称 Apache-2.0；另安装 unzip 和 Bun |

以上为直接镜像引用，其操作系统包和内嵌库均为镜像内的间接组件。[Docker 官方 Python 镜像说明](https://github.com/docker-library/docs/blob/master/python/README.md)明确要求使用者核对镜像中全部软件许可；[distroless 项目](https://github.com/GoogleContainerTools/distroless)的 Apache 声明不替代 Debian 包许可。主要软件依据：[Bun 许可及内嵌库](https://github.com/oven-sh/bun/blob/main/LICENSE.md)、[Node 及第三方声明](https://github.com/nodejs/node/blob/main/LICENSE)、[uv 许可证](https://github.com/astral-sh/uv)、[PostgreSQL](https://www.postgresql.org/about/licence/)、[Traefik](https://github.com/traefik/traefik)、[Adminer](https://www.adminer.org/)、[Playwright Docker 说明](https://playwright.dev/docs/docker)。

**只在镜像里的非宽松/未知项**：Debian/Ubuntu 的 glibc（LGPL）、GNU 工具（GPL）、Bun 的 JavaScriptCore/WebKit（LGPL），以及浏览器内嵌第三方组件；实际包名、版本、子许可必须以将来分发的 digest 为准，本次未拉取镜像生成 SBOM，不能给出已验证的完整组件清单。未固定的 tag 会漂移，即使 Node/distroless 固定 digest，也尚未完成其内部组件逐项核验。独立程序聚合通常不改变应用许可，但分发 GPL/LGPL 二进制须保留许可、相应源码或满足条款的提供方式。该待核验项已上报 Reviewer；不在本 PR 改镜像或依赖。

## NOTICE 与第三方声明判断

Apache-2.0 本身并不强制每个项目新建 NOTICE；若上游带有与分发内容相关的 NOTICE，必须保留其适用声明，依据 [Apache-2.0 §4](https://www.apache.org/licenses/LICENSE-2.0)。本 PR 按调整后的验收要求新增根 NOTICE，用于标明模板来源和许可入口；它不是声称已覆盖所有依赖或二进制声明的完整清单。

仓库已经携带模板衍生代码和 Procrastinate SQL 快照，**即使只分发源码也须保留其 MIT 声明**：本 PR 已原样保存模板 MIT 全文至 `licenses/full-stack-fastapi-template-MIT.txt` 并在根 NOTICE 标明来源和版权；`backend/app/alembic/versions/procrastinate_LICENSE.md` 继续随 SQL 分发，未修改。

若发布包含前端 bundle、Python wheel、Node 依赖、容器或离线包，则发布时按实际制品补第三方声明：包名/版本/来源、版权、许可全文、适用 NOTICE、LGPL/GPL 的对应源码及替换/重链接指引、MPL 覆盖源码取得方法、CC-BY 数据署名与变更说明。浏览器接收前端 bundle 也属于代码交付，不能因不提供 Docker 镜像就省略其中依赖的声明。本次已添加的根 NOTICE 和模板 MIT 全文不替代这些将来制品的声明与源码交付材料。

## 锁文件指纹与限制

| 锁文件 | SHA-256 |
| --- | --- |
| uv.lock | `1d24f23177664fc18f95bde0d2b58a2d8514fa38b086b97a1e82cafb34d1042f` |
| bun.lock | `541a5c2bb91d9298cb35e3ef9961baa7556a2e3f59d281c496e45f0ccb45ff6d` |
| pi/package-lock.json | `c3549948ffb559c20ec102f4a87cab62687c93e0a26b6637bf1277266e4c7df3` |

可用 `shasum -a 256 uv.lock bun.lock pi/package-lock.json` 复核。更新依赖后应重跑，不能把本报告套用于新锁文件。Registry 元数据是发行者声明，可能后续修订；包内 LICENSE 优先，完整清单可由上述代码重新生成。本报告没有使用模型处理员工数据、没有构建镜像，也没有验证尚未制作的二进制发布包。
