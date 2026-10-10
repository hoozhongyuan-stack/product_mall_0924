# 门店模块实施与接口约定

本扩展依据已确认的门店 HTML 原型及用户修订。平台统一装修和商品价格，门店仅管理本店是否销售、可售库存和交付。后台一级导航为门店，二级菜单横向排列「门店管理」「门店账户」「门店设置」；订单与售后仍属于统一订单模块。

## 业务边界

- 门店独立关联现有仓库，复用基础单位库存池、预留、付款扣减及退款归还。填写可售数量保留未支付订单占用，调整留审计记录。
- 门店上架不覆盖平台下架；未分配商品默认不销售。员工权限由会员与门店授权关系校验，商品、订单、账户分别授权。
- 顾客选店与员工操作门店相互独立。购物车按门店隔离，切店不迁移已有商品。订单永久保存门店及交付方式快照；历史无门店订单继续原流程。
- 首版门店实体订单单店单交付方式：PICKUP 到店自提、DELIVERY 配送到家、EXPRESS 快递发货；不混入权益核销商品。自提无运费；配送需配置费用和配送半径并验证收货地址坐标；快递沿用平台运费政策。
- 自提与配送事实纳入售后、退货及订单完成统计。交付不重复扣库存；已交付商品不能按未发货退款自动回补。
- 高德凭据支持后台加密配置。收益按商品规格配置进货价与平台分成比例；门店结算包含进货成本，扣除订单配置运费，运费由门店承担。订单完成且严格超过售后期才结算，门店申请提现、平台审核后线下发放。账户以实际入账、冻结和线下付款记录计算余额，禁止用销售额伪造可提现收入。

## API

沿用现有响应信封、认证、CSRF 和修订冲突协议。新增 admin `/stores` 创建列表、`/stores/{id}` 编辑详情、`/stores/{id}/staff` 人员授权、`/stores/accounts` 账户列表、`/stores/{id}/account` 详情。门店字段：id/name/contactName/contactPhone/address/city/latitude/longitude/openingHours/enabled/acceptingOrders/supportedModes/deliveryRadiusMeters/deliveryFeeFen/revision。

app `/stores` 与 `/stores/{id}` 用于选店。既有商品列表与详情增加可选 storeId；明确传入门店时不退回默认仓。报价增加 storeId/deliveryMode，服务端校验后保存门店政策快照；下单仍仅提交 quoteId/paymentMethod，服务端重新检查门店政策、销售状态、库存和费用。统一订单列表支持 storeId，列表详情包含 storeId/storeName/deliveryMode。

app `/store-center/stores` 返回授权门店。`/store-center/stores/{id}/products` 与商品 PATCH 处理销售状态及库存：revision/onSale/stock[{skuId,availableBaseUnits,expectedAvailableBaseUnits}]。订单操作需权限、订单修订及防重复请求标识；自提凭证与权益核销凭证独立。

## 验收

验证两店库存隔离、库存池组合不超卖、填写不释放预留、下架仅影响本店、跨店访问拒绝、政策变化需重新报价、自提幂等、已交付售后不自动回补、历史订单回归，以及账户未配置时无法提现。分别记录自动化、浏览器和真实小程序验收证据；本地测试不等于部署或真实支付验收。


## 基础门店阶段验收记录（2026-10-10，资金口径确认前）

开发分支 `codex/store-module`，基于实际应用 checkout 开发；本次未提交、推送或部署。

- 后端 PostgreSQL 全量回归 1073 项通过；最新门店、报价及履约专项 47 项通过，新增领域服务与接口语句覆盖率 95%。新增单测验证门店超过 200 个时先按距离排序再限制响应数量。
- PC `npm test`：100 个 Node 测试、366 个组件测试、TypeScript 检查和生产构建通过；覆盖报告语句 96.51%、分支 92.41%、函数 97.37%、行 98.63%。
- 小程序 `npm test`：246 项通过；整体行覆盖 96.29%、分支 86.20%、函数 85.85%。证据来自 wx/API 页面流程测试与静态配置检查。
- 真实 Django/PostgreSQL + Vue 浏览器集成 16 项通过，包含桌面和 390px 窄屏创建门店、保存、刷新持久性及账户未配置状态。另有两项门店界面模拟接口浏览器测试。
- Django 系统检查、迁移一致性检查和 `git diff --check` 均通过。

