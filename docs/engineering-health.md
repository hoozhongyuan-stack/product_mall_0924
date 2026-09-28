# 工程健康治理与验证记录

基线：`38496a5`（2026-09-28，主分支 E4.1）。本轮按顺序推进：前四步本地实现与验证完成；E5 合入评审发现一项待修 P2，目标环境上线验收尚未执行。各阶段的源码、自动化、浏览器、本机运维与外部环境证据分别记录，不以本机结果替代平台或真机验收。

## 实施顺序与边界

1. 先增加能复现问题的回归，再修复优惠券路由更新保护、CSV 转义及装修目标加载错误。
2. 提取小型 HTTP 基础模块，保留业务权限、错误码、请求体上限与现有 v1 响应数据；明确分页兼容规则。
3. 提取商品媒体编辑及持久操作控制流程，领域请求校验、权限和结果核验仍由业务域负责。
4. 建立组件、浏览器与分层覆盖率检查，以及可复现的 CI；开发测试工具不进入生产依赖。
5. 独立审查 E5 分支。真实环境使用用户提供的测试环境；地址与接入条件未就绪前只执行隔离本机验证，不开放真实支付、退款或通知通道。

## 项目盘点与治理判断

基线是单商户商城的三端单仓：Django 5.2.17/Python 3.12、PostgreSQL 18.6；PC 为 Vue 3.5.43、Vue Router 5.3.1、TypeScript 5.9.3、Vite 8.3.0、Element Plus 2.14.6；小程序使用原生 JS/WXML/WXSS。Python 依赖锁定 cryptography 50.0.1、psycopg 3.3.6、segno 1.6.6；PC 使用 pnpm 锁文件。E5 的 Gunicorn/容器运行能力属于另一个待评审分支，不计为本轮新增生产依赖。

| 工程位置 | 目的 |
| --- | --- |
| `backend/config` | 配置、路由装配与启动入口 |
| `backend/accounts/customers` | 后台账号权限与小程序会员身份，两个独立安全边界 |
| `backend/catalog/pages` | 商品、SKU、素材与装修发布/历史 |
| `backend/inventory/shipping/checkout/orders` | 库存、运费、报价与订单事务 |
| `backend/payments/fulfillment/aftersales` | 资金事实、履约、售后和退款协调 |
| `backend/benefits/points_exchange` | 优惠券、积分权益、积分兑换业务 |
| `backend/notifications/code_versions/report_exports` | 通知任务、源码版本、私有异步导出 |
| `backend/common` | 本轮提取的无业务 HTTP 能力 |
| `pc-admin/src/views`、`shared` | 管理页面、域内组件与有限的跨域公共能力 |
| `mini-program/pages`、`lib` | 小程序页面与网络/业务纯函数 |
| `docs`、`scripts` | 契约、切片证据和部署辅助脚本 |
| `.github/workflows` | 本轮增加的持续验证入口 |

健康判断：业务边界、事务与鉴权已有基础，测试资产较扎实，但不能仅凭测试数量判定可上线。本轮修复了已定位的行为分叉，合并 HTTP/CSV/媒体/恢复逻辑并增加持续验证；其余大文件与跨域依赖按后续功能切片收敛。API 的数量主要来自后台与小程序、业务操作和恢复端点，不应直接按 URL 数量删减；本轮治理的是响应与分页口径分叉，保留必要领域 API 和兼容别名。组件确有复制实现，现已把两个商品媒体区与两个操作恢复控制器合并。没有引入新的状态管理、请求库或通用业务框架；沿用 Element Plus、AssetPicker、媒体校验、幂等操作和已有业务规则，避免再造一套基础设施。

## 验证记录

### 第一阶段：行为回归

- RED：Vue 组件/路由测试 13 项失败、1 项通过；新增 CSV 隐藏公式用例失败。失败分别覆盖同组件参数切换缺少保护、HTTP/业务错误静默及重试缺失、前导空白后的公式未转义。
- GREEN：`cd pc-admin && npm test`，94 项 Node 测试、19 项组件/Router/加载器测试通过，随后 Vue 类型检查和 Vite 构建通过。
- 商品与库存共用 `shared/csv.mjs`，库存旧导入路径保留重导出；Home/Micro 共用目标加载器，失败来源清空、成功来源保留、可重试，并拒绝旧请求覆盖及卸载后更新。
- 新增开发依赖固定版本：Vitest 5.0.2、Vue Test Utils 2.5.1、happy-dom 20.14.5。生产依赖不变。
- 独立静态复审未发现阻塞项。当前路由测试是组合函数加真实内存路由，真实优惠券详情输入及浏览器路径在第四阶段补充，不能把挂载测试称为浏览器/真机验收。

