# E5 应用部署、备份与恢复

本编排用于单机、单实例商城。`compose.production.yaml` 管理 PostgreSQL、私有媒体卷、E3.1 发布闸门、Django Web、导出 worker、单实例维护调度和管理台反向代理。公开端口只绑定本机 `127.0.0.1:18080`，部署方需在它前面配置受信任的 HTTPS 入口、访问日志与监控；正式域名、证书、外部平台凭据和公网流量未由此文件设置。支付、退款、物流外部通道及积分兑换保持关闭。数据库与所有后台进程保留 `private.internal` 内网；仅 Web 额外接入独立 `egress` 网络供已授权的微信登录／凭据检测调用，出口网络本身不代表平台验收通过，也不是域名级防火墙。管理台沿用原有 `ingress` 网络，不接入其他应用的网络。

## 首次部署

1. 从受信任 Git 仓库准备**独立的完整检出**，固定到待发布的完整提交 SHA。工作目录须干净，`.git` 必须是目录，不能用带未提交文件的开发工作树。镜像构建和容器内 E3.1 闸门都会核对这一来源。
2. 由 root 部署账号将 `deploy/env.production.example` 复制到检出目录外的私有位置，设置实际域名、源代码 SHA、备份目录和三个密钥文件路径。采用 rootful Docker 且不启用 userns-remap；若平台使用其它 UID 映射，须另行验证，不放宽文件权限。密钥准备方式见下文。环境文件保持 root 所有、0600，不写 AppSecret；`WECHAT_MINI_APP_ID` 可选填写公开 AppID。备份目录必须在检出目录外、没有符号链接，权限设为0700。将 `E5_ENV_FILE` 指向环境文件；Compose 的变量插值由它读取。执行命令的 shell 同时导出 `MALL_RELEASE_REVISION` 和 `E5_BACKUP_HOST_DIR`，值应与文件一致。
3. 在满足以上条件后运行 `sh scripts/e5-deploy.sh initial`。入口先构建固定提交镜像，随后检查宿主机密钥权限和实际容器 UID 10001 的可读性、微信加密 key 格式与生产配置；预检不连接数据库、迁移或请求微信。通过后才启动数据库和初始化媒体卷，运行迁移及受信任小程序源码同步；闸门成功后才启动 Web、worker、调度及管理台。管理台本机地址 `http://127.0.0.1:18080/`，`/healthz` 可作就绪检查。

### 密钥首次安装与预检

Compose 的 file secret 使用宿主机文件绑定，不能依赖 YAML 中 `uid/gid/mode` 重映射。宿主机密钥目录须为 root 所有、0700；每个文件须为普通文件、没有符号链接，UID/GID 均为10001、权限0400。应用仍以非 root UID10001 运行，DB 官方入口和备份工具以 root 读取同一数据库密钥。安装动作只在已核对来源和目标后执行，预检不会更改权限或重建任何密钥。

以下示例假设三个真实密钥已经存放在受控来源；第三个是独立 Fernet key（32字节的 URL-safe base64，可带末尾换行），与 AppSecret、Django key 不同。不要将任何 key 放入 Git、镜像构建参数或命令行字面量。首次目标已存在时示例立即停止，禁止以生成新 key 的方式修复已有托管凭据的解密错误：

```sh
# root shell; replace paths with the reviewed private source and deployment directory.
set -eu
install -d -o root -g root -m 0700 /private/product-mall
test ! -e /private/product-mall/postgres_password
test ! -e /private/product-mall/django_secret_key
test ! -e /private/product-mall/wechat_credential_key
install -o 10001 -g 10001 -m 0400 /secure-source/postgres_password /private/product-mall/postgres_password
install -o 10001 -g 10001 -m 0400 /secure-source/django_secret_key /private/product-mall/django_secret_key
install -o 10001 -g 10001 -m 0400 /secure-source/wechat_credential_key /private/product-mall/wechat_credential_key
```