高德代理成功、缺配置、异常结构及超时均通过模拟上游测试；没有真实密钥联调结果。原生微信布局、定位授权、真实支付和真实资金结算未验收。账户页面及关闭状态已实现；真实收益计算、提现和打款仍需确认结算规则及配置渠道，不能据此报告资金功能已经上线。

真实后台浏览器截图保存在 `pc-admin/test-results/integration/integration-real-isolated--40c25-diness-persist-after-reload-{desktop,mobile}/`，包含 `real-store-persisted.png` 与 `real-store-account.png`。测试服务器和专属集成数据库已由运行脚本清理。

## 高德配置与资金规则补充（2026-10-10）

高德配置接口为 `GET/PUT /api/v1/admin/stores/map-settings`。读取返回修订、来源、可解密状态、加密就绪状态，以及三项凭据的配置状态和末四位；不返回原文。写入精确接受 `expectedRevision/webServiceKey/jsApiKey/jsSecurityCode`，正常情况下空字符串保留旧值，Web 服务 Key 必须存在，JS API Key 和安全密钥须成对配置。托管密文损坏时不使用环境参数回退，管理员必须提供新 Web 服务 Key 以替换损坏配置。

写入需要 `stores.manage`、CSRF、当前账号密码确认，确认操作为 `stores.map.configure`、对象 `amap`、当前修订。凭据复用现有持久化加密密钥文件，按独立用途加密保存；审计只记录修订。迁移 `stores.0002_storemapconfiguration` 创建空配置，不包含真实参数。Web 服务 Key 已接入门店地址搜索；另两项支持存储，尚未接入可视化 JS 地图选点。本地测试使用合成参数，没有录入用户截图凭据或完成真实高德联调。

用户后续确认比例为平台份额，门店结算包含进货成本，扣除订单配置的运费。平台利润＝（订单实付－进货成本）×平台比例；门店结算＝订单实付－平台利润－订单运费。例如实付100元、成本60元、比例20%、运费10元，平台利润8元、门店结算82元。订单实付包含顾客支付的运费；多规格订单按商品行优惠券／积分折抵后的实付比例分摊运费，采用最大余数法、相同余数按规格编号排序，再逐项计算平台利润向下取整，余分留给门店。进货成本按销售规格单位乘购买数量，不按库存基础单位再次换算。

售后期限配置接口为 `GET/PUT /api/v1/admin/stores/settlement-settings`，字段 `receivedWindowDays/revision/appliesTo`，写入接受 `receivedWindowDays/expectedRevision`。天数范围 1–365，复用平台现有 `AfterSalePolicy`，不新建互相冲突的结算等待期；配置对平台新订单生效，既有订单继续使用下单保存的期限快照。需要 `stores.manage`、CSRF、密码确认 `stores.settlement.configure`（对象 `aftersale-policy`、当前修订）；返回 `appliesTo=NEW_ORDERS`。售后期配置完成不会自动开放分润或提现。

本次配置增量专项 28 项后端测试通过，新增配置服务和接口合计语句覆盖率 98%；PC `npm test` 包含 100 项 Node、376 项 Vue 用例、类型检查和构建，全部通过。地图与售后期保存、重读的模拟 API 浏览器用例 4 项通过，包含桌面与窄屏。代码及安全审查发现的损坏凭据恢复问题已修复。本次未执行运行库迁移、真实密钥联调、提交、推送或部署。

配置文件稳定后补跑门店、管理员权限及售后回归 117 项全部通过，日志 `/tmp/store-map-root-tests.log`；迁移模型一致性与补丁空白检查通过。

## 分润、结算和提现实现（资金口径确认后）