外部目标已确认为测试环境，地址及接入方式待提供。

### 第二阶段：公共 HTTP 与兼容契约

- `common.http` 提取稳定 requestId、成功/错误响应、405/Allow、JSON 对象解析及两种分页 helper；六个 JSON 入口仍保留各自体积上限、异常类型与文案。鉴权、缓存和业务校验不进入公共层。
- 新测试在原实现上出现 36 个缺少 `meta` 的分页子案例失败；改动后覆盖 33 个页码 URL、5 个游标 URL，原有 `data` 不变，非分页列表无 `meta`。
- 定向测试 40 项通过；独立复审无 P1/P2；全量 **763 项测试通过**（366 秒），总语句覆盖率 **90%**，`common/http.py` **100%**。`makemigrations --check --dry-run` 无变化。
- 方法不匹配仍返回 405 与 Allow，新增结构化错误 `METHOD_NOT_ALLOWED`，客户端不需同步修改。

### 第三阶段：共享业务组件与恢复控制器

- 商品新建、编辑使用同一 `ProductMediaEditor`；保持各自保存、SKU、权限和修订规则。媒体类型、方形主图、排重和最多 8 张附图在绑定入口再校验，撤权/换目标/卸载后的旧响应不绑定。
- 商品新建真实挂载 RED：撤销上传权限后旧响应仍绑定；修复后通过。媒体共 18 项组件测试。
- 优惠券和积分兑换使用公共持久操作控制器；原领域 pending 验证、存储命名空间、权限、结果匹配与恢复策略保留。JSON 深复制冻结原请求，同幂等键重试不会采纳调用方后来修改的表单。
- 独立复审发现存储清理失败从 catch 再抛错，新增两域回归先 RED，再修复为保留原请求、关闭重试授权并显示存储错误。恢复后必须重新核验服务端结果和本地可写存储才解锁。
- 账号、权限、目标、页面卸载与密码确认均有迟到响应隔离；不改变服务端权限检查。

### 第四阶段：持续验证

持续验证入口为 `.github/workflows/quality.yml`：PR、main/master push 与手动触发，三个只读权限 job。Action 固定到已核对的提交 SHA；数据库为临时 CI 服务，未引用部署秘密。PC 安装使用冻结锁文件。开发依赖新增固定版本 Playwright 1.63.0 与 Vitest coverage-v8 5.0.2；后端复用已有开发依赖 coverage.py 7.16.1。生产依赖未改变。

| 层 | 入口与门槛 | 本机结果 |
| --- | --- | --- |
| 后端全部源码（排除测试、迁移、manage.py） | `coverage run manage.py test --noinput`；总语句至少 80%，公共 HTTP 至少 90% | 763 项，90% / 100% |
| PC 既有纯函数模块 | `npm run test:coverage` 的 Node 部分；lines/branches/functions 各至少 80% | 94 项；99.35% / 93.89% / 99.04% |
| PC 本轮新增公共模块 | Vitest 逐文件 statements/branches/functions/lines 各至少 80%，明确 include 四个文件 | 85 项组件/行为测试；见下表 |
| 小程序当前测试加载的 JS 模块 | `npm run test:coverage`；lines/branches/functions 各至少 80% | 164 项；93.10% / 83.65% / 80.90% |
| PC 编译构建 | `npm test` 中 vue-tsc 与 Vite build | 通过 |
| 依赖审计 | `pnpm audit --audit-level=high` | 当前完整依赖树 0 条已知漏洞 |

| 新公共模块 | statements | branches | functions | lines |
| --- | ---: | ---: | ---: | ---: |
| `shared/csv.mjs` | 100% | 100% | 100% | 100% |
| `shared/persistent-operation.ts` | 99.12% | 88.88% | 100% | 100% |
| `views/pages/targets.ts` | 100% | 100% | 100% | 100% |
| `views/catalog/ProductMediaEditor.vue` | 93.85% | 90.29% | 93.54% | 100% |