上述命令在 `set -e` 的独立 shell 中执行，或逐条确认退出码，不能忽略 `test` 的失败。`E5_WECHAT_CREDENTIAL_KEY_FILE` 指向第三个源文件；容器得到 `MALL_WECHAT_CREDENTIAL_KEY_FILE=/run/secrets/wechat_credential_key`，应用直接读取，入口不把其内容转成环境变量。release、Web、worker、scheduler 挂载同一持久 key，导出／会员／支付读路径不能使用不同 key。部署、恢复、定时备份都调用共享只读预检；缺失、空值、格式错误、宽权限、所有者错误或运行 UID 无法读取时失败，且停写尚未发生。新 key 的检查只证明部署材料有效，不证明已有密文可解密；与后台接入设置切片合并后还须用同 key 验证合成密文恢复。

预检服务使用相同镜像、环境、密钥与 UID，但不挂载 `media/pgdata`，不启动依赖，网络模式为 `none`。首次预检失败不会创建业务卷，避免污染 `initial` 对新卷的保护条件；不自动删除任何既有卷。

本地回归入口如下；均不连接业务数据库。第一项使用 Docker Compose 客户端只读渲染配置和命令替身核对停写顺序；Linux 项只在临时容器内创建合成文件，不读取真实密钥：

```sh
python3 scripts/e5-preflight-test.py
python3 scripts/e5-backup-restore-test.py
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,nosuid,size=16777216 --user 0:0 \
  --mount type=bind,source="$PWD/scripts",target=/tests,readonly \
  --entrypoint python \
  python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c \
  /tests/e5-secret-linux-test.py
```

本轮合成回归证据：7项顺序／配置／错误边界、6项Linux密钥校验、原有11项备份恢复测试通过；新预检模块行与分支合并覆盖率96%。目标Linux密钥挂载、真实TLS/微信出网及同key业务密文恢复仍须在合并部署时独立验证。

示例命令只说明变量和顺序；按实际私有路径替换，不把示例密钥当成生产凭据：

```sh
export E5_ENV_FILE=/private/product-mall/release.env
export MALL_RELEASE_REVISION="$(git rev-parse HEAD)"
export E5_BACKUP_HOST_DIR=/private/product-mall/backups
sh scripts/e5-deploy.sh initial
```

数据库不映射宿主机端口。`pgdata` 与 `media` 是独立命名卷；后者包括素材、代码包、导出、临时对象和延迟删除状态。容器和镜像重建不会主动删除卷。备份仍须覆盖**数据库加整个私有媒体卷**，单独保存数据库不能保证引用完整。

## 升级与配对备份

将待发布提交准备为新的干净独立检出，更新私有环境文件中的完整 SHA 后运行 `sh scripts/e5-deploy.sh upgrade`。入口先构建新镜像并完成上述密钥预检，预检失败时旧服务保持运行。随后读取旧运行镜像来源 SHA，停止 admin、Web、导出 worker 与调度，确认数据库无其他客户端，生成 PostgreSQL custom dump、完整私有媒体归档和 SHA-256 清单。备份包先写入临时目录，校验与落盘后原子改名。备份失败时写者保持停止；**禁止跳过备份直接运行迁移**。备份完成后才运行 E3.1 迁移／源码同步闸门并重启应用。

`pg_dump` 与媒体归档期间必须保持写者停止。若还有人工脚本、其它主机或数据库客户端，备份脚本会拒绝发布包。正式部署需把外部写入入口一并隔离。每份备份的 `manifest.txt` 记载 `consistent_at_utc`、完成时间、旧代码修订、两个文件大小及 SHA-256。复制备份到异机受控位置，并定期校验可读取性；仅保留本机目录不能覆盖宿主机故障。不要让 Web 或静态服务暴露备份路径。备份留存周期与存储容量需按实际数据增长设定，当前脚本不会自动删除历史备份。

日常备份由宿主机调度 `scripts/e5-backup-cycle.sh`，与升级共用原子操作锁，停止写者、完成配对备份后再恢复服务；失败时也尝试恢复原服务并返回非零状态。脚本从干净的当前检出读取完整提交 SHA，再与运行容器的版本标签核对。`deploy/e5-backup.cron.example` 给出每30分钟一次的安装模板，须由实际部署账号在目标宿主机安装，升级后把模板中的“当前检出”路径切到新版本并接入失败告警。**模板文件本身不会启用定时任务**。每轮停写时长随数据库和媒体体量增长，先在目标环境测量，再决定频率和业务可接受的短暂停机窗口。30分钟间隔本身不足以证明 RPO 小于1小时；还要计入上次一致点、备份耗时、调度失败和异机复制延迟。

