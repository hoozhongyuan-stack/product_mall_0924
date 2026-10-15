# 部署导览与最短操作路径

本文面向首次接手项目的开发和部署人员，说明如何从源码走到本地运行、单机 Docker 部署及验收。部署、备份、恢复的运维事实源是 [E5 运维手册](e5-operations.md)；密钥安装细则、资源限制、恢复保护和演练要求以该手册及实际脚本为准。工程约定见 [研发工程与页面规则](engineering-rules.md)，业务和 API 见 [API 契约](contract-v1.md)。

本文是操作说明，编写文档不表示执行过其中命令、发布新版本或完成真实平台验收。既有履约规则及 2026-10-13 UAT 发布证据见 [履约治理记录](fulfillment-governance.md)，不要用早期实施文档开头的历史状态推断当前部署版本。

## 1. 先选择运行方式

| 目的 | 入口 | 实际启动内容 |
| --- | --- | --- |
| 本地开发 | 根目录 `compose.yaml` + 手工启动后端、PC | 根 Compose **只启动 PostgreSQL**，不会启动商城应用 |
| 单机正式部署 | `sh scripts/e5-deploy.sh initial` / `upgrade` | `compose.production.yaml`：DB、媒体初始化、发布闸门、Web、导出 worker、scheduler、admin |
| 受限资源 UAT | 同一部署脚本，私有环境文件设置 `E5_DEPLOY_PROFILE=uat` | 正式编排 + `compose.uat.yaml` + `compose.uat-edge.yaml` |
| 日常配对备份 | `sh scripts/e5-backup-cycle.sh` | 暂停写者、备份 DB 和完整媒体卷、恢复服务 |
| 隔离恢复 | `sh scripts/e5-recover.sh BUNDLE_NAME` | 新 project、新 DB、新卷、独立本机端口；只启动 Web/admin 供验证 |

生产编排面向单机单实例。正式入口还需要部署方提供 HTTPS 反向代理、域名、证书、访问日志和告警；仓库不自动配置这些外部资源。Docker 主机需有 Git、Python 3、Docker Engine 和 Compose 插件。按 E5 已验证方式使用 Linux、root 部署账号、rootful Docker，未验证的 UID 映射不能通过放宽密钥权限绕过。

UAT 文件使用 `!override`，要求 **Docker Compose >= 2.24.4**。先检查 `docker compose version`，不能只确认 Docker Engine 版本。UAT 的限额不代表性能承诺，代码上传可选容器还会增加内存和临时卷预算。

## 2. 本地开发

以下命令从仓库根目录执行，只适用于开发环境。准备 Python 3.12、符合 [PC package.json](../pc-admin/package.json) `engines` 的 Node，以及 `pnpm@11.15.1`；后端媒体处理还需本机 `ffmpeg`。不要把 Django `runserver` 或 Vite 暴露为正式服务。

首次复制 [.env.example](../.env.example) 到被忽略的 `.env`，按实际开发库填写。已有 `.env` 或密码文件时保留原文件，跳过相应安装步骤。为本机 DB 准备独立密码文件；下面来源路径必须换成受控的开发密码文件。

```sh
set -eu
test ! -e .env
cp .env.example .env
install -d -m 0700 secrets
test ! -e secrets/postgres_password
install -m 0600 /secure-source/dev_postgres_password secrets/postgres_password
docker compose up --wait -d db
python3.12 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt
```

逐条确认命令成功；尤其不要忽略 `test` 失败后继续覆盖密码。`requirements-dev.txt` 已包含冻结的 `requirements.lock`。根 Compose 默认使用 `product_mall_dev` 数据库和用户、`127.0.0.1:15432`；若修改 `.env`，下面 Django 参数必须同步。

**Django 不自动加载根 `.env`。** 在启动后端的终端中显式设置实际环境变量；密码从私密文件读取，不回显：

```sh
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=15432
export POSTGRES_DB=product_mall_dev
export POSTGRES_USER=product_mall_dev
export POSTGRES_PASSWORD="$(cat secrets/postgres_password)"
.venv/bin/python backend/manage.py migrate --noinput
.venv/bin/python backend/manage.py create_owner
.venv/bin/python backend/manage.py runserver 127.0.0.1:8000
```

`create_owner` 交互输入登录名、显示名和密码，只允许建立唯一主账号；已有账号时跳过该步，不能用它重置已有账号。开发环境如需保存微信或地图托管凭据，还需显式设置 `MALL_WECHAT_CREDENTIAL_KEY_FILE` 指向独立、持久的开发 Fernet key 文件。不要共用生产密钥。