后台「门店设置」新增商品规格规则列表，`GET /api/v1/admin/stores/profit-rules` 支持 `search/page/pageSize`，返回规格、当前销售单位、成本（分）、平台比例（万分比）和修订。`PUT /profit-rules/{skuId}` 接受 `expectedRevision/purchaseCostFen/platformShareBps`，需要 `stores.manage`、CSRF、密码确认 `stores.profit.configure`，对象为 SKU ID。规则与销售单位版本绑定，单位版本改变后返回 `requiresReconfiguration=true`，提示重新填写成本，不套用旧单位规则。

下单调用资金域的快照入口，永久保存每项成本、比例及规则修订；当时未配置的规格保留未配置快照，不阻碍原有购买流程，也不使用今天的规则补算历史订单。结算仅处理实体门店订单的可信收款和交付完成事实，零元已付款订单允许按零元事实判断；必须严格超过下单保存的售后期，处理中售后阻止入账。缺少规则、负利润、无法确定退款成本分摊等情况显示 `HELD`，金额未知字段返回 null，不计入可提现余额。

资金模型及写服务归属 `payments`。`settle_store_orders --limit 100` 有界扫描，持久化游标跨过等待和异常订单，已接入 `run_maintenance_tick`；账户 GET 是只读投影，不触发结算。首次结算原子写入收入、钱包和唯一流水，重复执行不重复入账。数据库保护订单财务快照、资金流水、审核事件、已结算收入与提现申请原始字段不可修改或删除。

门店 `POST /api/v1/app/store-center/stores/{storeId}/withdrawals` 精确接受 `requestKey/amountFen/payeeName/bankName/bankAccount`。申请校验当前会员与门店账户权限、可用余额，原子冻结金额；同一 UUID 与正文重试返回原申请，内容变化拒绝。银行卡号加密存储，普通账户接口仅显示末四位。账户详情当前返回最近100条收入与提现记录，客户端明确展示相应记录；金额全部为整数分。

后台 `POST /api/v1/admin/stores/{storeId}/withdrawals/{id}/{review|pay|payee}` 需要独立权限 `stores.accounts.manage`、CSRF、修订及密码确认 `stores.withdrawal.{action}`（对象为提现 ID）。review 接受 `expectedRevision/decision=APPROVE|REJECT/reason`；驳回释放冻结金额，通过仅变为 `APPROVED_PENDING_PAYMENT`。pay 接受 `expectedRevision/paymentReference/reason`，只有已通过申请可登记线下付款，凭证编号必填，登记成功后变为 `PAID`。payee 接受 `expectedRevision`，单独确认后临时读取完整收款账号，记录脱敏审计；页面隐藏、权限变化或离开时清除原文。后台登记是授权人工付款事实，不调用银行转账。

PC 支持规则配置、账户收入与提现明细、审核、线下付款登记及临时查看收款信息。小程序支持账户余额、收入明细、提现申请和状态历史；网络结果不确定时使用原请求重试，避免重复冻结，销毁页面或身份改变时清除私人信息。资金与收款接口禁止缓存。

资金增量验收：后端全量1110项通过（`/tmp/store-finance-root-final.log`）；之后只优化账户列表读取，最终相关财务、门店 API 与维护专项72项通过，选定六个资金模块语句覆盖率98.33%，包含分支的综合覆盖率92%。账户列表只返回余额摘要，批量余额投影固定2次查询，详情保留最近100条记录。PC `npm test`：100项 Node、389项 Vue、类型检查与构建通过；财务组件覆盖报告语句89.69%、分支88.60%、函数91.48%、行100%。小程序 `npm test`255项通过，新财务辅助模块行和函数100%、分支87.88%。最新模拟 API 桌面与390px浏览器用例2项通过（`/tmp/finance-browser-root-final.log`），覆盖规格保存刷新、审核、登记付款及重读状态；这不代表真实银行发放或微信设备验收。本轮代码及安全审查的问题均已修复，没有新增生产依赖或写入真实资金数据。