闸门中的数据库迁移可能已经提交，后续源码同步才失败；故障时应用仍暂停，不能把“切回旧镜像”视为数据库回滚。先读部署日志和备份清单，在隔离恢复环境验证旧提交与数据，再决定切换流量。新环境若需重试，先查清失败原因；不要对结果不明的迁移或任务盲目重发。

## 隔离恢复与演练

数据库与媒体配对包**不包含部署密钥**。微信托管凭据密文必须使用原加密 key 才能恢复；三项部署密钥须通过独立受控渠道异机备份，记录与部署／备份版本的对应关系。恢复前从该渠道安装原 key 并运行相同权限预检，不能临时生成替代 key。保护密钥副本与数据备份的访问权限，不能把 key 内容追加到备份 manifest、日志或公开页面。密钥丢失不属于数据库恢复成功；不得对此声称 RPO/RTO 已达标。

`e5-recover.sh` 要求新的 Compose project、新的数据库名、不同的本机管理台端口和此前不存在的 `*_pgdata`、`*_media` 卷。它拒绝与源 project 同名，默认要求检出提交与备份清单的 `release_revision` 完全一致；先校验清单及两个备份文件的大小、哈希、PostgreSQL 归档和媒体路径／类型，再恢复到新库及空媒体卷；最后运行 E3.1 闸门，启动恢复栈。**不要**直接对正在运行的源 project 调用 `restore` 服务。失败的目标卷会保留供排查，需由运维确认后清理。恢复脚本不自动切换公网流量。若明确要在恢复后迁移至较新的兼容提交，单独设置 `E5_ALLOW_FORWARD_MIGRATION=1`；入口还会校验备份提交是目标提交的祖先，兼容性仍须单独确认并记录迁移结果，此时不属于原版本回退演练。

```sh
export E5_ENV_FILE=/private/product-mall/release.env
export E5_BACKUP_HOST_DIR=/private/product-mall/backups
export MALL_RELEASE_REVISION="$(git rev-parse HEAD)"
export E5_RECOVERY_PROJECT=product-mall-recovery-1
export E5_RECOVERY_DB=product_mall_recovery_1
export E5_RECOVERY_HTTP_PORT=18081
sh scripts/e5-recover.sh e5-YYYY-MM-DD-BUNDLEID
```

恢复后至少核对代表性的订单／审计／配置记录、素材摘要、代码版本包、导出任务与私有下载权限，并验证 Web `/healthz` 和管理台页面。公网切换前还需确认目标提交与迁移兼容。RPO 以故障时间减去清单中的 `consistent_at_utc` 计算；RTO 以故障发生到恢复栈就绪并完成数据校验的实际时间计算。两者应记录演练实测值及停写时长，不能仅依据备份频率或容器启动时间声称达标。阶段计划中的 RPO 1 小时／RTO 4 小时仍是待验证目标，不是当前承诺。

## 调度、监控与限制

`scheduler` 单实例每轮运行 `run_maintenance_tick --limit 100`，命令持有 PostgreSQL 会话 advisory lock，重叠执行会拒绝，单轮有超时和非零错误状态。它有界处理本地订单超时、确认收货、通知任务安全租约恢复、积分过期、报价／登录／读配额清理及私有媒体清理；不调用未接入的微信、支付或物流。导出由独立 `export-worker` 持续处理，过期导出文件清理由 worker 执行。权益对账需独立的跨轮游标方案，目前不在自动调度中。无效媒体恢复标记会保留并使调度报错，须人工排查。

监控至少覆盖服务存活和 `/healthz`、数据库卷／媒体卷／备份目录空间、备份完成时间与异机副本、调度及导出 worker 错误、E3.1 发布闸门失败。Compose 提供健康检查与重启，但不会自行发送告警；接入值班系统和告警阈值需由实际部署环境完成。生产前还要核对公网 TLS 终止、可信代理头、域名／CSRF、容量、备份加密与访问控制，以及真实平台和真机独立验收。