另开终端启动 PC：

```sh
cd pc-admin
pnpm install --frozen-lockfile
npm test
pnpm dev
```

默认 PC 地址为 `http://127.0.0.1:5173/`，Vite 将 `/api` 代理到 `http://127.0.0.1:8000`；隔离开发端口可通过 `MALL_API_PROXY_TARGET` 调整代理目标。小程序用微信开发者工具导入 `mini-program/`；当前源码 [app.js](../mini-program/app.js) 的 API 地址为本机模拟器地址，[project.config.json](../mini-program/project.config.json) 使用游客 AppID。真机及发布包必须使用一致的目标 AppID 与已验收 HTTPS 地址，不能沿用游客或本机配置。

本地测试命令应在独立测试数据库执行，不能指向生产库：

```sh
.venv/bin/python backend/manage.py test
```

小程序测试从 `mini-program/` 执行 `npm test`；PC 的 `npm test` 包含 Node、组件、类型检查及构建。以上是执行入口，不是本轮通过记录。

## 3. 固定发布来源与私有配置

部署不直接使用带改动的开发目录。准备**独立完整 Git 检出**：`.git` 必须是目录，HEAD 必须等于经审核的完整 40 位小写 SHA，tracked 和 untracked 文件均须干净。普通 linked worktree 的 `.git` 文件不满足 E5 发布入口要求。

下面示例检出本功能所在的 `codex/dining-alliance` 分支，不能直接以默认 main 代替；SHA 仍需替换为经审核的完整提交。在部署主机执行。`RELEASE_SHA` 是本例 shell 变量，真正传给脚本的是 `MALL_RELEASE_REVISION`。

```sh
export RELEASE_SHA=REPLACE_WITH_REVIEWED_FULL_40_CHARACTER_COMMIT_SHA
git clone --no-checkout --branch codex/dining-alliance https://github.com/hoozhongyuan-stack/product_mall_0924.git /srv/product-mall-release
cd /srv/product-mall-release
git checkout --detach "$RELEASE_SHA"
git rev-parse HEAD
git status --porcelain --untracked-files=all
```

最后一条必须无输出。不能把示例 SHA 原样执行，也不要通过删除未知文件、重置开发工作树来“做干净”。升级应准备新的独立检出，不能在运行检出里直接 `git pull`：当前备份任务、镜像来源及回退依据都绑定确切版本。

将 [deploy/env.production.example](../deploy/env.production.example) 复制到检出目录外，例如 `/private/product-mall/release.env`。该文件须 root 所有、0600；不要 `source` 它。Compose 用 `--env-file` 插值，E5 只另外读取几个受限字面字段。按实际环境填写：

| 参数 | 用途和约束 |
| --- | --- |
| `E5_DEPLOY_PROFILE` | `production` 或 `uat`；单行、不加引号，不能重复；缺省为 production |
| `MALL_COMPOSE_PROJECT` | 此实例稳定且独占的 Compose project 名，升级保持不变 |
| `MALL_RELEASE_REVISION` | 与部署检出 HEAD 相同的完整 SHA；shell 导出的值必须相同 |
| `MALL_ALLOWED_HOSTS` | 允许的主机名，如 `mall.example.com`；多个值逗号分隔 |
| `MALL_CSRF_TRUSTED_ORIGINS` | 含协议的来源，如 `https://mall.example.com` |
| `MALL_HTTP_PORT` | admin 的本机端口，默认 18080；始终绑定 `127.0.0.1` |
| `POSTGRES_DB` / `POSTGRES_USER` | 此实例独立的 DB 和用户；升级保持与已有卷一致 |
| `E5_POSTGRES_PASSWORD_FILE` / `E5_DJANGO_SECRET_KEY_FILE` / `E5_WECHAT_CREDENTIAL_KEY_FILE` | 三个部署密钥的绝对路径，均在检出外；微信加密 key 同时用于托管地图等加密配置 |
| `E5_BACKUP_HOST_DIR` | 检出外、真实无符号链接的私有备份目录，0700 或更严；shell 导出值与文件一致 |
| `PAGE_RUNTIME_SCHEMA_VERSION` | 保持 1，只有目标小程序 renderer 验证后才提升 |
| `MALL_MINIPROGRAM_API_BASE_URL` | 可选，目标公开 HTTPS **源站**，如 `https://uat.example.com`，不能含路径 |
| `WECHAT_MINI_APP_ID` | 可选公开 AppID；AppSecret 经受保护后台配置，不写此环境文件 |