范围限制：PC 这四个模块的逐文件门槛不代表全部 Vue 页面已有 80% 覆盖；Node 原生覆盖率只统计测试实际加载的模块，未加载页面不自动成为零分母项。微信开发者工具、真机、平台和生产验收均不由这些覆盖率代替。CI 配置已落地，本机命令已执行；远端 GitHub Actions 尚未触发，不能记为远端 CI 已通过。

配置依据：[GitHub Actions 上下文可用范围](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts)、[PostgreSQL service 指引](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers)、[Vitest coverage](https://vitest.dev/guide/coverage.html)、[Playwright webServer](https://playwright.dev/docs/test-webserver)。

浏览器验证：Chromium 的 1440px 桌面与 390px 窄屏各 5 项，共 **10/10 通过**。真实 Vue 页面验证优惠券详情浏览器后退时的拒绝/确认、首页与微页面目标失败后重试保留编辑、媒体上传失败/重试/移除、实际 CSV 下载内容；这些用例明确 mock HTTP 以控制故障。另有 **2/2 真实 Django/PostgreSQL 集成**：实际登录、创建草稿、刷新验证持久化，结束时查询数据库确认恰好两条草稿、零订单。启动器只允许 localhost，随机新建自有数据库和媒体目录，剥离平台凭据，正常执行后删除该库并停止自有进程。

浏览器截图已人工检查优惠券桌面与窄屏、首页目标重试窄屏、媒体上传恢复窄屏：布局可读、表单状态保留、恢复操作可达。机械扫描 `impeccable detect` 对五个修改的 Vue 文件返回 `[]`；不代替视觉或无障碍验收。截图与报告在忽略的 `pc-admin/test-results/browser/`、`test-results/integration/`、`playwright-report/browser/`，CI 会归档。真实集成关闭 HTML/trace，仅保留显式截图与 list 输出，避免一次性密码进入报告。独立复审发现并修复报告密码留存和退出清理两个 P2；最终复验通过。

### 复现命令

```sh
# 使用项目现有 PostgreSQL 测试配置；不要把 POSTGRES_TEST_DB 指向业务库
cd backend
python -m pip install -r requirements-dev.txt
python manage.py makemigrations --check --dry-run
python -m coverage run manage.py test --noinput
python -m coverage report
python -m coverage report --include='common/http.py' --fail-under=90

cd ../pc-admin
pnpm install --frozen-lockfile
npm test
npm run test:coverage
pnpm exec playwright install chromium
npm run test:browser
npm run test:integration

cd ../mini-program
npm test
npm run test:coverage
```

集成启动器优先使用环境中的 localhost `POSTGRES_HOST/PORT/USER/PASSWORD`，仅为创建随机测试库连接 `postgres` 控制库；不使用 `POSTGRES_DB` 作为业务库。本地 sibling `product_mall_0924` 的虚拟环境/secret 仅作兼容回退，CI 不依赖该目录。缺少凭据或后台依赖会明确失败。测试账号密码运行时随机生成，不放入命令行。

### 切片评审七项

1. 模块：HTTP 基础归 common、媒体归 catalog、恢复运输逻辑归 shared；契约仅新增分页 meta 和结构化 405，保留旧 data。
2. 数据约束：本轮无模型或迁移变更；既有业务数据库约束继续执行。
3. 并发与失败：原幂等键/冻结请求体保留，账号/目标/权限变化使旧响应失效，未知结果先恢复再重试，存储故障禁止盲发。
4. 规模：沿用现有服务端分页上限与查询；元数据只映射已有分页结果，不新增数据库查询。装修微页面仍仅列前 50 项并明确提示可输入其他 ID。
5. 证据：RED/GREEN、全量后端、组件、纯函数、mock Chromium 与真实隔离数据库各自记录；没有把它们称为微信或目标环境验收。
6. 扩展：新的 HTTP 基础放 common；新领域提供自己的持久操作策略；新商品表单复用受控媒体组件，业务校验仍在所属域。
7. 既有能力：复用 Element Plus、AssetPicker、validateMediaFile、领域幂等/权限校验及现有布局；参考原样板商品管理/编辑（34/35），本轮不重绘页面和小程序。

退出清理补充实测：复用隔离启动器创建新库、迁移、启动两个本机服务，在执行 Playwright 前向本次启动器发送 SIGTERM；退出 130，独立核对本次数据库不存在、两个自有 PID 不存在、两个端口关闭。故障注入首个进程停止超时后，第二个进程仍清理、数据库仍 DROP，最终明确报错。

### 第五阶段：E5 独立合入评审与上线验收

评审对象：`codex/e5-docker-ops@de661977bc3821153358c0c047e73f3301ffb7b2`，基于 main `38496a5`，4 个提交、30 个文件、1564 行增加/9 行删除。E5 工作树干净；本轮未修改 E5、未合并或推送。与工程治理工作树的唯一同改路径是 `docs/engineering-rules.md`，三方 `git merge-file` 返回 0；其余路径无交集。这只证明文本合并可行，不是合并后运行验证。

**合入结论：暂缓，先修复以下 P2。**

- `compose.production.yaml:27` 固定运行用户 `10001:10001`，184/186 行以宿主机文件提供 secrets；`docs/e5-operations.md:8` 要求密钥仅部署账号可读，但缺少 Linux UID/ACL 映射或初始化方案。
- 在 Linux 中，部署账号 UID 1000 或 root 创建的 `0600` 密钥不会被 UID 10001 读取；`scripts/e5-app-entrypoint.sh:6-7` 因 `test -r` 失败终止，release/Web/worker 不能启动。
- 已用无网络、无真实秘密的临时容器验证：Linux UID 1000/0600 合成文件对 UID 10001 不可读；Mac Docker Desktop 的 bind 行为可能掩盖此问题。[Docker Compose 官方文档](https://docs.docker.com/reference/compose-file/services/#secrets)说明 file secret 使用 bind mount，uid/gid/mode 映射不可依赖。
- 修复要求：确定安全的服务 UID/ACL 或专门的密钥初始化机制，保持密钥最小读权限，并在停写/迁移前增加非 root 可读预检和 Linux 回归。不能用全局可读权限绕过。

本次执行的 E5 检查：

| 检查 | 当前证据 |
| --- | --- |
| 备份/恢复脚本 fixture | 11 项通过；数据库命令均为临时替身，未连接业务库 |
| 维护调度单元测试 | 7 项通过，数据库 setup 明确跳过 |
| 7 个 E5 shell 的语法检查 | 通过 |
| 本机已有 recovery 栈 | admin/Web/DB healthy，scheduler/export-worker running，5 个容器 restart=0 |
| `127.0.0.1:18085` | `/healthz` 与管理台 200；通知 availability 返回 `PLATFORM_NOT_VERIFIED`，通道关闭 |
| 运行版本 | `4d57e604…`，与分支 HEAD 差异只有演练记录文档 |
| 调度近 5 轮 | 正常有界摘要；只是该日志窗口的状态 |

未发现备份配对、隔离恢复目标、摘要/路径守卫、迁移后故障处置与 common.http 的其他确定 P1/P2 冲突。备份在写者停止时生成数据库与完整媒体配对包，恢复使用新 project/新卷/新库，文档没有把回旧镜像当作数据库回滚。

| 目标环境验收项 | 状态 | 需要形成的证据 |
| --- | --- | --- |
| Linux 密钥与启动 | 待修复/复验 | 服务 UID 可读、其他用户不可读、部署前预检通过 |
| 数据库与媒体成对备份恢复 | 待目标接入 | 代表性业务记录、素材摘要、代码包、私有下载权限核对 |
| RPO/RTO、停写耗时 | 待目标演练 | 真实数据量、故障时间、一致点、数据验证完成时间 |
| 定时备份与异机副本 | 待验收 | cron 实际安装/运行、失败处理、异机拷贝可读 |
| 告警 | 待接入/送达验证 | 健康、空间、备份超时、worker/调度、发布闸门故障触发与恢复通知 |
| 真实微信主体与平台 | 待接入 | 真实账号和平台回执；相关通道按验收结果逐项放行 |
| 真机 | 待接入 | 目标手机的登录、选购、媒体、权限/异常交互记录 |
| GitHub Actions | 待远端执行 | 实际 run 链接与完整 jobs 结果 |

scheduler/export-worker 当前没有服务级 healthcheck，Compose 也不会主动发送告警；这些运行与告警证据仍须补齐。旧 E5 文档中的恢复点年龄 76 秒/RTO 35 秒属于此前小数据量本机演练，本次没有重新执行恢复，不能作为目标环境指标。用户已确认有测试环境，地址、接入方式、真实主体和真机条件尚待补充。