三个密钥必须独立保管，微信 key 为独立 Fernet key，不是 AppSecret 或 Django key。按 [E5 密钥安装与预检](e5-operations.md#密钥首次安装与预检) 安装：宿主机密钥目录 root 所有、0700，文件 UID/GID 10001、0400、普通文件且无符号链接；部署环境文件本身仍是 root/0600。备份目录、密钥文件和环境文件不放 Git，不作为构建参数，不公开为静态资源。

## 4. 首次正式部署或 UAT

先准备好上述私有文件、目录及 HTTPS 方案，再在**干净发布检出**中运行：

```sh
export E5_ENV_FILE=/private/product-mall/release.env
export MALL_RELEASE_REVISION="$(git rev-parse HEAD)"
export E5_BACKUP_HOST_DIR=/private/product-mall/backups
sh scripts/e5-deploy.sh initial
```

`initial` 要求目标 project 没有运行或停止的容器，且 `${project}_pgdata`、`${project}_media` 卷从未创建。失败残留须先查明，不能反复用 `initial` 绕过保护，也不能删除业务卷重试。

脚本顺序为：渲染配置 → 构建固定版本镜像 → 宿主机与实际应用 UID 密钥预检 → 启动 DB/初始化媒体 → release 闸门 → 启动应用。release 通过 [deploy-with-code-sync.sh](../scripts/deploy-with-code-sync.sh) 按顺序执行：

1. 核对 Git 根、HEAD 和干净状态。
2. `migrate --noinput`。
3. `check_wechat_credentials`，只读检查托管微信凭据能否用原 key 解密，不调用微信。
4. `sync_deployed_miniprogram --expected-revision "$MALL_RELEASE_REVISION"`，从受信任 Git 对象生成不可变源码包。

首次建立唯一主账号使用交互命令，不接受密码命令行参数。先按下一节定义 `compose` 函数，再执行 `compose exec web sh scripts/e5-app-entrypoint.sh python backend/manage.py create_owner`。`exec` 创建的新进程需要该入口重新从文件加载 DB/Django 密钥，不能直接省略入口运行 Django。公网入口建立前，本机 HTTP 可用于 `/healthz` 排查；正式配置的会话及 CSRF Cookie 为 Secure，后台登录应通过 HTTPS。

UAT 另在同一个私有环境文件中设置 `E5_DEPLOY_PROFILE=uat`、`MALL_EDGE_NETWORK`、唯一的 `MALL_EDGE_ALIAS`，并使用 UAT 域名和 CSRF 来源。专用外部 edge 网络需运维预先创建，只连接 Caddy 与本商城 admin；Caddy 反代 `${MALL_EDGE_ALIAS}:8080`，HTTP 只跳转 HTTPS。该 overlay 不会替你配置 Caddy。仅在核对这一入口边界后，才使用其中固定覆盖协议头的配置；不要将它用于任意明文公网代理。资源和 HTTPS 边界详见 [E5 UAT 章节](e5-operations.md#受限资源-uat-与-https-入口)。

桌码目标默认 `DINING_CODE_ENV_VERSION=release`、`DINING_CODE_CHECK_PATH=1`。UAT 如使用体验版，明确在私有环境文件设置 `DINING_CODE_ENV_VERSION=trial`、`DINING_CODE_CHECK_PATH=0`，并验证实际扫码目标；这些值不会自动把微信开发版本设成体验版。

### 复用正确的 Compose 组合

正式、UAT、可选上传 profile 的文件组合不同。后续诊断从发布检出根目录，在**独立 shell** 中加载脚本自带函数；环境变量沿用上面的实际值：

```sh
root="$PWD"
. ./scripts/e5-compose.sh
compose config --quiet
compose ps -a
```

函数读取 `E5_ENV_FILE`、选择相应 `-f` 和 profile。不要在 UAT 中只执行根 `docker compose` 或漏掉资源/edge 覆盖。`compose config --quiet` 不打印解析后的配置；完整配置可能包含外部配置参数，排障时不要公开粘贴。

### 可选：可信构建机预构建

目标机空间或构建网络受限时，可在相同完整、干净检出上预构建，再传输镜像。目标是 x86 Linux 时，即使构建机是 Apple Silicon 也明确使用 `--platform linux/amd64`：

```sh
docker build --platform linux/amd64 --build-arg MALL_BUILD_REVISION="$MALL_RELEASE_REVISION" -f deploy/Dockerfile.backend -t "product-mall-backend:$MALL_RELEASE_REVISION" .
docker build --platform linux/amd64 --build-arg MALL_BUILD_REVISION="$MALL_RELEASE_REVISION" -f deploy/Dockerfile.admin -t "product-mall-admin:$MALL_RELEASE_REVISION" .
docker build --platform linux/amd64 --build-arg MALL_BUILD_REVISION="$MALL_RELEASE_REVISION" -f deploy/Dockerfile.ops -t "product-mall-ops:$MALL_RELEASE_REVISION" .
```

通过受控渠道使用 `docker save` / `docker load` 传输并校验镜像包，目标主机仍需相同的干净 Git 检出。部署或恢复前在 shell 导出 `E5_IMAGE_MODE=prebuilt`、`E5_IMAGE_PLATFORM=linux/amd64`。脚本校验三张镜像的标签、`org.opencontainers.image.revision` 和 OS/架构，任一不符即在停写前退出；不会自动 pull 或重新打标签。默认 `E5_IMAGE_MODE=build` 在目标机构建。可选上传还需同 SHA 的 `product-mall-mini-ci` 镜像，细节见 E5 手册；镜像、传输包和旧版本均需计入磁盘预算。

## 5. 升级、备份与恢复

### 升级已有实例

在新独立干净检出准备目标 SHA，更新私有环境文件中的 `MALL_RELEASE_REVISION`，保持 project、DB、持久密钥与备份路径对应同一实例。重新导出第 4 节三个变量，运行：

```sh
sh scripts/e5-deploy.sh upgrade
```

`upgrade` 要求已有运行的 Web 和可识别的旧修订。新镜像准备及密钥预检先执行，失败时旧服务继续运行；通过后停止 admin、Web、导出 worker、scheduler 及已启用的两个上传服务，清理允许再生成的旧缩略图缓存，确认无其它 DB 客户端，生成旧版本的**配对备份**，成功后才执行新版本 release 和重启。

备份失败时写者保持停止，禁止跳过备份直接迁移。release 失败时迁移可能已提交；不能直接切旧镜像、运行反向 `migrate` 或 `git pull` 来“回滚”。先核查日志和备份，按下述方式在隔离项目恢复原代码与配对数据，验证后另行决定流量切换。

### 日常备份

从与当前运行修订一致的干净检出、使用相同 `E5_ENV_FILE` 与 `E5_BACKUP_HOST_DIR` 运行：

```sh
sh scripts/e5-backup-cycle.sh
```

脚本与升级共用 `.e5-operation-lock`，暂停全部写者，完成备份后恢复；备份失败也会尝试恢复服务并返回非零。应先测量停写时长，再安装 [cron 模板](../deploy/e5-backup.cron.example)，升级后更新其中的当前检出路径并配置失败告警；模板本身不启用任务。保留并核对 `database.dump`、完整 `media.tar` 和 `manifest.txt`，数据库与媒体必须配对；媒体包含素材、代码包、导出及临时/删除状态，不能只备份图片目录。

备份复制到异机私有存储并定期演练。配对包**不含密钥**，原数据库密码、Django key、微信加密 key 必须经独立受控渠道备份；已有密文无法解密时不能生成新 key“修复”。脚本不自动清理历史备份或 Docker 空间。RPO/RTO 根据实际一致点、异机复制和恢复验证耗时测量，不能用 cron 间隔推断达标。

### 隔离恢复

选取实际备份目录名，读取 manifest 中的完整 `release_revision`，准备该提交的独立干净检出和原部署密钥；默认目标代码必须与清单完全一致。使用源环境的私有配置定位备份，恢复脚本自行覆盖目标 project、DB、端口：

```sh
export E5_ENV_FILE=/private/product-mall/release.env
export E5_BACKUP_HOST_DIR=/private/product-mall/backups
export MALL_RELEASE_REVISION="$(git rev-parse HEAD)"
export E5_RECOVERY_PROJECT=product-mall-recovery-1
export E5_RECOVERY_DB=product_mall_recovery_1
export E5_RECOVERY_HTTP_PORT=18081
sh scripts/e5-recover.sh e5-YYYY-MM-DD-BUNDLEID
```

备份名为 `e5-日期-字母数字ID`，不是完整路径；将示例换成真实目录名。恢复 project、数据库、端口不得与源实例冲突，project 容器和目标两卷必须此前不存在。入口校验大小、哈希、数据库归档及媒体路径/类型后，只向新库和空媒体卷恢复；随后仍执行迁移/凭据/源码同步闸门，启动 Web/admin。

恢复保留 UAT 资源覆盖，但去掉在线 edge，额外使用 [compose.recovery.yaml](../compose.recovery.yaml) 撤销 Web 平台出口；scheduler、导出和上传服务保持停止。它不切换公网流量，不验证微信；人工验证也可能产生审计/读配额记录。检查代表订单、审计、配置、素材摘要、代码包和私有下载权限，保留实测时间。失败目标留存排查，不能对在线源 project 直接运行 `restore` 服务。只有单独评估兼容性后才用 `E5_ALLOW_FORWARD_MIGRATION=1` 恢复到后继提交；这属于前向迁移，不是原版本回退。

## 6. 微信、地图及外部能力

容器健康、源码包生成、凭据可解密、外网可达和真实业务成功是不同验收结果。默认闸门不会证明外部平台已通：

| 能力 | 部署后仍需完成的配置与验证 |
| --- | --- |
| 小程序登录 | 后台「小程序 → 小程序接入」保存目标 AppID/AppSecret；核对微信侧域名/IP 等账号配置、凭据校验、真实 `wx.login`、两账号隔离及真机结果 |
| 小程序包 | `MALL_MINIPROGRAM_API_BASE_URL` 与后台目标 AppID 生成环境专用不可变包；微信侧配置实际请求/上传/下载域名及相关权限，真机保持合法域名和证书校验 |
| 代码上传 | 可选 `E5_CODE_RELEASE_ENABLED=1`、`E5_MINI_CI_DISPATCH_TOKEN_FILE`；后台保存用途正确的代码上传私钥；微信公众平台配置真实出口 IP 白名单；上传开发版与设置体验版分别验证 |
| 自动提审/发布 | 微信第三方平台组件凭据、验证票据、目标小程序代码管理授权；与独立 `CI_DIRECT` 上传区分，只有适用的 `DIRECT_COMMIT` 链路可继续提审发布 |
| 高德地址与地图 | 后台「前置仓 → 前置仓设置 / 地图配置」配置 Web 服务 Key；可视地图还需成对的 JS API Key/安全密钥及高德侧适用域名/IP 配置，实际验证地址搜索、地图加载/选点和保存重读 |
| 支付、退款、物流、积分兑换 | 分别完成适用配置、真实平台/业务验收及独立启用决策，不能以部署成功开放 |

上传 dispatch token 是独立的 32—128 字符 URL-safe 随机值，文件同样由受控目录保存、UID/GID 10001、0400；不是微信私钥或 AppSecret。可选 `WECHAT_CODE_UPLOAD_EGRESS_IP` 只供后台显示运维核对过的出口 IP，不能证明白名单已通过。上传容器隔离、离线编译检查和依赖风险见 [mini-ci-worker README](../mini-ci-worker/README.md)；该 profile 默认关闭，离线编译通过不能代替真实微信上传及生产安全决策。

第三方平台授权回调为 `/api/v1/wechat/open-platform/authorization-callback`，消息票据回调为 `/api/v1/wechat/open-platform/events`，使用目标环境 HTTPS 源站。`UNKNOWN`、`UPLOAD_UNKNOWN`、`RELEASE_UNKNOWN` 先人工核查微信结果，不能自动重试或把本地关闭任务视为平台成功。

高德托管配置优先，尚未托管时兼容 `AMAP_WEB_SERVICE_KEY`、`AMAP_JS_API_KEY`、`AMAP_JS_SECURITY_CODE` 环境变量；Compose 已传入这三项，生产示例文件未列出，通常直接经后台保存。三项参数共享持久微信加密 key 的存储保护；托管密文损坏不能静默退回环境值。Web 服务 Key 与安全密钥不得下发小程序；PC 的公开 JS API Key 与同源安全代理按现有实现使用。保存成功不代表高德网络、配额或在线 SDK 已验收。接口及地图边界见 [契约](contract-v1.md)。

`ORDER_PAYMENT_METHODS_ENABLED` 中两种方式均关闭是 settings 的默认回退值。实际支付方式由 `payments.availability.enabled_payment_methods` 优先读取持久化的 `OfflinePaymentPolicy.wechat_enabled` / `offline_enabled`（可由后台保存），没有该策略时才回退到 settings；不能据默认值判断当前 UAT 是否允许线下下单。生产 Compose 固定 `EXCHANGE_ORDER_ENABLED=0`、`WECHAT_REFUND_ENABLED=0`、`KDNIAO_ENABLED=0`。向环境文件添加同名 `=1` 不会覆盖 Compose 的固定值，支付凭据配置也未由默认编排完整传入，不能把 `.env.example` 当成完整生产通道配置。

后台现行操作以实时会话、权限、CSRF、修订及幂等控制为准，已取消操作员二次密码；早期微信/门店/E5记录中的“密码确认”是历史措辞，以 [现行工程规则](engineering-rules.md#2026-10-11-后台业务免操作员密码约定) 和 API 契约为准。

## 7. 验收与故障定位

完成部署后，沿用第 4 节的 `compose` 函数执行只读诊断：

```sh
compose ps -a
compose logs --tail=200 preflight release web admin export-worker scheduler
curl --fail --silent --show-error http://127.0.0.1:18080/healthz
docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$(compose ps -q web)"
compose exec -T web sh scripts/e5-app-entrypoint.sh python backend/manage.py migrate --plan
```

本机端口以实际 `MALL_HTTP_PORT` 为准；检查所有实际启用应用镜像的 revision，而不只看 Web 标签。`migrate --plan` 只查看待迁移计划。需要登录或保存的业务验收在确认授权、数据范围和账号后执行，保存后刷新/重开确认持久化。

| 层次 | 应记录的证据 |
| --- | --- |
| 来源与部署 | 完整 SHA、干净检出、镜像修订/架构、环境 profile、release 退出结果和备份清单 |
| 运行 | 应用健康、迁移计划、HTTPS/证书、域名与 CSRF、scheduler/导出错误、卷空间及其它共机应用正常 |
| 业务 | 后台登录、适用角色权限、关键列表与保存读回、媒体私有下载、代表订单/库存/审计与升级前后计数核对 |
| 外部平台与设备 | 微信/高德的真实结果、上传/提审/发布分别记录、体验版指定、桌码目标及真机验证 |
| 恢复与运营 | 异机备份、原 key 可用、隔离恢复数据核对、实际停写/RPO/RTO、失败告警送达 |

快速定位时先保留日志及当前状态，按失败层处理：

| 现象 | 首先核对 |
| --- | --- |
| `Target checkout is not clean` / SHA 不符 | 是否误用开发目录、linked worktree，是否有未跟踪文件；重新准备正确完整检出 |
| `!override` 解析失败 | Compose 插件版本是否 >= 2.24.4，是否加载了正确文件组合 |
| 密钥不可读/凭据检查失败 | 宿主机目录/文件所有者权限、UID 10001、Fernet 格式、是否恢复了原 key；不要打印密钥或生成新 key 绕过 |
| `initial` 拒绝 project/volume | 是否已有运行/停止容器或历史卷；判断是升级还是失败恢复，不删除未知卷 |
| 备份失败/仍有 DB 客户端 | 停写范围是否包含其它脚本和外部连接、备份目录空间与权限、媒体路径是否安全；升级失败写者仍停时按手册处理 |
| release 失败 | 依次检查迁移、凭据解密、源码同步；迁移可能已提交，先隔离恢复验证 |
| 后台 403/登录不保持 | HTTPS、`MALL_ALLOWED_HOSTS`、CSRF 来源、Secure Cookie、受信任代理头及账号权限 |
| HTTPS 502 / 本机健康正常 | Caddy、专用 edge、唯一别名和上游 8080；不要给 DB/Web 开公网端口排查 |
| 微信/地图不可用 | 后台有效配置与修订、原 key、平台权限/白名单、实际出口与域名、服务日志中的脱敏错误 |
| 任务超时或未知 | 先按原任务身份查询本地和平台事实，避免重试产生重复外部操作 |
| 操作锁存在 | 确认是否有真实部署/备份进程；不能未调查就删锁并并行执行 |

服务重启和 `/healthz` 200 只能说明相应运行层状态。不得通过 `docker compose down -v`、全局 prune、删除业务卷、反向迁移或盲目重发外部任务解决未知结果。详细停写、恢复和告警处理继续查阅 [E5 运维手册](e5-operations.md)。
