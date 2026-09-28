# B2C 商城 V1.0｜数据与 API 契约草案

状态：阶段 A 的分类、状态、编码与规格、素材上限、默认权限和主账号方案已获项目确认，2026-09-24；B0 交易契约及 B1—B4 的库存、报价、订单与权益结算已完成本机实现与验证。C0 已建立独立支付凭证、事件去重、异常历史与统一收款结算入口；该入口只供后续可信渠道适配器调用，线下实际到账核实与微信支付尚未接入。微信和线下两种公开下单方式均关闭。本文是研发契约，不改变《B2C商城小程序V1.0需求文档.md》的业务规则。工程骨架、账号权限、分类/商品/SKU 基础切片、批量分类、商品图片/视频、原生小程序商品浏览、首页草稿/预览/发布、已有草稿商品规格与 SKU 编辑、独立微页面，以及冷启动 GIF 与静态兜底图切片已落地；验证结果见[研发评审记录](phase-a-preparation.md)。E1页面版本列表与回退见5.22；E1.1底部导航和客服悬浮配置见5.23。

## 1. 契约通则

- 服务端：Django 模块化单体，PostgreSQL；PC 后台：Vue 3、TypeScript、Vite、Element Plus、Vue Router；小程序：微信原生。首切片依赖版本已锁定于 `backend/requirements.lock` 与 `pc-admin/pnpm-lock.yaml`。
- API 前缀 `/api/v1`。数据库主键建议用服务端生成的 UUID，API 中作为不透明字符串；商品编号、SKU 编码、订单号分别是业务编号，不能拿名称或规格代替主键。
- 金额字段命名以 `...Fen` 结尾，类型为非负整数分；有方向的流水金额可用有符号整数分。基本库存数量为整数，`...BaseUnits` 表示换算后的基本单位。时间使用带时区的 ISO 8601 字符串，数据库使用 `timestamptz`。
- 状态枚举由服务端定义，新增值要求客户端能显示保底文案。写请求提交的价格和库存只用于变化确认，不作为服务端成交依据。
- 成功响应：`{ "success": true, "data": {}, "requestId": "...", "meta": {} }`；失败响应：`{ "success": false, "error": { "code": "...", "message": "...", "details": [] }, "requestId": "..." }`。`meta` 只在列表等需要分页的接口返回。
- 列表参数 `page` 从 1 开始，`pageSize` 默认 20、上限建议 100；响应 `meta` 包含 `page`、`pageSize`、`total`。排序字段使用服务端白名单。PC 表格勾选只表示当前页，不在服务端保存跨页选择。
- PC 写操作使用服务端会话、CSRF 保护和操作权限校验；建议同源部署。小程序公开读接口不要求登录，用户私有数据接口使用微信登录换取的站内会话并校验资源归属。凭据不在 URL、响应或普通日志中出现。
- 更新商品和页面草稿使用 `expectedRevision` 防止覆盖他人的编辑；版本不匹配返回 `REVISION_CONFLICT` 和最新版本摘要。资金、库存、发布接口还需幂等或状态机保护。

## 2. 阶段 A 数据实体

字段类型按 PostgreSQL 表达。`created_at`、`updated_at`、操作者及审计字段在所有可变业务表中按需配置；不会用软删除掩盖审计历史。

| 表/实体 | 关键字段 | 必要约束与用途 |
|---|---|---|
| `admin_account` | `id uuid`、`login_name text`、`display_name text`、`password_hash text`、`kind OWNER/STAFF`、`enabled bool`、`revision int`、`auth_version int`、`failed_count int`、`locked_until timestamptz`、`last_active_at timestamptz` | `login_name` 唯一；数据库约束全库至多一个 `OWNER`。资料/权限编辑校验 `revision`；账号停用、凭据重置、账号组关系变化及所属组权限变化/停用时，使受影响账号旧会话失效或即时重算权限。主账号不能被子账号管理。登录失败限制、空闲超时依需求文档第 12.1 节。 |
| `permission_group`、`account_group`、`group_permission` | 组 ID/名称/启用状态/`revision int`；账号与组；组与操作权限码 | 账号可入多个组，关联对分别唯一。更新组权限时比较并提升 `revision`，拒绝过期提交；操作权限由服务端集中枚举，默认拒绝未授权操作。 |
| `audit_log` | `id`、`actor_id`、`action_code`、`object_type/id`、`before/after jsonb`、`result`、`request_id`、`occurred_at` | 高影响操作保留前后值与结果；敏感字段脱敏或只记变化类型。业务修改与成功审计在同一事务内写入。 |
| `action_confirmation` | 凭证哈希、操作者、会话键、动作、对象、修订号、到期与核销时间 | 账号和权限组高影响操作使用 5 分钟一次性凭证；核销用数据库行锁保证并发请求无法复用。原始凭证仅在确认响应中返回，不入库。 |
| `category` | `id`、`parent_id`、`name`、`sort_order`、`status ACTIVE/INACTIVE`、`revision` | 最多二级；父级不能指向自身或二级分类。商品只绑定启用的二级分类；一级分类只用于导航。停用有在售商品的分类须先处理关联商品。 |
| `product` | `id`、`product_no`、`name`、`category_id`、`fulfillment_kind SHIP/REDEEM`、`status DRAFT/ON_SALE/OFF_SALE`、`ever_on_sale`、`description_content`、`revision` | 新建先保存草稿，所有 SKU 初始下架。`status` 由 SKU 状态和是否曾上架派生：任一 SKU 在售则 `ON_SALE`；全部下架且曾上架则 `OFF_SALE`；从未上架则 `DRAFT`。`product_no` 全库大小写不重复，1—64 字符；名称最多 120 字符。富文本必须净化。库存决定能否购买，不决定商品是否展示。 |
| `product_spec_axis`、`product_spec_option`、`sku_spec_selection` | 规格项名、排序；规格值、排序；SKU 与选中值 | 每商品最多 2 个规格项、每项最多 20 个值、组合最多 100 个。选中值必须属于该商品的规格项；同商品 SKU 组合由稳定 ID 生成唯一键，不用展示名称。 |
| `sku` | `id`、`product_id`、`sku_code`、`list_price_fen bigint`、`sale_status ON_SALE/OFF_SALE`、`unit_version_id`、`revision` | `sku_code` 在单商户内大小写不重复，1—64 字符；与商品编号、规格名值分列。`list_price_fen >= 0`。已有订单或库存流水的 SKU 编码及规格含义不直接覆盖。 |
| `member_grade` | `id`、`code`、`name`、`rank`、`enabled`、`revision` | 阶段 A 建立等级字典，供 SKU 等级价引用和展示；默认等级按需求文档，自动升降级及运营配置留待阶段 D。`code` 和 `rank` 各自唯一。 |
| `sku_grade_price` | `sku_id`、`grade_id`、`price_fen bigint`、`effective_at` | 同一 SKU、等级和生效版本不能重复。实际成交价由服务端根据会员等级选取，历史订单保留快照。 |
| `sku_unit_version` | `id`、`sku_id`、`base_unit`、`sale_unit`、`ratio_positive_int`、`effective_at` | 比例为正整数；启用新版本不改写历史订单和库存流水。阶段 A 建立版本结构，阶段 B 接入实际记账。 |
| `media_asset`、`product_gallery_image`、`product.main_image_id`、`product.video_id` | 素材 ID、存储键、MIME、字节数、宽高、摘要；商品主图/视频关联与附图排序 | 商品素材支持 PNG/JPEG 图片和 MP4 视频；主图须正方形，最多主图 1 张、附图 8 张、视频 1 条。启动素材另支持至少 2 帧的 GIF，静态兜底图使用 PNG/JPEG。本机临时上传上限：图片/GIF 10 MiB、视频 50 MiB；GIF 正式体积与加载目标经目标设备验证后定稿。存储键不等于公开 URL。 |
| `micro_page`、`page_config_version`、`page_publication` | 页面 `id`、类型 `HOME/MICRO`、草稿名称；版本 `id`、`page_id`、`revision`、发布名称快照、`config_json jsonb`、发布人/时间；每页当前发布版本指针 | 首页是唯一的 `HOME` 页面，可关联已发布微页面。草稿可改；发布时从草稿生成不可变版本，并原子切换指针。校验组件字段、素材、链接目标发布状态和权限；失败保留旧版。首页主题三色属于版本配置。历史版本在阶段 A 保存，页面回退操作在阶段 E 交付。 |
| `startup_config`、`startup_config_version`、`startup_publication`、`startup_publish_request` | 唯一配置草稿及修订、素材 ID；不可变版本与当前指针；操作者和幂等键 | GIF 与兜底图独立引用。草稿允许暂空；预览/发布要求两项素材完整且文件可用。发布在事务中生成版本、切换指针、写幂等记录与审计，失败保留旧版。用户端只读当前发布版本。3 秒倒计时从页面实际呈现后开始，分享/扫码目标由客户端恢复。 |

建议索引：`sku(product_id, sale_status)`、`product(category_id, status)`、`product_spec_selection(sku_id)`、`page_config_version(page_id, revision)`；唯一索引包括 `upper(product_no)`、`upper(sku_code)`、`member_grade(code)`、`(page_id, revision)`、`(product_id, spec_key)`，以及仅对 `kind='OWNER'` 生效的唯一约束。`product_no` 和 `sku_code` 只接受英文字母、数字、`-`、`_`；`spec_key` 由服务端按稳定规格项 ID 排序后连接所选规格值 ID，同商品内唯一，不用展示名称或顺序生成身份。无规格商品使用固定的单 SKU 键。具体索引 SQL 在首批迁移时核对。

公开读取仅把启用分类下、商品与 SKU 均为 `ON_SALE` 且后续具备真实可售库存的组合标为可购买。下架或不可售的旧链接返回明确状态与提示，不允许继续提交购买。

商品接口以默认启用仓的实时可售量显示是否可选购；报价与选购不锁库。订单提交尚未实现，所有商品仍返回 `purchasable: false`，不能用样板数字伪造可售库存或开放提交订单。

## 3. 阶段 A API 字段表

表中列出业务字段；通用响应包装、`requestId` 和审计规则按第 1 节执行。所有写接口服务端重新校验权限、类型、范围和资源状态。

| 方法与路径 | 请求或查询关键字段 | 响应关键字段与约束 |
|---|---|---|
| `POST /api/v1/admin/auth/login` | `loginName`、`password` | `accountId`、`displayName`、`permissionCodes`、会话状态；不返回密码哈希。失败次数与锁定时限在服务端执行。 |
| `GET /api/v1/admin/me`、`POST /api/v1/admin/auth/logout` | 会话 Cookie | 当前账号、权限；退出使会话失效。高影响操作仍重新确认当前会话。 |
| `GET/POST /api/v1/admin/accounts`、`PATCH /api/v1/admin/accounts/{id}` | 登录名、状态、权限组 ID、`expectedRevision` | 账号摘要；主账号和子账号权限边界在服务端执行，停用立即失效。凭据重置单独动作并审计。 |
| `GET/POST /api/v1/admin/permission-groups`、`PATCH /api/v1/admin/permission-groups/{id}` | 组名、`permissionCodes[]`、`expectedRevision` | 权限组及成员数；不能通过自改组提升自己的权限。 |
| `GET/POST /api/v1/admin/categories`、`PATCH /api/v1/admin/categories/{id}` | `parentId`、名称、排序、状态、`expectedRevision` | 两级分类树；管理端读取时返回关联商品数与在售商品数，停用前检查在售商品。 |
| `GET /api/v1/admin/member-grades` | 无 | 返回阶段 A 的等级字典供 SKU 等级价编辑；阶段 D 再提供等级规则管理。 |
| `GET /api/v1/admin/sku-rows` | `keyword`、`categoryId`、`fulfillmentKind`、SKU 状态、`page`、`pageSize` | 一行一个 SKU：`skuId`、`skuCode`、`skuRevision`、`productId`、`productRevision`、`productNo`、`productName`、商品类型、有序 `specs[{name,value}]`、日常价、等级价、状态。`rowKey=skuId`；当前分页信息位于 `data` 的 `page/pageSize/total`。库存尚未接入。 |
| `POST /api/v1/admin/products`、`GET/PATCH /api/v1/admin/products/{id}` | 商品基础字段、规格项和值、SKU 列表、素材 ID、`expectedRevision` | 商品与 SKU 独立 ID/编码；服务端校验规格上限、组合唯一及素材绑定。创建只允许草稿及下架 SKU；PATCH 只修改商品基础字段及媒体替换/移除，拒绝提交独立商品 `status`。规格编辑使用下列独立接口。 |
| `POST /api/v1/admin/products/{id}/specs/preview`、`PUT /api/v1/admin/products/{id}/specs` | 草稿商品的完整 `specAxes`、`skus`、`expectedRevision`；保留的规格项/值须带原 ID，保留的 SKU 须带 `id`、`expectedSkuRevision`；PUT 另带预览返回的 `previewToken` | 两个接口均需 `catalog.write`、`sku.price.write`、`sku.status.write`、`sku.unit.write`。预览返回保留/新增/移除 SKU 和移除项的等级价、单位版本数量；令牌绑定操作者、商品、请求内容和 SKU 修订快照，10 分钟有效。PUT 锁定商品及 SKU，在一个事务中重新校验预览、修订、组合和全局 SKU 编码；匹配组合保留 SKU ID 与未变资料，新增组合建新 SKU，移除组合删除草稿 SKU 与其当前资料并写审计。非草稿拒绝。 |
| `POST /api/v1/admin/sku-rows/batch-status` | 当前页选中 `items[{skuId,expectedRevision}]`、`saleStatus` | 最多 100 个 SKU，每项在事务中锁所属商品再锁 SKU、校验修订和上架前置条件、更新商品派生状态及审计；返回 `results[{id,success,revision,productStatus?,productRevision?,code?,message?}]` 及成功/失败数。逐项失败不影响已成功项；客户端变更筛选或分页时清空选择。 |
| `POST /api/v1/admin/products/batch-category/preview` | 当前页选中 `skuIds[]`、目标 `categoryId` | 服务端按商品去重，返回受影响商品总数、全部 SKU 总数、页外 SKU 数、各商品修订号和不可操作原因，并生成 10 分钟有效的签名 `previewToken`。 |
| `POST /api/v1/admin/products/batch-category` | `items[{productId,expectedRevision}]`、目标 `categoryId`、`previewToken`、`Idempotency-Key` 请求头 | 仅可提交预览中允许操作的商品；预览摘要不匹配或过期则拒绝。逐商品重新校验，返回逐项成功/失败；同一操作者、同一键和同一请求重试返回已记录结果，换内容返回 409。 |
| `PATCH /api/v1/admin/skus/{id}/status`、`PATCH /api/v1/admin/skus/{id}/price`、`PUT /api/v1/admin/skus/{id}/grade-prices`、`PUT /api/v1/admin/skus/{id}/unit` | SKU 状态、日常价、等级价数组或新单位版本、`expectedRevision` | 每项独立验权限与修订号；SKU 首次上架要求商品有主图、启用的二级及一级分类和销售单位。SKU 与商品派生状态、修订号、审计同事务保存。等级价 PUT 仅替换已启用等级的价格，保留已停用等级的原价及旧版本；单位旧版本保留。 |
| `POST /api/v1/admin/assets` | `multipart/form-data` 的 `kind=IMAGE/VIDEO/GIF` 与 `file` | 仅 `asset.upload` 可调用，返回 `assetId`、MIME、大小、宽高及 `adminUrl`；校验文件声明、容器结构和实际解码，GIF 须至少 2 帧。图片/GIF 10 MiB、视频 50 MiB。本机使用已安装的 `ffmpeg`；上传服务缺少解码器时返回 503。文件写入 `MALL_MEDIA_ROOT`，生产环境必须显式配置持久目录。未绑定素材分别受单账号 200 MiB、全站 1 GiB 配额限制，超过 24 小时由 `purge_orphan_media` 清理，上传时也会补充清理。 |
| `GET /api/v1/admin/assets/{id}/file`、`GET /api/v1/app/assets/{id}/file` | 管理端预览、公开素材读取 | 管理端需 `asset.read` 或符合页面/启动草稿、当前或历史发布引用及本人未绑定上传的受限预览条件；公开 URL 在素材被当前上架商品、首页/微页面当前发布版本的可见组件，或启动页当前发布版本引用时可访问；草稿、隐藏组件和旧版独占素材不可公开。MP4 支持单段 `Range` 请求。 |
| `GET/PUT /api/v1/admin/pages/home/draft` | 首页组件、顺序、显隐、站内链接与三项主题颜色；PUT 含 `expectedRevision` | 返回 `pageId`、草稿 `revision`、`config`、`publishedRevision`；保存草稿允许逐步配置未完成组件，不改变线上版本。`page.read` 可读，`page.edit` 可写。 |
| `POST /api/v1/admin/pages/home/preview`、`POST /api/v1/admin/pages/home/publish` | 预览 `{expectedRevision}`；E1起发布 `{expectedRevision,expectedPublicationRevision}`，另需 `Idempotency-Key` 和绑定账号、动作、页面 ID、修订号的一次性 `X-Action-Confirmation` | 预览严格校验可见组件、素材和站内目标；发布在事务内生成不可变版本、切换当前指针并写审计。过期修订返回 `REVISION_CONFLICT`；同键同修订重试返回原版本，同修订换键拒绝再次发布。 |
| `GET/POST /api/v1/admin/pages`、`GET/PUT /api/v1/admin/pages/{id}/draft`、`POST /api/v1/admin/pages/{id}/preview/publish` | 列表 `page/pageSize`；创建 `{name}`；草稿 PUT `{name,expectedRevision,config}`；预览 `{expectedRevision}`；E1起发布 `{expectedRevision,expectedPublicationRevision}`，另带 `Idempotency-Key`、一次性 `X-Action-Confirmation` | 仅列出 `MICRO` 页面，分页上限 100；创建返回 201 与初始草稿。草稿返回 `pageId/name/revision/config/publishedRevision`；预览严格校验可见组件与目标；发布事务生成不可变版本并原子切换指针。`page.read/edit/publish` 分别校验；版本列表与回退见5.22。 |
| `GET/PUT /api/v1/admin/startup/draft` | GET 无；PUT `{expectedRevision,gifAssetId,fallbackAssetId}`，两个素材 ID 可暂空 | 返回 `{revision,gifAssetId,fallbackAssetId,publishedRevision}`。`startup.read` 可读，`startup.edit` 可写；素材类型与文件存在性由服务端校验；过期修订返回 `REVISION_CONFLICT`。 |
| `POST /api/v1/admin/startup/preview`、`POST /api/v1/admin/startup/publish` | 预览 `{expectedRevision}`；E1起发布 `{expectedRevision,expectedPublicationRevision}`，另需 `Idempotency-Key` 与绑定 `startup.publish`、对象 `startup`、当前修订的一次性 `X-Action-Confirmation` | 预览返回修订及两张素材的管理端 URL；发布要求两项有效并返回 `versionId/revision/gifUrl/fallbackUrl`，后两项为公开相对路径。同键同修订重试返回原版本；不同键重复发布当前修订返回 409；版本、指针、请求记录和审计同事务提交，失败旧版继续生效。 |
| `GET /api/v1/app/startup` | 无 | 只返回当前发布版本的 `versionId/revision/gifUrl/fallbackUrl`，素材 URL 为 `/api/v1/app/assets/{id}/file` 相对路径；未发布返回 `404 STARTUP_UNPUBLISHED`。 |
| `GET /api/v1/app/home`、`GET /api/v1/app/pages/{id}` | 无；微页面 ID 为 UUID | 只返回当前发布版本的可见组件。首页返回 `versionId/config`，未发布为 `404 HOME_UNPUBLISHED`；微页面返回 `pageId/versionId/name/config`，名称为发布时快照，未发布或非微页面为 `404 PAGE_UNPUBLISHED`；非 UUID 路径由路由层返回 404。`/api/v1/app/bootstrap` 仍待实现。 |
| `GET /api/v1/app/categories`、`GET /api/v1/app/products`、`GET /api/v1/app/products/{id}` | 商品列表支持 `categoryId`、`keyword`、`page`、`pageSize`；详情以商品 ID 获取 | 分类接口返回启用的两级分类；商品接口只返回启用分类下有主图及在售 SKU 的上架商品。列表保留最低在售 SKU 日常价 `minListPriceFen`，并给 `cartEligible` 与 `availabilityCode`；详情每个 SKU 给 `listPriceFen`、当前 `applicablePriceFen`、`priceSource`、`availableQuantity`、`cartEligible`。有效会员按等级显示适用价，游客显示日常价。库存按默认启用仓的 `(账面－预留) ÷ 当前销售单位比例` 计算。即使有库存，`purchasable` 仍为 false，直到订单链路验收；下架后详情及素材返回 404。 |

建议错误码：`AUTH_REQUIRED`、`SESSION_EXPIRED`、`PERMISSION_DENIED`、`VALIDATION_FAILED`、`REVISION_CONFLICT`、`CATEGORY_IN_USE`、`SKU_CODE_DUPLICATE`、`SKU_SPEC_IMMUTABLE`、`MEDIA_INVALID`、`PUBLISH_TARGET_INVALID`、`STOCK_NOT_READY`、`RATE_LIMITED`、`INTERNAL_ERROR`。状态码分别使用 400/401/403/404/409/422/429/500；业务错误码是客户端稳定判断依据，文案可调整。

### 3.1 商品创建与修改结构

建议商品只能绑定二级分类。创建请求的业务结构如下；服务端生成的 ID、修订号和操作时间由响应返回，客户端不自行指定。

| 对象 | 请求字段 | 校验 |
|---|---|---|
| 商品 | `productNo`、`name`、`categoryId`、`fulfillmentKind`、`status`、`descriptionHtml`、`mainImageAssetId`、`galleryAssetIds[]`、可选 `videoAssetId` | 新建状态为 `DRAFT`；编号 1—64 字符且大小写不重复，名称最多 120 字符；分类为已启用二级分类；履约类型固定为发货或核销；富文本净化；主图计入图片总数，合计最多 9 张、视频最多 1 条。 |
| 规格项 | `specAxes[{clientKey,name,sortOrder,options:[{clientKey,value,sortOrder}]}]` | 每商品最多 2 项、每项最多 20 值；同项内值不重复，排序唯一。`clientKey` 只用于单次请求关联，不进入业务编号。 |
| SKU | `skus[{id?,skuCode,specOptionKeys[],listPriceFen,saleStatus,gradePrices:[{gradeId,priceFen}],unit:{baseUnit,saleUnit,ratio}}]` | 最多 100 个不同组合；规格值必须来自本商品；编码 1—64 字符且大小写不重复，金额非负、换算比为正整数；等级 ID 必须存在且启用。规格组合唯一键使用服务端稳定 ID，不信任客户端名称。 |

创建响应返回 `productId`、`productRevision`、每个 `clientKey` 对应的服务端规格 ID、每个 SKU 的 `skuId` 和 `skuRevision`。规格修改请求同时带 `expectedRevision`，已有 SKU 带 `id` 与 `expectedSkuRevision`；新增 SKU 无 `id`。当前仅允许修改草稿商品，订单与库存流水尚未接入；以后若已有 SKU 被交易或库存引用，数据库保护关系须阻止移除，并在业务层给出具体影响和可恢复的错误。商品规格及其 SKU 的一次保存要么全部成功，要么全部失败。

当前已实现的创建接口返回商品和 SKU ID/修订号；规格稳定 ID 可从商品详情读取。商品 PATCH 支持名称、分类、履约类型、净化后的描述及 `mainImageAssetId`、`galleryAssetIds`、`videoAssetId`，拒绝独立修改 `status`；素材字段可传 `null` 或空数组移除。已有草稿商品的完整规格矩阵与 SKU 通过 `/specs/preview` 和 `/specs` 原子修改；草稿规格保存不直接上架 SKU。

### 3.2 页面配置结构与发布

`page_config_version.config_json` 建议包含 `schemaVersion: 1`、`pageType: HOME/MICRO`、`theme`、`components[]`。首页 `theme` 包含 `pageBackgroundColor`、`headerBackgroundColor`、`brandTextColor`；颜色按实际对比度和目标设备验证。每个组件有稳定 `componentId`、`type`、`sortOrder`、`visible`、对应 `props`；同页面排序不得重复。

| 组件类型 | `props` 必填项 | 发布前校验 |
|---|---|---|
| `CAROUSEL` | `slides[{assetId,link?}]` | 素材可用，链接目标有效。 |
| `IMAGE_HOTZONE` | `assetId`、`areas[{x,y,width,height,link}]` | 坐标以图片左上角为原点，四值在 `[0,1]` 且 `x + width <= 1`、`y + height <= 1`；链接目标有效。 |
| `DIVIDER` | `style` | 只接受服务端允许的样式枚举。 |
| `SEARCH` | 可选 `placeholder` | 搜索目标为站内搜索页。 |
| `NOTICE` | `text`、可选 `link` | 文案非空，链接目标有效。 |
| `FILING` | `recordNo` | 正式备案号由运营或合规提供，未提供时不能用占位号发布。 |

链接结构为 `{type: PRODUCT|CATEGORY|PAGE|FUNCTION, targetId}`；`PAGE` 只接受独立微页面 UUID，草稿可引用尚未发布的微页面，预览/发布必须确认目标已发布，并拒绝自引用和当前发布图中的循环。不接受任意外链；商品和分类目标发布时须符合上架/启用状态，功能目标限 `SEARCH`/`CATALOG`。首页和微页面发布先验证当前可见组件与链接，再在一个事务中生成不可变版本、切换 `page_publication` 指针并写审计；`expectedRevision` 过期时拒绝，同一请求键重试只产生一个版本。本段记录原发布切片；E1历史与回退见5.22，事件待发送机制另属E2。

启动 GIF 配置独立于页面配置。发布时同样检查草稿修订、素材可用性和权限，生成不可变版本并切换 `startup_publication` 指针；失败时用户端继续读取旧版本和兜底图。

### 3.3 阶段 A 权限与会话基线

阶段 A 操作码与接口映射、预设组矩阵及拒绝用例详见[阶段 A 开工准备与评审记录](phase-a-preparation.md#2-阶段-a-权限评审)。后续阶段再增加库存、订单、收款、退款与代码发布的细粒度操作码。主账号可调整组权限，但子账号不能给自己增加权限。

| 账号或预设组 | 阶段 A 默认权限 |
|---|---|
| 主账号 | 所有阶段 A 权限；后续模块新增时仍拥有全部后台权限。 |
| 商品运营 | 商品/分类/SKU 读取与编辑、SKU 状态/单位/价格写入、素材读取/上传；复合商品写入需逐项校验。 |
| 订单售后 | 无阶段 A 业务操作权限；阶段 C、D 增加订单、履约及售后权限时再配置。 |
| 库存管理 | 无阶段 A 业务操作权限；阶段 B 增加库存权限时再配置，不获得 SKU 改价权限。 |
| 会员营销 | 首页/微页面与启动配置读取、草稿编辑、素材读取/上传；默认无发布权。阶段 D 再增加会员与营销权限。 |

主账号首次建立由本机管理命令交互输入凭据，仅允许建立一个 `OWNER`，不提供代码内默认密码；实际初始化前指定凭据保管与交接人。PC 会话采用服务端存储、HttpOnly Cookie 与 CSRF 防护；每次请求检查账号启用状态、会话空闲时间和当前授权版本，不信任登录时返回的 `permissionCodes` 缓存。账号组变更、组权限修改或组停用时，事务内提升所有受影响账号的 `auth_version`；资金与发布等高影响动作还需二次确认并重新校验会话。登录失败 5 次限制 15 分钟、空闲 30 分钟会话失效，来源频率限制按需求文档第 12.1 节执行。

## 4. 交易关键数据对象：阶段 B、C 开发前定稿

| 表/实体 | 必须保存的字段 | 强约束 |
|---|---|---|
| `warehouse`、`inventory_balance` | 仓库及唯一默认仓；`(warehouse_id, sku_id)`、`on_hand_base_units`、`reserved_base_units` | 同一 SKU/仓库余额行唯一；`0 <= reserved <= on_hand`。已付款待履约数量单独统计，不再减一次账面库存。 |
| `inventory_reservation`、`inventory_ledger` | 订单项、仓库、SKU、基本单位数量、换算版本、占用/确认/释放状态；不可变流水及业务编号 | 一个订单项在同一仓的有效占用唯一；流水不直接修改或删除，纠错写反向流水。 |
| `customer_identity`、`member` | 微信身份标识与站内会员 ID、等级及状态 | 手机号不是唯一身份或自动合并依据；用户私有资源按会员 ID 校验归属。 |
| `order`、`order_item` | 订单号、会员、固定支付方式、支付状态、超时快照、金额；商品/SKU/规格/单位/数量/价格/分摊/仓库/履约类型快照 | 订单号唯一；订单项成交快照不可随商品编辑改变；金额分摊之和等于订单金额。 |
| `coupon_hold`、`points_account`、`points_hold`、`points_ledger` | 订单关联、暂占/冻结/核销/释放状态与数量；每会员一行积分账户，记已结算余额与冻结总量 | 同一权益不能被两个有效订单重复占用；`frozen_points >= 0`，已结算余额允许因售后扣回为负；零元订单和取消/超时路径完整结算。 |
| `payment_event`、`payment_receipt`、`payment_anomaly`、`payment_anomaly_event` | C0 保存渠道、收款账户内部标识、订单号、外部流水号、分单位金额、到账时间、可信来源、登记完成标记和应用时间；事件 ID/摘要；异常当前原因、状态及不可改写历史 | 实际到账按渠道＋账户＋外部交易号唯一；事件按渠道＋账户＋事件 ID 唯一；每笔到账最多一个异常对象，每订单最多一笔已应用收据。凭证资金事实、通知和异常历史不可改写；关闭后到账不恢复。预支付 `payment_attempt`、验签适配器及人工处置人字段随 C1/C2 接入。 |
| `idempotency_record`、`outbox_event` | 操作者/范围/请求键/内容摘要/业务结果；事件类型、业务 ID、投递状态与次数 | `(actor, scope, key)` 唯一；成功结果和关键事件与业务写入同一事务，投递可重试。失败回滚不留下已占用键；成功键在关联业务保留期间不能复用。 |

## 5. 交易关键 API 与并发结果

| 方法与路径 | 核心输入 | 结果与幂等约束 |
|---|---|---|
| `POST /api/v1/app/checkout/quotes` | `{items:[{skuId,quantity,seenPriceFen?}],addressId?,couponId?,pointsToUse?}`；最多 50 个不同 SKU，数量 1—9999；券 ID 为本人可用券，积分为非负整数 | 返回 `quoteId/expiresAt/lines/goodsTotalFen/shippingFeeFen/couponDiscountFen/pointsDiscountFen/payableFen/availableCoupons/availablePoints/selectedCouponId/pointsToUse/ready/confirmRequired/addressRequired/availablePaymentMethods/orderSubmissionAvailable`。`availablePaymentMethods` 只列服务端已开放方式，当前 `[]`；布尔字段表示至少一种方式已开放，不替代商品、地址、价格及会员校验。报价不占库存或权益，提交时重新校验。 |
| `GET/PUT /api/v1/admin/settlement/shipping-policy` | 需 `settlement.shipping.manage`；写入 `{feeFen,deliveryScope:'NATIONWIDE',expectedRevision}` | 首版每笔含快递商品的订单收取一次固定运费，纯核销单为零；初始化金额 1000 分，全国配送。金额 0—1000000 分，修订冲突返回 409；写入审计。 |
| `POST /api/v1/app/orders` | `{quoteId,paymentMethod}` 和 `Idempotency-Key` UUID 请求头；优惠选择固定在报价中 | 提交按 `paymentMethod` 独立校验开放条件，关闭方式返回 `SETTLEMENT_NOT_READY`，当前两种均关闭。隔离测试路径重验售价、库存、运费及权益；同事务创建订单与占用、保存优惠分摊。应付大于零为待付款，零元直接结算且不生成虚构资金收据；零元公开提交也受方式闸门保护。 |
| `GET /api/v1/app/orders/{id}` | 登录用户与订单 ID | 只返回本人订单；明确支付、履约、优惠及异常状态，客户端超时后以此查询最终结果。 |
| `POST /api/v1/app/orders/{id}/wechat-prepay` | 订单 ID、幂等键 | 仅固定微信支付且仍待付款的订单可发起；预支付受理不等于订单已付款。 |
| `POST /api/v1/callbacks/wechat/pay` | 微信原始通知与签名头 | 验签、解密并核对商户号、订单号、金额后，按事件 ID 与订单状态幂等处理；关闭后到账进入异常队列。 |
| `POST /api/v1/admin/orders/{id}/confirm-offline-payment` | 实际到账金额、方式、凭证引用、必要备注、二次确认 | 仅授权人员核实到账后调用；金额不符拒绝确认并记录原因；只允许一次有效收款。 |
| `POST /api/v1/app/orders/{id}/cancel`、内部超时任务 | 订单 ID、幂等键或到期时间快照 | 仅待付款订单释放占用并关闭；收款确认与关单竞争同一订单锁，只能有一个有效状态结果。 |

并发锁序建议：提交订单按 `(warehouse_id, sku_id)` 排序后锁定余额行，再锁券和积分记录；收款、关单先锁订单行，再按固定顺序处理占用与余额。外部支付请求与回调处理不占用长数据库事务。需要用 PostgreSQL 并发用例验证多 SKU 部分不足、重复提交、回调与超时竞争、迟到通知及零元订单。具体隔离级别、锁等待超时和死锁重试次数由实现试验定稿。

### 5.1 B0 交易实现契约：失败重试、迟到账与积分冻结

以下是阶段 B、C 实现必须遵守的设计约束，尚不是功能或并发测试通过记录。阶段 B 负责下单幂等、库存与权益占用；阶段 C 负责真实收款、超时异常与迟到账款项处置。阶段 D 的积分发放、有效期运营和售后退款继续使用同一积分账户与流水，不另建可绕开冻结的余额口径。

1. **失败的幂等键与期限。** 下单以认证会员、操作范围和客户端生成的 `Idempotency-Key` 定义键空间，请求的规范化业务内容计算摘要，不能用报价标识或 HTTP 原文代替摘要。同键并发先取得 PostgreSQL 事务级锁，再查唯一记录；只在订单及全部占用成功的同一事务中写入成功记录，返回订单 ID、稳定状态和结果摘要。校验失败、库存不足、死锁最终失败或事务回滚都不保留“处理中”或失败占用记录，故原请求可在条件变化后安全重试；失败审计在事务外另记。已有成功记录：内容相同返回同一订单及当前可查询状态，内容不同返回 `IDEMPOTENCY_CONFLICT`。客户端改动内容时应生成新键。成功键的完整结果保留 24 小时供直接重放；其后同键返回 `IDEMPOTENCY_KEY_EXPIRED` 并附原订单可查询标识，绝不创建新订单。键唯一记录/墓碑至少随关联订单保留，不能因响应缓存过期而重新开放。外部支付交易号去重独立于客户端键，不受 24 小时窗口影响。
2. **迟到账身份、补偿与责任。** 微信到账先验签及核对商户号、订单号、金额，再用 `(channel, merchant_account_id, external_trade_no)` 唯一登记实际资金；通知事件另按渠道事件 ID 去重。到账凭证先独立持久化；关闭订单的有效到账在后续短事务中关联唯一 `payment_anomaly(receipt_id)` 并写待处理状态，不恢复订单、库存或权益。线下到账必须记录可核对的银行/渠道流水号与收款账户；没有可核对流水号的报告只作为待核查线索，不自动生成已到账记录或确认订单。回调结果未知或补偿任务重试时，先按渠道订单号查单或人工对账，再按同一资金身份补写收据和异常；外部退款请求经 outbox 异步执行并用该收据派生的退款业务号幂等。`payments` 交易域拥有异常及退款状态，`orders` 只提供关闭状态与关联，库存模块不直接处理资金。阶段 C 默认由授予 `payment.anomaly.resolve` 与 `refund.initiate` 的订单售后账号核对并发起处置；线下资金实际退回只能由另一名授予 `refund.confirm` 的账号确认，服务端比较两次操作的账号 ID。迟到线下款默认退款；若用户同意新交易，须新建订单重新校验价格、库存与优惠，并关联原单及资金流水。未核实资金去向前不得把异常标成已解决。
3. **积分账户与冻结。** 每会员一条 `points_account`，以带符号的 `settled_points` 记录已结算余额，以非负 `frozen_points` 记录有效冻结；可用积分为 `max(0, settled_points - frozen_points)`，已过期积分不得计入可用。下单事务在统一锁序的库存、券之后锁该账户行，处理到期批次，核对可用量，再创建同订单唯一的 `points_hold` 并增加冻结量；积分有效期批次及冻结分配需保留，以便释放或退款时追溯。确认收款时在订单锁及账户锁保护下将冻结量和已结算余额各扣一次并写不可变流水；关单时只释放冻结，不扣已结算余额；零元订单在创建事务内直接结算。重复确认、释放或相反状态竞争以订单和冻结状态拒绝第二次转换。售后扣回可使已结算余额为负，后续获赠积分先抵负数；冻结前始终重新核对有效可用积分。阶段 B 若优惠券和积分来源尚未接入，不得把未校验的客户端权益金额用于报价或下单，也不能将含权益结算标为验收通过。

### 5.2 B1 仓库、余额与入库接口

B1 的数据由 `inventory` 模块负责。仓库编码大小写唯一，首个仓库必须是唯一默认仓；本切片可新增非默认仓，默认仓切换与停用留待订单锁库规则接入时实现。`inventory_balance(warehouse_id, sku_id)` 唯一，数据库约束 `on_hand_base_units >= 0`、`0 <= reserved_base_units <= on_hand_base_units`。已确认的库存流水由 PostgreSQL 触发器拒绝更新和删除；错误须在后续调整单能力中写反向流水。`catalog/inventory_access.py` 是库存模块读取 SKU 当前单位的入口，库存权限不授予价格编辑。已有入库草稿、余额或流水引用的 SKU 不得移除、改编码、改基本单位或改已使用规格含义；商品规格预览和保存、SKU 单位接口均重新校验并返回 `SKU_INVENTORY_REFERENCED`，数据库外键继续保护关联行。

| 接口 | 输入及结果 | 权限与事务 |
|---|---|---|
| `GET/POST /api/v1/admin/warehouses` | GET 返回 `{items:[{warehouseId,code,name,isDefault,enabled,revision}]}`；POST `{code,name,isDefault}` 创建仓库。 | `inventory.read` / `inventory.manage`；编码和唯一默认仓由数据库兜底。 |
| `GET /api/v1/admin/inventory/skus` | `keyword/page/pageSize`；只返回 SKU ID、编码、商品名、当前基本/销售单位、比例及单位版本，不返回价格。 | `inventory.read`；分页上限 100。 |
| `GET /api/v1/admin/inventory/balances` | 可按 `warehouseId/keyword` 筛选；分页返回仓库、SKU、基本单位、账面、锁定、可售数量。仅列已有余额行，未入库的 SKU 不伪装为可售。 | `inventory.read`；可售量由服务端以账面减锁定计算。 |
| `GET/POST /api/v1/admin/inventory/inbounds`、`GET /inbounds/{id}` | POST `{warehouseId,reason,items:[{skuId,quantity,unit:BASE\|SALE}]}` 与 UUID `Idempotency-Key` 保存 1—50 行草稿；明细返回每行操作单位、版本、比例及基本单位换算快照。列表和详情可重新打开。 | 读需 `inventory.read`，创建需 `inventory.manage`；`(actor,key)` 唯一，同键同内容返回同一草稿、不同内容冲突；草稿不改余额。 |
| `POST /api/v1/admin/inventory/inbounds/{id}/confirm` | `{expectedRevision}` 和 UUID `Idempotency-Key`；同一确认键由原操作人重试返回同一结果，不同键重复确认或过期修订返回冲突。 | `inventory.manage`；锁定入库单和有序 SKU，重查仓库及当前单位版本，全部余额、流水、单据状态和审计在同一事务提交。 |

B1 仅完成真实入库与库存读取；人工出库、盘点、订单占用及销售扣减尚未开放。小程序商品仍沿用 `STOCK_NOT_READY` 的不可购买状态，直到完整结算链路接入并验证，不能仅因已有账面数量就开放购买。

### 5.3 B2 人工出库与库存流水

`inventory` 模块负责非销售人工出库和库存流水的查询。人工出库原因限定为报损、样品领用、内部使用、其他出库；草稿保存操作数量、单位版本和换算快照，不改变余额。确认时重新检查仓库、SKU 当前单位版本以及每行最新可售数量 `on_hand_base_units - reserved_base_units`。出库只能扣未锁定库存，不能使账面为负或侵占订单锁定量；整单任一行不足则不产生任何余额、流水或确认状态。订单发货不使用人工出库接口，也不再次扣减库存。

| 接口 | 输入及结果 | 权限与事务 |
|---|---|---|
| `GET/POST /api/v1/admin/inventory/outbounds`、`GET /outbounds/{id}` | POST `{warehouseId,reason:DAMAGE\|SAMPLE\|INTERNAL\|OTHER,note,items:[{skuId,quantity,unit:BASE\|SALE}]}` 与 UUID `Idempotency-Key` 保存 1—50 行草稿；`note` 可空、最多 200 字。返回单号、修订号、每行基本单位换算快照和状态；列表可分页，详情可重新打开。 | 读需 `inventory.read`，创建需 `inventory.manage`；同一操作者、同一键、同一内容返回原草稿，换内容冲突。 |
| `POST /api/v1/admin/inventory/outbounds/{id}/confirm` | `{expectedRevision}` 与 UUID `Idempotency-Key`；同键由原确认人重试返回原单，不同键重复确认或修订过期返回冲突。 | `inventory.manage`；按稳定顺序锁单据、SKU 和余额，整单扣减、不可改写流水、状态及成功审计同事务提交。 |
| `GET /api/v1/admin/inventory/ledgers`、`GET /ledgers/{id}` | 分页查询入库/人工出库流水，可按仓库、SKU、变动类型及关键词（商品名、SKU 编码、单号）筛选；返回关联单据、操作数量与单位、比例、历史基本单位、基本单位变动、变动前后余额、原因、操作人和时间。 | `inventory.read`；仅只读，不提供直接改写或删除接口，数据库触发器继续拒绝变更。 |

盘点任务、差异审核与调整流水另设切片；B2 的人工出库和账面余额不能代替下单锁库与销售扣减。公开小程序购买状态继续保持 `STOCK_NOT_READY`。

### 5.4 库存盘点任务、差异审核与调整流水

`inventory` 模块拥有盘点任务、盘点明细、差异审核及调整流水。任务按一个仓库、1—50 个指定 SKU 创建，记录开始时的账面数量与单位；盘点期间不冻结仓库，原有入库和人工出库照常进行。盘点人提交全部实盘数量时，服务端按稳定 SKU 顺序取得锁、重读最新账面及锁定量，以 `实盘 - 提交时账面` 形成待审核差异。盘点原因与提交快照供审核，不在提交时改余额。

| 接口 | 输入及结果 | 权限与事务 |
|---|---|---|
| `GET/POST /api/v1/admin/inventory/stocktakes`、`GET /stocktakes/{id}` | POST `{warehouseId,skuIds:[...]}` 与 UUID `Idempotency-Key` 建立 1—50 行盘点任务；列表按仓库、状态分页；详情包含单号、状态、修订号、盘点人/审核人和各 SKU 的开始账面、提交账面、实盘、差异、原因。待审核详情另给出当前账面、当前锁定量及 `bookChanged`，提示审核前的变化。 | 读取需 `inventory.read`，创建需 `inventory.manage`；同一操作者与键的相同内容只创建一单，不同内容冲突。 |
| `POST /stocktakes/{id}/submit` | `{expectedRevision,items:[{skuId,countedBaseUnits,reason}]}` 与 UUID `Idempotency-Key`；须提交任务内全部 SKU，数量为非负基本单位整数，有差异时须填原因；返回最新账面和待审核差异。 | `inventory.manage`；修订不符拒绝覆盖，单位或 SKU 身份变化拒绝；同键重试返回同一结果，提交失败不留部分结果。 |
| `POST /stocktakes/{id}/return` | `{expectedRevision,reason}` 与 UUID `Idempotency-Key`；待审核单退回盘点中，保留可追溯的审核意见；盘点人重新录入并提交。 | `inventory.review`；不能直接编辑待审核数据。 |
| `POST /stocktakes/{id}/approve` | `{expectedRevision}` 与 UUID `Idempotency-Key`；审核前重读最新账面及锁定量。若数量、锁定量或余额行更新时间与提交快照不同，返回 `BOOK_CHANGED`，须退回重盘；如调减后低于锁定量则拒绝。 | `inventory.review`；同一事务内更新全部余额、生成非零差异的不可改写调整流水、更新任务状态并记录成功审计。重复同键不重复调账，不同键/过期修订拒绝。主账号拥有该权限；库存管理预设组默认只含读写，审核需单独授予。 |

零差异审核可以完成任务而不生成零数量流水。退回时将本次提交的逐行账面、实盘、差异、原因和修订号留在审计日志，再清空可编辑明细供重盘。已被后续操作取代的幂等动作重放返回 `ACTION_ALREADY_APPLIED`，不回退任务状态。调整流水保留操作前后余额、基本单位、差异原因和盘点明细来源，不能直接改写或删除；更正已审核盘点应发起新的盘点任务。选定 SKU 范围以每单 50 行为上限，整仓自动分批创建尚未开放。该切片不开放小程序购买；订单占用、销售扣减与 C01—C11 并发验收仍在后续交易切片。

### 5.5 B3 选购与服务端报价

`customers` 拥有微信身份、会员站内令牌及收货地址；`catalog` 拥有日常价、等级价、单位及商品状态；`inventory/availability.py` 提供默认仓只读可售量；`checkout` 只生成十分钟有效的报价快照，不修改余额或预留量。既有第 5.4 节盘点切片历史上也曾标为 B3；本节起按用户明确指定的 **B3「选购与报价」** 命名，盘点以业务名称识别。

| 接口 | 输入及结果 | 保护边界 |
|---|---|---|
| `POST /api/v1/app/auth/wechat`、`POST /auth/logout`、`GET /me` | `wx.login` 一次性 code 在服务端向微信换取 openid；登录返回站内 `accessToken/expiresAt/member`，登出撤销当前令牌。 | AppID 与密钥仅从环境读取；未配置返回 503，无开发身份绕过。微信 session_key 不保存、不回传；令牌摘要入库，会员停用、失效或更换 AppID 后拒绝。 |
| `GET/POST /api/v1/app/addresses`、`PUT/DELETE /api/v1/app/addresses/{id}` | 地址含收件人、电话、省市区、详细地址与默认标记；修改/删除须 `expectedRevision`。 | Bearer 会员仅能管理本人有效地址；最多 20 条；单会员最多一个有效默认地址由数据库部分唯一约束保证。 |
| `POST /api/v1/app/checkout/quotes` | 请求和返回字段见第 5 节总表。无效/下架/库存不足逐行标示，变化价格需明确确认；游客按日常价报价，会员按有效等级价报价。 | 无效令牌返回 401；跨会员地址拒绝；同一来源 15 分钟最多 60 次。重复报价产生独立快照且不占库。快照到期由 `purge_checkout_quotes` 定期清理；未来订单不得信任旧快照。 |

购物车仅在小程序本机保存 SKU、数量、选中状态及上次展示价，最多 50 行；它不代替服务器库存或价格。登录中断仅保留 SKU、数量及非个人价格提示，回来后重新读取地址和报价。运费、可用券和积分由服务端报价；页面展示应付拆分，公开下单和付款入口仍关闭，不显示虚构的实付完成态。

### 5.6 B4 订单事务与公开提交闸门

`orders` 拥有 `customer_order`、`customer_order_line` 和成功防重复键；`inventory` 拥有 `inventory_reservation` 及不可改写的占用事件。订单提交在一个 PostgreSQL 事务内按既有入出库的锁序先锁商品/SKU 并重新核对在售状态、等级适用价、单位版本和报价金额，再按 `(warehouse_id, sku_id)` 顺序锁余额并重查最终可售量；订单与逐项成交快照、占用记录、防重复键同时提交。任一行不足或中途失败，整单及全部占用回滚，失败键可在条件变化后重试。相同会员、范围与 UUID 键的相同业务内容在 24 小时内返回原订单当前状态；不同内容返回 `IDEMPOTENCY_CONFLICT`；过期成功键返回 `IDEMPOTENCY_KEY_EXPIRED` 和原订单 ID，不重新建单。订单金额及逐行成交事实和占用事件由数据库触发器拒绝改写。

取消与到期关闭先锁订单，再按余额顺序释放有效占用；重复关闭不二次释放。`close_expired_orders` 管理命令需由部署环境定时执行；本机仅验证命令和事务行为，不能把未配置调度的环境视为自动关单。订单详情和取消只允许所属会员访问。C0 已用独立 PostgreSQL 连接验证内部可信收款入口与取消/超时竞争；实际渠道关单竞态仍需 C2 真实商户联调。

**公开购买闸门仍关闭：** `payments.availability` 统一读取 `ORDER_PAYMENT_METHODS_ENABLED`，当前 `WECHAT:false`、`OFFLINE:false`。提交入口逐方式校验；商品 `purchasable` 为“有可售库存且至少一种方式开放”，报价返回可用方式及聚合布尔值，当前均不可提交。小程序独立交互入口继续关闭，随 C1/C2 的对应页面接入逐方式控制。开放某方式前须完成该方式的真实收款核实、并发边界、调度和页面交互；不能因线下可用而同时开放微信。测试库订单不代表本机用户实际下单。

### 5.7 B4 结算闭环的当前边界

`shipping` 拥有唯一运费配置及修订号。首版快递按单固定 1000 分，纯核销不收费，混合单只收一次；全国地址适用。优惠券和积分只抵商品金额，不抵运费；积分按 100 积分抵 1 元、用券后商品金额最多抵 20%。`benefits` 拥有券适用性、会员归属、每会员唯一的已结算／冻结积分账户、积分有效期批次，以及占用、释放、核销入口。报价取账户可用量和未到期批次可用量的较小值；下单时在同一事务内锁账户与批次并重验。`checkout` 保存十分钟报价快照，`orders` 在提交事务内再次调用 `shipping` 与 `benefits`，并锁定价格、库存与权益。运费修订或权益变化导致旧报价拒绝；失败时订单、库存、券和积分共同回滚。

订单和订单项记录券/积分分摊与实际应付，数据库约束保证金额等式；财务快照、订单项、库存占用事件、积分和券状态事件由 PostgreSQL 阻止改写。零元订单在同一事务内把库存占用转为销售扣减，写 `SALE` 库存流水并核销权益。待付款单取消或到期则释放权益与库存。优惠券活动创建、领取、后台发放以及积分规则运营界面属于后续营销切片；当前测试通过受信数据入口构造权益，不将它称为已可运营的营销系统。C0 内部结算边界见下节，真实线下/微信渠道仍待接入。

### 5.8 C0 收款状态机与异常基础

- `payments.service.record_verified_payment(VerifiedPayment)` 只接受未来验签/查单适配器或有权限的线下到账核实流程提供的可信结果，C0 没有公开收款确认、模拟回调或后台资金写接口。渠道与来源须一致；金额为正整数分，账户内部标识和可核对流水号必填。用户付款报告不能调用该入口。
- 入口禁止嵌套外部事务：先独立提交到账凭证，再独立登记通知事件，最后应用订单。带事件的新凭证初始 `ready_for_settlement:false`；事件身份核验成功后才可结算。事件冲突保留新到账，进入 `IDENTIFIER_CONFLICT`；恢复入口不能越过未完成登记或身份冲突。
- 结算事务先锁订单，再锁收据，按既有顺序核对并结算库存、券和积分。库存占用必须覆盖全部订单项且数量、SKU、仓库一致；权益占用金额、积分总数及会员归属必须与成交快照一致。订单已付款、已关闭或方式/金额不符的另一笔真实到账均记异常，不重复结算。未关单但已到期的订单先关闭释放，再将到账记异常，不以到账时间回写恢复原单。
- 结算中途失败不抹去已提交资金事实；库存、券、积分、订单和凭证应用时间共同回滚，记录 `SETTLEMENT_FAILED`。`settle_recorded_payment(UUID)` 是内部恢复入口；成功后解决异常并写历史，订单已关闭则重分类为 `CLOSED_ORDER`。旧失败记录不能覆盖新的身份冲突或其他待核查原因。
- 异常当前状态只有 `OPEN/RESOLVED`；C0 仅自动解决成功重试的结算故障，资金不符、迟到账和身份冲突不自动处置。凭证、通知、异常历史、订单状态时间约束及一单最多一笔应用收据均有 PostgreSQL 保护。人工退款/补单、权限及二次确认、预支付、验签、查单和部署补偿调度留后续切片，不能将 C0 自动化称为真实收款验收。

## 6. 阶段 A 评审与未决项

1. 项目已确认：商品只绑定启用的二级分类；分类、商品、SKU 状态枚举；商品编号与 SKU 编码长度/字符规则及大小写唯一；规格稳定 ID 组合唯一；本机临时素材上限；阶段 A 默认权限组；唯一主账号交互初始化。首批迁移需验证约束与索引实际可执行。
2. 商品请求和页面组件结构仍是候选字段表；实施时定稿其他展示字段长度、本机媒体目录、富文本净化白名单及上传失败清理。正式素材尺寸、体积和性能经目标手机实测。
3. 会话与 CSRF 方案按第 3.3 节实施；本机 HTTP 与未来 HTTPS 的 Cookie 配置应分环境验证。主账号凭据保管与交接人在实际初始化前指定。
4. 微信账号的自动提审、发布和回退路径仍在实施待验证清单 T-01；没有真实账号结论前，代码版本管理接口不写成已可自动发布。
5. 微信支付商户号尚无，交易接口均为设计契约；不能用模拟回调替代真实验签、退款和资金结果联调。

阶段 A 的接口、数据库迁移及服务端权限测试通过后，再按《开发交付说明》第 6 节记录页面、前后端联调和真机验证证据。本文在评审通过前保持草案状态。

### 5.9 阶段 C1：线下付款说明、付款报告与授权到账核实

C1 接入线下服务端与客户端闭环，**公开下单仍按支付方式关闭**。付款配置保存不改变闸门；微信收款留 C2。资金证据仍使用第 5.8 节可信入口，用户报告不调用它。

| 接口 | 权限 / 输入 | 输出与语义 |
| --- | --- | --- |
| `GET /api/v1/app/payments/offline-policy` | 公开，只读 | `instructions`、`merchantAccountId`、`revision`、`configured`、`wechatTimeoutMinutes`、`offlineTimeoutMinutes`、只读 `availablePaymentMethods`。未配置时为空，不提供虚构账户。 |
| `GET/PUT /api/v1/admin/payments/offline-policy` | `payment.settings.manage`；PUT 需 CSRF，`expectedRevision` 与上述四个可编辑字段 | 说明最多 4000 字、账户标识最多 80 字；二者同时填写或同时清空；时限为 1—10080 分钟，默认微信 30、线下 1440。修订不符返回 409，成功审计。修改只影响新订单。 |
| `GET /api/v1/app/orders` | 会员 Bearer，本人订单 | `items/page/pageSize/total`；每页 1—100，默认 20。支持状态、方式、`reported=true/false`、订单号 `search`。列表不含后台流水、确认人或其他会员数据。 |
| `GET /api/v1/app/orders/{id}` | 本人订单 | 原订单详情增加 `revision`、不可改写的 `paymentInstructions` 快照（说明、账户标识、配置修订、时限分钟）、`paymentReviewStatus` 与 `paymentReports`。核实状态为 `UNREPORTED/PENDING_REVIEW/PAID/CLOSED`，成交仍以订单 `status` 为准。 |
| `POST /api/v1/app/orders/{id}/payment-report` | 本人；UUID `Idempotency-Key`；`{note}` | 线下待付款、未过期订单可报告，说明 1—500 字，每单最多 20 条；报告不可改写，不生成资金凭证，不改变待付款状态、库存或权益。原键原内容重试复用，改内容拒绝；报告后显示待核实。 |
| `GET /api/v1/admin/orders`、`GET /api/v1/admin/orders/{id}` | `order.read` | 列表分页同上；详情增加实际到账 `receipts` 和操作员核对 `confirmations`，各最多最近 50 条，并返回总数。确认资料含原操作员 `actorId`、授权时间、结果与关联收据。 |
| `POST /api/v1/admin/orders/{id}/offline-reconciliations` | `order.read` + `payment.offline.confirm`；CSRF；UUID `Idempotency-Key` | 输入 `expectedRevision/merchantAccountId/externalTradeNo/amountFen/paidAt/note/verified:true`。实际账户须与下单快照一致；金额为正整数分，时间带时区且不晚于当前；备注最多 500 字，金额不符必须说明原因。保存不可变核对记录，**尚不代表资金登记或付款确认**。返回记录及 `confirmationObjectId`、`confirmationRevision:1`。同操作员原键原内容重试复用。 |
| `POST /api/v1/admin/auth/confirm` | 当前密码；`payment.offline.confirm`；对象为上述不可变核对记录 ID，修订 1 | 原有一次性、五分钟、账号与会话绑定的二次确认。令牌授权的是记录中的固定账户、流水、金额与时间。 |
| `POST /api/v1/admin/payments/offline-reconciliations/{id}/confirm` | 同两项权限、原核对操作员、CSRF；空对象；首次需 `X-Action-Confirmation` | 授权与审计独立持久化，随后调用可信资金入口。结果 `PAID/ANOMALY/PENDING`、`receiptId/orderStatus/order`；金额不符、关闭后或额外到账进入异常，不恢复原单。结算失败返回 503 且保留资金和授权资料。已授权记录可由原操作员在仍有权限时重试，PC 从历史记录恢复同一对象。 |

订单付款说明和待付款时限在提交时保存快照，数据库禁止改写。原成功提交的防重复键可在方式闸门关闭后重放，未成功的新请求仍被拒绝。零元订单沿用统一结算，不虚构到账收据。授权前账户不符、格式错误、缺失差异原因会拒绝核对资料；它们不被当成已验证到账自动记录。C1 以前没有账户快照的历史订单可读取，但本接口不能据此确认线下收款，需后续独立的授权历史资料处理。

小程序付款报告首版为文本（付款人、参考流水及必要说明），不包含图片上传。付款时限到期但服务端尚未关单时，页面停止新付款指引和报告入口，提示刷新／取消，不自行把订单改为关闭；已有未知报告只能原键重试查询处理结果。PC 与小程序不自行计算成交或库存；发货、核销、退款和人工异常处置留后续切片。真实收款资料、核对岗位、真机与部署调度证据是公开开放前置项。

### 5.10 阶段 C2：微信支付研发接口与补偿

C2 已实现开发功能，**未连接真实商户、未发起实际收款，微信和线下公开下单闸门仍关闭**。配置商户资料不会开启闸门。新生产依赖 `cryptography==50.0.1` 经用户确认后加入声明及锁文件，负责成熟 RSA 与 AES-GCM 实现；没有自写密码算法。生产私钥/APIv3 key 不进入数据库、后台表单、API 响应、日志或版本库。

| 接口 / 入口 | 鉴权与输入 | 输出和业务语义 |
| --- | --- | --- |
| `POST /api/v1/app/orders/{id}/wechat/prepay` | 当前会员 Bearer、本人正金额微信订单；空 JSON；UUID `Idempotency-Key`；当前微信方式开放且应用/付款身份匹配 | `{attemptId,paymentParameters:{timeStamp,nonceStr,package,signType:"RSA",paySign},expiresAt}`。同键或新键均复用同一外部支付单，同键跨订单 409。每单最多 20 个重试键。只有待付款且未过期可返回参数；处理中 409、方式关闭 409、商户配置缺失 503。 |
| `POST /api/v1/app/orders/{id}/wechat/query` | 当前会员、本人微信订单、空 JSON；已存在交易的查询不依赖公开闸门 | `{paymentState,order}`；状态为 `NOTPAY/USERPAYING/SUCCESS/CLOSED/REVOKED/PAYERROR`，结果未知时返回可重试错误并保留操作证据。不存在本地支付尝试时，不调用微信，返回本地 `NOTPAY/CLOSED`。成功查单先登记可信资金再走 C0 结算；金额取 `amount.total`，不以优惠后的 `payer_total` 取代订单应付额。 |
| `POST /api/v1/payments/wechat/notify` | 无会员会话；必须通过微信原始报文签名、时间戳与 pinned key ID 验证，AES-GCM 解密后核对交易事实；报文最多 64 KiB | 成功 HTTP 204。验签/资料失败 400/401，过大 413，未配置或结算待恢复 503；错误仅返回微信协议 `code:FAIL` 与受控说明，不套商城 JSON envelope。无模拟回调旁路。 |
| 订单详情新增 | 原本人或后台 `order.read` 范围 | `wechatPaymentAvailable`：微信闸门开放、配置齐备、本人订单待付款且未过期、无到账/关闭/处理中快照时为真；缺失字段视为不可支付。`wechatPaymentState`：最近服务端快照，非微信订单为 null。成交仍仅以订单 `status=PAID` 为准。 |
| `manage.py reconcile_wechat_payments --limit 100` | 部署管理命令，无公开管理接口 | 一次最多 1—1000 个到期非终态支付单，先主动查单，已关闭或超时的本地订单再尝试远端关单；网络或关单竞态未知保留待重试。未配置时直接退出且不调用微信。任务不会启用支付方式；外部调度、告警与故障恢复部署留 C4。 |

**支付尝试与并发：** 内部订单号为 33 字符，C2 为每单生成独立 32 字符 UUID hex `out_trade_no` 映射，绝不因网络失败另建外部交易号。`WechatPaymentAttempt` 固定订单、应用、商户、付款 openid、金额及付款期限；`WechatPrepayRequest` 是会员重试键别名；`WechatOperation` 保存 PREPAY/QUERY/CLOSE 的开始与受控结果，无原始报文或秘密。数据库唯一键、正金额、状态/租约/预支付期限一致性和不可改写触发器保护证据。每个支付单每分钟最多 30 次真实 provider 操作；30 秒租约与令牌隔离并发，过期租约的新操作使旧操作不可覆盖最新结果。网络调用不持有订单、库存或权益事务锁。

预支付响应丢失、过期租约或中断后先查单；只有已验签的 `NOTPAY` 或 `ORDERNOTEXIST` 才允许重发同一外部单号。远端 `CLOSED/REVOKED` 经订单域关闭本地待付款单；确定未创建且本地已关闭的任务收敛为关闭。取消/超时在本地原事务中释放占用并登记关闭任务，远端关单失败不恢复订单。`ORDERPAID` 等关单竞态留下一轮查单；迟到账进入 C0 异常队列，原单保持关闭。

**到账信任边界：** 所有响应先验签再解析，通知再解密。必须核对应用、商户、商户订单号及 SUCCESS 的 JSAPI 类型、付款 openid、正整数分金额、CNY、微信交易号和带时区到账时间。非成功查单中的选填对象/字段可缺省，出现时仍校验；完整资金证据仅在 SUCCESS 必需。未知但已验签且身份正确的外部商户订单号按有界非控制字符文本接收，保存 UNKNOWN_ORDER 异常，不用本机产单字符子集丢弃真实到账。资金及通知去重复用 C0：一笔可信资金独立持久化，库存/券/积分结算失败保留收据，原事件或主动查单可恢复，到账快照不会被旧的未付款响应降级。

**小程序：** 报价按服务端方式能力显示微信选项；下单成功后进入订单结果/详情，由用户点击微信支付，客户端不自动付款。`wx.requestPayment` 的成功、取消、失败均先查单并 GET 最新订单；客户端成功不置 PAID。结果未知时仅保存各订单的 UUID 重试键和阶段，重进页面先核查，禁止盲目再付款；不保存签名支付参数或付款人资料。超时/关闭/零元/到账待结算停止新付款，关闭订单可查迟到账；401、退出或切换会员清除私有页面与重试意图。

**部署配置：** `.env.example` 提供空项：`WECHAT_MINI_APP_ID`、`WECHAT_PAY_MERCHANT_ID`、`WECHAT_PAY_MERCHANT_SERIAL`、`WECHAT_PAY_API_V3_KEY_FILE`、`WECHAT_PAY_PRIVATE_KEY_FILE`、`WECHAT_PAY_PLATFORM_KEYS`（key ID/序列号→文件路径 JSON）、`WECHAT_PAY_NOTIFY_URL`（HTTPS）。APIv3 key 文件恰好 32 字节，PEM 私钥及微信公钥/平台证书由部署持有人配置；轮换可同时信任多个明确 key ID，未知 ID 拒绝，证书模式校验序列号与有效期。provider 固定 HTTPS 主机、禁用重定向和环境代理、8 秒 socket 超时、64 KiB 响应上限。金额只设程序/数据库正 bigint 表示边界，不自拟收款业务额度；实际商户限额与风控待联调确认。

协议核对依据：[官方小程序下单](https://pay.wechatpay.cn/doc/v3/merchant/4012791897)、[商户订单号查单](https://pay.wechatpay.cn/doc/v3/merchant/4012526919)、[支付成功通知](https://pay.wechatpay.cn/doc/v3/merchant/4012791861)、[微信支付公钥验签](https://pay.wechatpay.cn/doc/v3/merchant/4013053249)。研发 mock/临时 RSA 与 AES 测试不能替代商户身份、真实金额、重复/丢失通知、微信关单、真机和部署调度验收。

### 5.11 阶段 C3：已付款订单履约研发契约

`fulfillment` 保存发货、收货、核销凭证与不可改写的操作证据。订单 `status` 继续表示收款结果，另返回整单 `fulfillmentStatus` 和每个订单项的 `fulfillment.status`；只有服务端已确认 `PAID` 才能履约。收款时已将库存占用转为销售扣减，发货和核销均不再扣库存。线下与微信公开下单闸门仍分别关闭；本片的已付款演示订单只在隔离本机库中使用合成到账凭证。

| 接口 / 入口 | 鉴权与输入 | 结果和边界 |
| --- | --- | --- |
| `GET/PUT /api/v1/admin/fulfillment/carriers` | GET 需 `fulfillment.read/ship/settings.manage` 之一；PUT 需 `fulfillment.settings.manage`、CSRF；`{code,name,enabled,expectedRevision}` | 管理最多 200 条承运商；只允许启用项用于新发货，历史运单保留当时名称。代码大写字母、数字或下划线 2—24 位；修订冲突 409。初始没有虚构的真实物流账号。 |
| `GET/PUT /api/v1/admin/fulfillment/policy` | GET 需 `fulfillment.read/settings.manage` 之一；PUT 需 `fulfillment.settings.manage`、CSRF；`{autoConfirmDays,expectedRevision}` | 自动收货天数 1—30，初始 10 天；新订单在提交时保存策略修订与天数快照，发货时写入自动确认时刻，后改配置不影响旧单。 |
| `POST /api/v1/admin/orders/{id}/shipment` | `fulfillment.ship`、CSRF、UUID `Idempotency-Key`；`{carrierCode,trackingNo,expectedRevision}` | 仅已付款且含实物项、所有实物项同下单仓的订单可整单发货一次；保存仓库、操作员、承运商、运单及时间。原键原内容重放不二次发货，不再扣库存；不同请求或已发货返回 409。 |
| `POST /api/v1/admin/orders/{id}/shipment/correct` | `fulfillment.ship`、CSRF；`{carrierCode,trackingNo,reason,expectedRevision}` | 留存原运单、新运单、原因、操作员和时间后修改当前展示值；发货事实与时间不撤销。原因 5—500 字，修订冲突 409。 |
| `POST /api/v1/admin/shipments/batch` | `fulfillment.ship`、CSRF；`{items:[{orderId,carrierCode,trackingNo,expectedRevision,requestKey}]}` | 一批 1—50 单，逐单独立短事务和 UUID 重试键；返回每单成功或失败、成功/失败数。PC 只提交当前页已勾选订单，不因一单失败回滚已成功的其他单。 |
| `POST /api/v1/app/orders/{id}/confirm-receipt` | 本人会员 Bearer、空 JSON | 已付款已发货可手工确认一次，重复确认保持同一结果；实物项完成。`manage.py auto_confirm_receipts --limit 100` 按发货快照时刻批量补偿，部署调度留 C4。 |
| `POST /api/v1/admin/redemptions/lookup` | `fulfillment.redeem`、CSRF；`{code}` | 扫码枪或手工输入凭证码，返回凭证/订单项、已核销、剩余、有效期、修订号和可核销状态；错误尝试按操作员 15 分钟内第 5 次后限流。码不放 URL 或后台订单详情。 |
| `POST /api/v1/admin/redemptions/{voucherId}/use` | `fulfillment.redeem`、CSRF、UUID `Idempotency-Key`；`{quantity,expectedRevision}` | 数量为正整数且不超过剩余、有效期未过、订单已付款；锁订单与凭证，更新计数并追加事件。原键同内容重放只生效一次；并发旧修订 409。 |
| `POST /api/v1/admin/redemptions/events/{eventId}/reverse` | 独立 `fulfillment.redeem.reverse`、CSRF；`{reason,expectedRevision}` | 授权撤销一笔核销，追加反向事件并恢复相应剩余次数；同事件重复撤销不重复增加。原因 5—500 字，原事件仍保留。 |

**状态与可见性：** 实物项为待发货、运输中、已收货；核销项为待收款、有效期待核实、待核销、部分核销、已完成或过期。混合单只有所有订单项完成才返回整单 `COMPLETED`。会员本人已付款订单详情及本人订单操作响应可读其随机凭证文字码 `voucherCode` 与只编码该码的 PNG data URL `voucherQrDataUrl`；GET 详情响应为 `Cache-Control: private, no-store`。小程序将图片写入私有用户文件并在刷新／离页时清理，无法显示图片时保留文字码。PC 订单列表/详情、后台付款核对及公开商品接口均不返回凭证明文或二维码。数据库只保存凭证随机 nonce 与校验摘要；订单项有效期、自动收货策略和发货原始事实有快照或不可改写保护，凭证计数受购买数量约束。

**固定截止日与遗留数据：** 用户确认按商品配置的固定 `YYYY-MM-DD` 截止日，使用 `Asia/Shanghai` 当地日期判断，截止日当天可核销，次日起过期；该日期在商品详情与报价展示，并保存至订单项。没有有效期或已过期的核销 SKU 不可新上架或下单。C3 以前已经付款、订单项无有效期快照的核销单仍可读取，但核销服务拒绝使用，不追溯改写成交快照；需单独受控的历史处理规则。二维码由已获授权的生产依赖 `segno==1.6.6` 在本人已付款订单响应时生成，不持久化 PNG 或增加公开图片 URL。自动化和静态编译已覆盖接口及图片格式；开发者工具渲染和真机扫码仍待验收。

**物流与调度：** 订单详情保留已登记的快递公司、运单号、发货/收货时间；轨迹通过下述单独接口查询。物流服务失败不得改变已发货事实。真实物流账号、轨迹联调、微信商户、定时任务部署、模拟器/真机和真实履约验收仍需分层记录。

### 5.12 阶段 C3 尾片与 C4 本机作业契约

| 接口 / 入口 | 鉴权与输入 | 结果和边界 |
| --- | --- | --- |
| `GET /api/v1/app/orders/{id}/tracking` | 本人会员 Bearer；只读本人 `PAID` 且已发货订单 | `carrierCode/carrierName/trackingNo/status/events[{time,description}]/checkedAt/retryAfter`；状态为 `UNAVAILABLE/NO_EVENTS/IN_TRANSIT/DELIVERED/EXCEPTION`。无权限 401，非本人、未付款或未发货 404；响应 `private, no-store`。即使轨迹失败仍返回发货资料，绝不更改订单、库存或发货事实。 |
| `manage.py run_phase_c_jobs --limit 100` | 部署或本机作业入口；每类任务每次 1—500 单 | 单次有界执行超时关单、到期自动收货、已配置商户的微信支付补偿；输出各任务结果。商户配置缺失时 `wechat=SKIPPED_UNCONFIGURED` 且无远端调用；任一实际作业失败仍尝试其他作业，最终非零退出供调度告警和重试。反复运行依赖各域幂等与状态重查，不设置公开下单闸门。 |

轨迹适配固定使用快递鸟 `1002` 即时查询 HTTPS 地址，服务器以账号 ID 与 AppKey 生成请求签名；输入只取已存运单与承运商代码，响应上限 64 KiB、最多保留最近 30 条事件、超时 5 秒，拒绝重定向。可替换 `ShipmentTrackingSnapshot` 保存与运单绑定的查询缓存，15 分钟成功缓存／1 分钟失败缓存和 15 秒并发租约；运单更正后旧缓存不得展示。`KDNIAO_ENABLED` 默认 `0`，同时配置账号 ID 和 AppKey 并显式启用才会发起第三方请求。小程序在已发货详情加载与手动重试时展示加载、无轨迹、异常和事件列表；会员切换的旧响应不再显示。真实账号的承运商代码映射、配额、费用、限流及异常语义待联调，不因 mock 验证而开启开关。[快递鸟即时查询说明](https://www.kdniao.com/api-track?st=1)。

部署调度应由环境持有人配置单实例、非重叠运行的每分钟作业，记录退出码并在失败时告警；设置 PostgreSQL 与密钥环境变量，不把私钥写在命令行或仓库。C4.3 增加 `backend/config/phase_c_local_scheduler.py` 作为**本机隔离库演练入口**：只接受显式指定的本机 C4 可丢弃数据库，清除子进程微信支付及物流账号配置，以按数据库固定的文件锁阻止重叠，子进程继承锁描述符；以有界超时、JSONL 结果和按数据库固定的完成标记及告警记录每轮，重启后跨状态目录检测未完成轮次或漏跑并立即补跑。成功轮次只清除开始前已有告警。它不安装系统级调度或发送远端告警；正式环境仍需部署持有者接入持久调度、告警通道、凭据、监控和恢复流程，真实商户及物流服务也未运行。

### 5.14 D0 售后占用与退款最终确认基础

本片只交付内部服务和既有订单读模型增量，没有公开售后写接口、退款网络适配器或完整操作页面。微信退款验签/查询/真实商户验收、线下凭证表单及密码二次确认由后续切片接入；不得直接把内部 `VerifiedRefund` 作为客户端可提交的数据。

- `aftersales` 拥有 `AfterSalePolicy`（收货后默认15天、范围1—365天）、新订单不可改写的 `OrderAfterSaleSnapshot`、每订单项一个 `AfterSaleAllocation` 和多个 `AfterSaleCase`/不可改写事件。历史无时限快照的订单返回 `AFTERSALE_POLICY_MISSING`，不自动套用当前规则；未发货、已发货、未核销和已核销分别校验。超过站内时限的依法售后义务仍须后续人工路径承接。
- 一申请对应一个订单项，同项最多一笔进行中售后。金额由服务端按 `floor(订单项实付分 × (已退款数量 + 本次数量) / 购买数量) − 已退款商品金额` 计算，最后数量承接尾差；以 `OrderLine.payable_fen` 的历史券/积分分摊为准。占用及累计退款不超过购买数量与商品实付；不把运费混入商品退款。本片暂拒绝零现金分摊申请，纯积分/零现金和券积分退款权益结算留D3；运费退款留独立规则与占用入口。
- 内部入口 `apply_case(member,line_id,kind,quantity,reason,key,redemption_scope='UNUSED')`：本人已付款且有已结算收款证据；原因5—500字、UUID防重复键。`REFUND_ONLY/RETURN_REFUND` 与是否已发货一致；核销范围显式 `UNUSED/USED`，已核销部分须后台单独审核。重复相同申请复用原case，不同内容同键409。
- 状态：`PENDING_REVIEW → WAITING_REFUND`（仅退款）或 `WAITING_RETURN`（退货退款）；待审核可由本人撤销为 `WITHDRAWN`，授权人员可拒绝为 `REJECTED`，二者释放占用。`WAITING_RETURN` 本片不能进入退款，须D2验收入口。`WAITING_REFUND → COMPLETED` 只由可信退款最终确认触发，失败/未知保持占用。审核需 `aftersale.review`，申请/审核/撤销递增订单修订；事件记录操作员与原因。
- 履约与售后均先锁同一Order：当前整单发货入口遇任一实物项进行中售后或已退款时拒绝；核销可用量扣除未核销售后占用，成功后才永久作废。已核销部分售后冻结/成功时拒绝撤销核销，退款不恢复已核销容量、不回库存。未发货实物退款成功沿下单仓和单位快照经 `inventory.refunds.restore_unshipped_refund_locked` 回库一次；已发货退款须验收，本片不自动回库。
- `payments.refunds.prepare_refund` 创建每case唯一意图、原收款凭证/商户/渠道/交易号/金额快照及稳定退款业务号，需 `refund.prepare`。内部派发/查单以30秒租约隔离，网络调用应在事务外；`UNKNOWN/PROCESSING` 禁止新派发，先查单；`FAILED` 仍沿原意图/业务号重试。已有任何资金凭证时禁止再次派发，优先本地结算恢复或人工核查；D0没有实际网络派发。
- 内部 `record_verified_refund(VerifiedRefund)` 先独立提交成功资金证据，再经 `settle_recorded_refund` 锁Order→case→intent→evidence，统一完成库存/核销副作用与售后。微信来源限定通知/主动查询，通知须事件ID；渠道、商户、原付款交易号和金额必须匹配意图。线下证据需不同于意图登记人的当前授权复核员（`refund.offline.confirm`）；没有HTTP接口替代密码二次确认。重复通知不重复退款结算，额外资金身份、金额或事件冲突进入异常，不改已完成业务。
- 资金证据已提交但业务事务失败，返回 `SETTLEMENT_FAILED`、保留证据和占用；恢复不发起新资金动作。失败记录不能覆盖 `IDENTIFIER_CONFLICT` 等待核查异常。`manage.py recover_recorded_refunds --limit 100` 每次最多1—500，跳过人工异常，失败/新异常非零退出；本片未安装部署调度。
- 数据库约束及触发器保护成交金额计数、占用聚合、资金身份唯一、不可改写证据与成功终态。售后完成必须匹配已应用退款资金及原收款；未发货回库和未核销作废也必须匹配同case/订单项/数量，不能只构造任意UUID副作用。隔离空库回退仅证明schema可逆，不代表有资金业务数据的生产降级方案。
- 既有订单列表/详情新增 `afterSaleSummary:{activeCount,refundedQuantity,refundedFen}`；订单项核销新增 `heldQuantity/availableQuantity`，原 `remainingQuantity` 表示尚未永久消耗数量。进行中整单/受影响订单项为 `AFTER_SALE`，全量退款为 `REFUNDED`，部分退款后的其他项独立推进。支付事实仍为 `PAID`，不得把退款等同于删除原收款。PC/小程序已有状态文案已兼容，完整申请/审核/退款页面留D1/D2。

### 5.15 D1 申请、撤销、审核与线下退款复核

D1 接入下列 HTTP 和页面入口，业务数量、金额、占用及退款结算仍沿用5.14；退货验收、微信退款网络适配及真实账号联调后置。全部私有成功读写响应使用 `Cache-Control: private, no-store`。小程序以会员 Bearer 鉴权，不接受后台会话作为会员身份；PC 使用当前后台会话、CSRF 和逐操作权限。

| 接口（均在 `/api/v1` 下） | 输入 / 权限 | 返回 / 处理规则 |
| --- | --- | --- |
| `GET /app/orders/{orderId}/aftersale-options` | 本人会员；最多200订单项 | `{orderId,items:[{lineId,title,fulfillmentKind,quantity,payableFen,options:[{kind,redemptionScope,maxQuantity}],blockedCode,blockedMessage}]}`；批量读取成交、付款、履约、规则快照、已退款与活动占用。选项不是占用承诺，提交须再次重验。 |
| `POST /app/aftersales/preview` | 本人会员；仅 `{lineId,kind,redemptionScope,quantity}` | 返回同身份字段及服务端 `amountFen`；不创建申请或占用。页面不能自行计算退款上限。 |
| `GET /app/aftersales` | 本人会员；`page/pageSize/status/orderId/orderNo` | 本人分页申请；默认20、最多100，每页无资金账户或凭证档案资料。 |
| `POST /app/aftersales` | 本人会员；`Idempotency-Key: UUID`；仅 `{lineId,kind,redemptionScope,quantity,reason}` | 新申请201、重放200；同键不同内容409。`reason`5—500字；同项活动售后拒绝重叠。申请/撤销按近期活动有界限流，重放申请不新增活动。 |
| `GET /app/aftersales/{caseId}` | 本人会员 | 本人case及最近30条处理事件；非本人404。仅安全退款状态，不返回银行卡、原交易、退款流水、归档凭证、复核人员资料。 |
| `POST /app/aftersales/{caseId}/withdraw` | 本人会员；仅 `{expectedRevision}` | 仅待审核可撤销；修订/状态变化409。拒绝或撤销释放占用；会员停用后不能通过内部入口绕过检查。 |
| `GET /admin/aftersales` | `aftersale.read`；同上分页/筛选字段 | case列表含订单号、订单项成交资料、申请类型/数量/商品金额/状态；不附逐项资金详情。 |
| `GET /admin/aftersales/{caseId}` | `aftersale.read` | case、处理事件；另有 `refund.prepare` 或 `refund.offline.confirm` 才返回 `refundSource` 和 `offlineRefund`，普通售后查看人员不获取资金流水与档案号。 |
| `POST /admin/aftersales/{caseId}/review` | `aftersale.read`＋`aftersale.review`；仅 `{approve,reason,expectedRevision}`；密码确认令牌 | `approve`须布尔值、原因5—500字；令牌绑定 `aftersale.review`、case ID、case revision及当前会话。审核通过只进入待退款/待退货；拒绝释放占用，不确认资金。 |
| `POST /admin/aftersales/{caseId}/offline-refunds` | `aftersale.read`＋`refund.prepare`；UUID防重复键 | 仅线下方式、WAITING_REFUND；下述实际转账资料保存为不可改写核对记录，初态 `PENDING_CONFIRMATION`。保存不写成功资金凭证、不回补库存、不作废额度。 |
| `POST /admin/refunds/offline-reconciliations/{id}/confirm` | `aftersale.read`＋`refund.offline.confirm`；空JSON `{}`；密码确认令牌 | 另一名当前授权人员确认保存的资料；令牌绑定 `refund.offline.confirm`、记录ID、revision1和当前会话。已授权记录的恢复只允许原复核员；完成后重复处理沿同一资金身份重放，不重新转账。 |

case DTO 的共同字段为 `caseId/orderId/orderNo/lineId/title/fulfillmentKind/kind/redemptionScope/quantity/amountFen/reason/status/revision/createdAt/updatedAt/canWithdraw`；detail另有 `refundStatus/events[{action,status,reason,occurredAt}]`。后台额外返回支付方式及脱敏会员标识；有资金权限时 `refundSource:{merchantAccountId,originalTradeNo,amountFen}` 来自原已应用收款。PC审核原因会向本人展示，应填写可公开的审核依据。

线下登记 body 严格限定 `expectedRevision/merchantAccountId/externalRefundNo/amountFen/refundedAt/refundMethod/proofReference/note/verified`。实际账户和金额必须与原收款/本笔审核金额一致；带时区的转账时间不能晚于当前时间；方式枚举 `BANK_TRANSFER/WECHAT_TRANSFER/ALIPAY_TRANSFER/CASH/OTHER` 只表示人工转账方式，不触发对应网络退款。`proofReference`为必填1—128字的店铺凭证归档编号，拒绝URL/协议内容，不读取或访问第三方地址；`note`为最多500字补充说明，`verified`必须为true。凭证原件由店铺线下留存，**本片没有上传、存储或验证图片原件**。

核对 DTO 为 `reconciliationId/confirmationObjectId/confirmationRevision/preparedById/preparedByName/merchantAccountId/externalRefundNo/amountFen/refundedAt/refundMethod/proofReference/note/refundNo/originalTradeNo/intentStatus/authorizedAt/authorizedByName/outcome/createdAt/canConfirm`。有效outcome由已完成资金意图/异常和核对结果共同决定，恢复命令完成后不能继续显示待复核。资金提交前再验当前复核权限；不可改写资金字段、双人约束及“核对记录成功须对应成功退款意图”由数据库保护。

PC路径为 `/aftersales`、`/aftersales/{caseId}`，由订单列表/详情和后台导航进入；权限组表单可分别授予上述四项售后/退款权限，预设组未自动扩权。登记或确认遇未知结果时保持原内容；确认网络错误/5xx必须成功查询最新状态后才允许继续，已知字段/权限错误保留说明以便修正。小程序从订单详情进入 `/pages/aftersales/index?orderId=...` 和 `/pages/aftersales/detail?id=...`，先预览再申请，网络不确定时沿原body/key恢复；会员切换或卸载后旧响应不显示私有资料，旧401也不能清除新会员令牌。核销详情展示可用量和售后占用，不能将尚未永久消耗的 `remainingQuantity` 误标为可核销量。

申请图片上传需安全会员附件模块，按本片明确边界后置；当前申请使用文字说明、线下退款使用凭证归档编号。退货验收、微信退款、权益/运费退款、历史缺失规则快照处理以及真实商户/真机验收均不由D1代替。

### 5.16 D2 退货验收与微信退款适配

- 本人 `POST /api/v1/app/aftersales/{caseId}/return-shipment`：UUID `Idempotency-Key`；精确字段 `{expectedRevision,carrierName,trackingNo}`。只允许 `WAITING_RETURN`，同键同内容重放；更正产生新历史，不覆盖既有事实。退货地址须与店铺沟通，本片不虚构地址或自动预约寄件。
- `POST /api/v1/admin/aftersales/{caseId}/return-acceptance/preview` 预览；同路径去掉 `/preview` 保存。精确字段 `{expectedRevision,mode,receivedQuantity,salableQuantity,refundQuantity,refundAmountFen,reason}`；`mode=RECEIVED/WAIVED_RETURN`，金额为整数分或 `null`（由服务端计算），不得高于快照范围；免寄回的收货/可售数量为0，原因5—500字。保存需 `aftersale.read`、`aftersale.return.accept`、UUID防重复键及绑定case/修订/会话的密码二次确认。
- 原申请数量/金额不可改写；验收一笔形成最终结论，详情另返回 `returnShipment`、`returnAcceptance`、`effectiveRefundQuantity/effectiveRefundAmountFen`。部分批准释放差额占用，零批准结束为拒绝；验收可售数量在验收事务回库，不可售数量只保存处置事实。免寄回无库存回补。资金成功后使用有效批准值，不再次回补已验收库存。
- 微信原路退款发起 `POST /api/v1/admin/aftersales/{caseId}/wechat-refund`，body `{expectedRevision}`、UUID键及动作 `refund.wechat.dispatch` 的密码二次确认，权限 `aftersale.read/refund.prepare`；查单 `POST .../wechat-refund/query` body `{}`。原收款与商品退款金额只取服务器快照，客户端不能提交商户、交易号或可信退款凭证。
- 微信退款请求/响应RSA签名验签、退款通知AES-GCM解密与身份/币种/原支付总额/退款金额核对沿共同资金入口；受理不等于成功，失败仍沿原退款业务号，未知/处理中先查单，已有资金凭证只恢复本地结算。实际商户网络默认关闭且配置缺失不请求第三方。
- 图片/凭证原件上传、退货物流实时轨迹及上门取件、真实微信退款联调、权益/运费与零现金退款、正式补偿调度、真机验收继续分别记录后置状态。

退款回调为 `POST /api/v1/payments/wechat/refund-notify`；只认原始报文验签解密的 `REFUND.SUCCESS/CLOSED/ABNORMAL`，非成功事件单独去重。相同事件身份冲突永久记录双方待人工核查，渠道入口与共同退款恢复均拒绝自动结算。申请响应即使声称SUCCESS，也先按受理处理，最终通过通知或主动查单确认；通知金额对象按官方字段可缺currency，但如带必须CNY，申请/查询响应必须CNY。接口字段依据[微信退款申请](https://pay.wechatpay.cn/doc/v3/merchant/4012791862)、[退款查询](https://pay.wechatpay.cn/doc/v3/merchant/4012791884)、[小程序退款通知](https://pay.wechatpay.cn/doc/v3/merchant/4012791906)。

`manage.py reconcile_wechat_refunds --batch-size 100` 每批1—500，只查询PROCESSING/UNKNOWN，不主动退款；未启用/缺配置跳过且无网络请求，实际单项失败在输出计数后非零退出。`WECHAT_REFUND_ENABLED=0` 默认为关闭，`WECHAT_REFUND_NOTIFY_URL` 由部署持有人配置HTTPS退款回调；不复用支付回调地址。正式持续补偿调度尚未部署。

### 5.17 D3：权益、运费与零现金结算

- 新订单在成交明细及权益占用建立后保存 `benefit_order_snapshot`：积分发放金额单位/积分数、有效天数、过期返还天数、等级门槛、订单项抵扣与现金快照、券原有效期。历史订单缺该快照返回 `SKIPPED/MISSING_HISTORICAL_SNAPSHOT`，不套用当前规则补发。
- 订单域 `orders.benefit_lifecycle.reconcile_benefits_locked` 协调履约完成事实、售后累计商品退款、已成功运费退款，调用 `benefits.lifecycle` 与 `customers.consumption` 的命名入口。先锁 Order，再 Member；涉及库存先锁stock，然后券、积分账户与批次；网络不进入结算事务。
- 所有订单项履约或售后结束且无进行中售后后，按最终有效现金实付累计消费并发消费积分。核销撤销同步撤回消费与奖励，重新完成只补累计目标差额。退款返还抵扣积分按订单项原分摊及累计现金退款比例；零现金项按累计批准退款数量比例，末笔补齐尾差。原批次未过期沿原有效期，已过期使用成交规则快照中的短期有效期。奖励扣回可形成负余额，后续取得积分先抵债，抵债部分不得再次到期扣减。
- 运费与商品金额分离：所有快递项未发货且全部获准退款时，最后一项批准领取唯一整单运费退款占用。金额冻结后不可改写；退款请求使用商品与运费合计，仍核对原渠道收款身份、总实付及共同成功凭证。已发货或部分退款不自动退运费。优惠券只在整单全部商品数量和现金均退完后、原有效期仍有效时恢复；部分退款不返券。
- `POST /api/v1/admin/aftersales/{caseId}/settle-benefits`：body `{expectedRevision}`，UUID `Idempotency-Key`；需 `aftersale.read`、`refund.prepare` 及密码确认动作 `refund.benefits.settle`，objectId 为 caseId、revision 为当前修订号。仅批准且现金合计为零的售后可处理，建立独立不可改写权益完成凭证；不创建 `RefundIntent` 或 `RefundEvidence`。含商品现金或运费现金必须沿原资金路径确认。重复同键同内容返回已完成结果；同键不同内容冲突。
- 售后 DTO 的 `effectiveRefundAmountFen` 继续表示批准商品金额；新增 `goodsRefundAmountFen`、`shippingRefundAmountFen`、`totalRefundAmountFen`、`canSettleBenefits`。`refundSource.amountFen` 是资金核对合计。订单及售后详情的 `orderBenefits` 是**整单累计权益结果**：`status` (`PENDING/SETTLED/SKIPPED`)、`effectiveSpendFen`、`earnedPoints`、`returnedPoints`、`clawedBackPoints`、`couponRestored`、`policyRevision`；历史缺快照只返回跳过原因。不得把累计数据当成本次退款增量。
- PC 使用资金合计录入/核对，零现金页面明确没有现金转出；未知结果先 GET 售后最终状态，再沿保存的原 body/key 重试。会员页面分别显示商品、运费与合计，不以售后批准替代退款成功。
- `reconcile_order_benefits --batch-size 1..500 --after-order-id UUID` 仅恢复有规则快照的已付款订单本地权益，返回 `nextCursor`，失败非零退出；不触发渠道退款。`expire_points --limit 1..500` 处理到期可用批次，保留已有冻结事实。正式调度与部署另验收。积分及等级规则配置此片提供受信任领域入口，营销运营配置页面、纯积分商城另片交付。

自然到期消费奖励的退款扣回细则已由用户确认：已经自然到期并已扣账部分不再重复扣减，未到期且已使用部分可形成负余额；抵扣积分返还时原已过期部分按配置给予短期有效期。此业务口径已收口，真实账号、真机与部署验收仍分别后置。


### 5.18 D4.0：会员、等级与积分运营

本片开放会员权益查询和等级／积分规则配置；优惠券活动运营、手工积分调整／赠送界面、纯积分兑换商品与订单分别留后续切片。公开下单、真实微信收退款与物流网络继续关闭。

| 接口 | 权限与用途 |
| --- | --- |
| `GET /api/v1/admin/members` | `member.read`；分页、会员UUID片段、当前等级、启用状态查询。 |
| `GET /api/v1/admin/members/{memberId}` | `member.read`；当前等级、有效消费、积分账户摘要。 |
| `GET /api/v1/admin/members/{memberId}/points` | `member.read`；积分明细分页。 |
| `GET /api/v1/admin/members/{memberId}/consumption` | `member.read`；消费、评级变化与公开原因分页。 |
| `GET /api/v1/admin/member-rules` | `member.rules.read` 或 `member.rules.manage`；当前规则。 |
| `PUT /api/v1/admin/member-rules` | `member.rules.manage` + CSRF + 密码确认；修订、幂等与审计。 |
| `GET /api/v1/app/member/overview` | 本人Bearer；权益摘要、当前规则及已启用等级规则修订。 |
| `GET /api/v1/app/member/points`、`/consumption` | 本人Bearer；本人明细分页，禁止传其他会员标识。 |

列表统一 `items` + `pagination:{page,pageSize,total}`，默认20、最大100、页码1—100000。会员列表另有安全 `grades:[{id,code,name,rank}]` 过滤项。`search` 最大64字且只搜索会员UUID，`enabled=true|false`，禁止重复和未知查询参数。会员不返回微信openid/appid、登录令牌或内部发放引用；私密响应 `Cache-Control:private,no-store`，本人接口 `Vary:Authorization`。

会员摘要包括 `effectiveSpendFen`、`gradeEffectiveAt`、`gradePolicyRevision`，积分 `settledPoints/frozenPoints/availablePoints/debtPoints/expiredPendingPoints/expiringPoints/nextExpiryAt`。自然到期但尚未扣账的积分不计可用；欠额不与冻结混为一项。积分旧事件与权益生命周期流水合并查询，EARN／RETURN对应的GRANT仅显示一次。冻结／释放标记 `effect=FREEZE|RELEASE`，其余 `BALANCE`；旧记录无余额快照时明确返回null。`sourceRef` 为公开原因枚举：`ORDER_COMPLETED/REFUND_COMPLETED/FULFILLMENT_REVERSED/POINTS_EXPIRED` 或安全的既有类型／`ORDER_SETTLEMENT`，不返回内部原因原串。

规则响应为 `revision`、`grades:[{id,code,name,rank,minimumSpendFen}]` 与 `points:{earnUnitFen,earnPoints,deductPoints,deductFen,maxPercent,validDays,refundValidDays}`。更新body严格为 `expectedRevision`、完整points、所有启用等级 `{id,minimumSpendFen}` 及1—200字reason。首级0、门槛严格递增；积分单位参数1—1000000、抵扣上限1—100%、期限1—3650天，金额整数分。确认动作 `member.rules.update`，对象 `member-rules`、修订绑定，使用 `Idempotency-Key` UUID及 `X-Action-Confirmation`。同账号同键同内容返回原保存结果，不重复修订／审计；不同内容冲突，未知结果先GET再按原body/key恢复。

报价和订单记录完整规则快照；提交时重新校验并锁定政策，变化返回 `BENEFIT_POLICY_CHANGED` 409，客户端重新报价。抵扣钱数 `floor(pointsToUse × deductFen / deductPoints)`，0分抵扣拒绝消费积分；订单项退款的积分数量按实际核销积分分摊，不能拿抵扣分金额充当积分。订单政策不可改写，数据库核对政策换算并拒绝JSON null。

等级规则修改不批量重写既有会员或成交价。新规则订单全部完成且无进行中售后时启用较新门槛，零现金完成也可启用；会员保存已启用最高修订，迟到旧单／旧单退款调整累计有效消费时沿用该修订，避免重新启用旧门槛。待付款／未完成订单不启用；消费积分金额与期限仍按各订单原成交快照处理。

### 5.19 D4.1：优惠券活动、发放与本人领取

活动状态为 `DRAFT/PUBLISHED/LEGACY`。草稿完整校验后保存，发布冻结编码、名称、券型、金额、适用商品、核销资格、有效期、总量、个人领取上限及领取方式。暂停／恢复仅改变 `issuanceEnabled` 和修订，停止／恢复新增领取与后台发放，不改变已发券条款与结算资格。历史活动回填 `LEGACY`，保留既有券使用、占用、取消释放及退款恢复，不开放新领取或编辑。新业务活动必须经草稿／发布入口建立。

| 接口 | 权限与用途 |
| --- | --- |
| `GET/POST /api/v1/admin/coupon-campaigns` | 查询需 `coupon.read`；新建另需 `coupon.manage`。 |
| `GET/PUT /api/v1/admin/coupon-campaigns/{id}` | 查询需read；完整草稿修改另需manage、`expectedRevision`。 |
| `GET /api/v1/admin/coupon-product-options` | read + manage；按商品分页，避免多个SKU造成选择重复。 |
| `POST /api/v1/admin/coupon-campaigns/{id}/publish`、`/distribution` | read + `coupon.publish`、CSRF、密码确认动作 `coupon.publish`；对象活动UUID、当前修订绑定。 |
| `GET/POST /api/v1/admin/coupon-campaigns/{id}/issuances` | 查询需read；发放另需 `coupon.issue/member.read`、密码确认动作 `coupon.issue`。 |
| `GET /api/v1/admin/coupon-operations/{key}` | 当前账号的原操作结果，复查原动作权限；重复发放另复查 `coupon.issue.repeat`。 |
| `GET /api/v1/app/coupon-campaigns`、`POST /{id}/claim` | 本人Bearer；当前开放有效活动、本人领取次数、领取。 |
| `GET /api/v1/app/member/coupons`、`/coupon-claims/{key}` | 本人券分页及本人原领取结果，不接受别人的会员标识。 |

后台写入统一严格JSON、UUID `Idempotency-Key`、当前权限和CSRF；发布、开关、发放须 `X-Action-Confirmation`。草稿完整字段为 `code/title/kind/minGoodsFen/discountFen/productIds/redeemEligible/validFrom/validUntil/totalQuantity/selfClaimLimit/claimMode/issuanceEnabled`，编辑另含 `expectedRevision`。编码ASCII字母数字下划线短横线1—64位，标题1—100字，时间带时区且结束晚于开始；商品最多100个不重复存在UUID，空表示全场；总量1—1000000，上限1—min(总量,100)，方式 `SELF/ADMIN/BOTH`。现金券门槛0，满减券门槛正数，金额整数分；核销商品默认不参与，需明确启用。

发布body `{expectedRevision}`；开关body `{expectedRevision,issuanceEnabled}`；发放body `{expectedRevision,memberId,quantity,reason}`，数量1—100、原因最多200字。后台默认1张；同活动同会员已有后台发放或本次数量大于1时，必须有重复权限及非空原因。后台发放占永久总量，不占个人SELF限额；用券、取消和返券都不恢复发放额度。只有有效且启用的当前应用会员可领取／受领。发放锁序为member→campaign→allocation；发布只锁campaign，无反向member锁。等待领域锁后重验当前会话、账号与权限；内部受信任发放入口也验证操作员当前能力。

活动DTO为 `id/code/title/kind/minGoodsFen/discountFen/productIds/productNames/redeemEligible/validFrom/validUntil/status/revision/totalQuantity/issuedQuantity/remainingQuantity/selfClaimLimit/claimMode/issuanceEnabled`，productNames为 `{id,name}` 数组。会员活动DTO去掉运营状态、编码、修订和内部计数，增加 `selfClaimedCount/canClaim`。本人券DTO为 `id/campaignId/title/kind/minGoodsFen/discountFen/productIds/productNames/redeemEligible/validFrom/validUntil/status/issuedAt/orderId`，不返回操作员、内部键、微信身份或其他会员资料。

列表统一 `items/pagination:{page,pageSize,total}`，默认20／最大100／页码最多100000，拒绝重复及未知查询参数。后台活动 `q` 最多100字、status按活动枚举；本人券status为 `ALL/AVAILABLE/RESERVED/USED/EXPIRED`。仅AVAILABLE按原截止时间及返券期限较早者判EXPIRED；USED与RESERVED保留真实使用／订单占用事实。未到开始时间的AVAILABLE仍显示有效起止，不代表当前可以结算。

领取body `{}`，完成结果 `{campaignId,couponIds,quantity:1}`；后台发放另含memberId。恢复接口返回 `{status:'COMPLETED',result:原结果}` 或 `{status:'NOT_FOUND'}`。NOT_FOUND不证明无在途请求，只允许原键原内容重试；状态／结果畸形、408／429、网络中断均保留未知状态。PC按操作员、目标保存原键与body，小程序按本人保存领取键；成功及恢复响应核对身份后清理，账号切换不重放旧会员操作，存储失败阻止提交。历史凭证、券身份和发放关系不可改写，数据库延迟约束核对额度、会员计数、唯一券ID与实际券数量。私密成功／失败响应均no-store，Vary Authorization与Cookie。真实容量、正式部署限流及真机验收另记录。

### 5.20 D5：纯积分兑换

兑换商品关联既有SKU，独立设置正整数积分售价和 `DRAFT/ON_SALE/OFF_SALE` 状态；现金售价、会员等级折扣与现金抵扣比例不参与计算。两种销售方式共享SKU库存，兑换发布仍要求已发布商品内容、有效分类、主图、当前单位及未到期的核销截止日。兑换上下架独立于现金销售开关。

本片一次兑换一个商品，数量1—99；积分售价1—1000000。快递兑换积分售价包含配送成本，订单包邮，不再收现金或积分运费。核销兑换使用商品固定截止日。订单不使用优惠券、不增加有效消费或消费积分、不激活等级规则。

| 接口 | 权限与用途 |
| --- | --- |
| `GET/POST /api/v1/admin/exchange-offers` | 查询需 `exchange.read`；创建另需 `exchange.manage`。 |
| `GET/PUT /api/v1/admin/exchange-offers/{id}` | 查询及修改独立积分售价，修改需read/manage与 `expectedRevision`。 |
| `POST /api/v1/admin/exchange-offers/{id}/availability` | read/publish、CSRF及密码确认动作 `exchange.publish`，绑定兑换商品UUID和当前修订。 |
| `GET /api/v1/admin/exchange-sku-options` | read/manage；既有SKU分页选择及结构可用状态。 |
| `GET /api/v1/admin/exchange-operations/{key}` | 当前操作员原请求的结果恢复，重验当前动作权限。 |
| `GET /api/v1/app/exchange-products`、`/{id}` | 公开兑换商品列表及详情。 |
| `POST /api/v1/app/exchange-quotes` | 本人Bearer，输入 `{offerId,quantity,addressId?}`；快递须本人有效地址。 |
| `POST /api/v1/app/exchange-orders` | 本人Bearer、UUID `Idempotency-Key`，仅输入 `{quoteId}`。 |
| `GET /api/v1/app/exchange-operations/{key}` | 本人原兑换结果恢复；无结果仅允许原键原内容重试。 |

后台创建输入 `{skuId,pointsPrice}`，修改 `{expectedRevision,pointsPrice}`，开关 `{expectedRevision,status:'ON_SALE'|'OFF_SALE'}`。写入使用UUID幂等键；恢复响应为 `{status:'COMPLETED',result:原兑换商品DTO或订单DTO}` 或 `{status:'NOT_FOUND'}`。未知状态保留原键、内容及主体，账号切换不自动重放；持久存储失败阻止提交。

兑换报价有效10分钟，记录不可改写快照与摘要。提交重验当前应用会员、商品修订、积分售价、单位、地址、截止日、库存及可用积分；库存和积分占用、正式扣除、已付款订单及核销凭证在同一事务完成。失败整笔回滚；同键同内容恢复原结果，同键不同内容拒绝。

兑换订单为 `orderKind:'POINTS'`、`paymentMethod:'POINTS'`，现金金额及现金优惠全部为0，`exchangePoints` 为成交积分，订单项 `pointsUnitPrice/pointsTotal` 为独立成交快照。不建立现金到账凭证、不请求微信支付。履约复用现有已付款订单入口，发货及核销不再扣库存。未履约的取消通过售后申请与审核，履约后使用普通售后规则；授权零现金权益结算退回对应积分，并记录批次、有效期及累计返还事实，不创建现金退款意图。

`EXCHANGE_ORDER_ENABLED` 默认0，独立于始终关闭的公开现金方式。隔离库的合成积分体验可单独开启；本机测试和模拟器不代表真实账号、真机、部署或公开营业验收。

D5列表默认20、最大100、页码最多10000。报价返回quoteId、expiresAt、ready、exchangeOrderAvailable及阻塞原因；订单含orderKind、exchangePoints与逐项pointsUnitPrice／pointsTotal。售后使用requestedRefundPoints、effectiveRefundPoints、pointsToReturn、refundablePoints及returnedPoints，分别区分申请、批准、待返与已返事实。原成交数量不改写；发货界面按原数量减已完成退款数量显示实际待发数量，进行中售后占用不等于已退款。

### 5.21 E0：素材存储、中心与引用选择

`GET /api/v1/admin/assets` 返回 `{items,total,page,pageSize}`，默认20、最大100，页码1—100000。`asset.read` 查看全部；仅有 `asset.upload` 时只能查询本人未绑定素材。筛选 `kind=IMAGE|VIDEO|GIF`、`q`（文件名最多120字）、`binding=BOUND|UNBOUND`、`square=1`（静态方形图片）。素材DTO补 `originalName/sha256/createdAt/bindingStatus/availability`，可用状态为 `READY/MISSING/EXPIRED`；不回显物理路径、上传账号或凭据。筛选结果只供选择，保存时重新校验文件、期限与权限。

`GET /api/v1/admin/assets/{id}/references` 需 `asset.read`，同样分页；返回引用的 `domain/objectId/label/role/version/state`，状态为 `BOUND/DRAFT/CURRENT/HISTORY`。商品主图、附图、视频，页面草稿、所有发布版本（含隐藏组件），启动草稿及所有版本都是保留引用。缺少对应 `catalog.read/page.read/startup.read` 时名称脱敏、objectId为null；历史保留不赋予公开访问权限。积分商品复用原商品素材引用。

`DELETE /api/v1/admin/assets/{id}` 需 `asset.delete` 与CSRF，锁后重验实时身份与权限。存在任何保留引用返回409 `MEDIA_REFERENCED`，不存在返回404，成功返回 `{deleted:true}`。删除数据库记录与审计同事务；文件只在提交后清理，文件清理失败保留持久化意图待恢复，已删除素材不能再由API读取。客户端需确认并在失败时保留当前结果、允许重试，不根据点击提前移除素材。

商品、页面和启动保存／发布均按稳定UUID顺序锁定素材并重新校验文件可用性；未绑定超过24小时的素材不能新增绑定或发布。新增引用要求 `asset.read`，或 `asset.upload` 且素材为本人未绑定上传；本对象已有引用可以保留。业务写权限与素材权限分别校验，等待锁后重新校验会话与业务动作权限，不能由客户端隐藏按钮替代。被历史引用的素材不适用未绑定24小时清理。

文件底层为 `catalog.storage.LocalStorage`。生产必须显式配置 `MALL_MEDIA_ROOT` 到持久卷或受控持久目录，默认不提供目录级静态公开服务；素材公开仍经既有当前业务可见接口。新素材键为 `media/product/...` 或 `media/startup/...`，兼容旧 `product/startup/page` 键；拒绝绝对路径、上级穿越、反斜杠及键内符号链接。新目录0700、文件0600；临时文件写入并同步后同卷原子安装，不覆盖已有文件。`code/` 预留不可变产物边界（默认200 MiB上限），`export/` 预留私有下载与24小时保留（默认100 MiB上限）；E0仅定义策略，不开放E3构建或E4导出接口。

`.pending-delete/` 是与媒体同卷的持久化上传／删除意图，`tmp/` 是临时上传区。数据库／审计失败不产生可用假记录；外层事务回滚后保留的意图由恢复根据数据库事实处理，不误删仍有记录的文件。磁盘故障返回503 `MEDIA_STORAGE_UNAVAILABLE`。`recover_media_storage` 恢复意图及过期临时文件，`purge_orphan_media` 先恢复意图再清理过期未绑定素材；部署应定期执行恢复／清理并监控失败日志，调度和容量压测留E5。不得把整个media根目录作为公开目录，也不得把代码产物、导出或意图目录交给素材清理器。

`compose.storage-check.yaml` 为无网络的隔离命名卷验证：首次 `docker compose -f compose.storage-check.yaml run --rm -e PROBE_MODE=write probe` 写入合成文件，随后 `docker compose -f compose.storage-check.yaml run --rm probe` 用新容器读出并核对摘要。已存在卷重复写入会拒绝覆盖；这只证明本机命名卷重建持久性，完整应用编排、备份、升级恢复及RPO/RTO仍属于E5。


### 5.22 E1：页面与启动配置历史、原子回退

页面域提供首页、独立微页面和启动配置历史。`GET /api/v1/admin/pages/home/versions`、`GET /api/v1/admin/pages/{pageId}/versions`、`GET /api/v1/admin/startup/versions` 默认20、最大100分页，`page` 为1—100000；页面需 `page.read`，启动需 `startup.read`。返回 `{list,total,page,pageSize,currentVersionId,publicationRevision,draftRevision}`，条目包含 `versionId/revision/name/publishedAt/publishedBy/isCurrent`；按配置修订及UUID倒序稳定排序，操作者只返回展示名，不返回账号凭据。相同路径追加 `/{versionId}` 查看快照，页面附 `config`，启动附管理端 `gifUrl/fallbackUrl`。页面版本必须属于URL指定页面，历史可看不等于当前可发布。私有响应禁止缓存。

三个领域路径下的 `POST /rollback` 精确接收 `{expectedRevision,expectedPublicationRevision,versionId,reason}`；草稿修订为正整数、发布修订为非负整数，原因去首尾空白后1—200字，需CSRF、8—128位可打印ASCII `Idempotency-Key` 及一次性密码确认凭证。页面需 `page.read`＋`page.publish`，启动需 `startup.read`＋`startup.publish`。确认动作 `page.rollback/startup.rollback` 复用对应发布权限，绑定对象 `{pageUUID}:{versionUUID}`／`startup:{versionUUID}`，确认修订是 `expectedPublicationRevision`，防止确认后换目标。

回退只切换当前发布版本指针，保留草稿内容、名称、修订和素材引用；不产生重复配置快照，不切换小程序代码版本。独立 `publicationRevision` 初始0，存量已发布记录回填1，每次正常发布／回退递增；E1起普通发布也精确接收 `{expectedRevision,expectedPublicationRevision}`，旧发布客户端须升级；升级前单修订成功请求的旧摘要与E1双修订摘要不同，旧键返回409，升级时须先核对当前线上状态和原发布版本，再以新键执行明确的新操作，不能把旧键冲突当未知请求自动重试；与 `expectedRevision` 草稿修订同时核对，防止A→B→A后的旧请求误覆盖。草稿DTO补 `publishedVersionId/publicationRevision`。已生成过版本的同一草稿修订不能再次创建快照，可通过历史切换该版本，或先修改草稿再发布。

页面发布与回退共用事务级页面图锁，随后锁页面、发布指针及按UUID排序的素材；启动锁配置与发布指针。锁后重新验证会话和业务权限，成功重放也须实时授权。新回退重新校验所有保留素材文件、可见组件图片类型和链接的当前商品／分类／页面状态、自链及循环；启动重验GIF和静态兜底图。历史版本已有合法素材归属，回退不要求任意新素材绑定权限。缺文件、无效目标、修订冲突或审计／存储失败均保留原线上成功版本、发布修订和草稿。

成功操作保存不可改写的请求凭证、目标、修订、原因和审计；同键同内容返回原结果，同键异内容409。重放返回原操作结果，不再次切指针；原结果可能已被后续操作取代，客户端需读取当前历史状态。未知网络结果保留原键和原body，仅允许恢复同一请求，不能改选版本或生成新键盲重试。回退当前版本拒绝，不无意义增加发布修订。

数据库禁止UPDATE／DELETE历史配置、历史素材关系和成功操作凭证；页面指针只允许指向本页版本，发布指针不可删除重建计数，已发布指针不可置空。素材关系的INSERT只由当前发布路径建立，本片不声称数据库独立解析全部配置并核对关系集合。`page.read/startup.read` 可读取所属域历史保留素材，公开访问仍只跟随当前可见发布引用；历史引用继续阻止素材清理。PC共用历史抽屉，覆盖加载、空、错误重试、分页、详情、当前标签、原因／密码确认及只读状态；回退后更新线上信息并保留编辑器未保存内容。底部导航和客服悬浮配置另属E1.1。

### 5.23 E1.1：底部导航与客服悬浮配置

导航与客服是两个独立单例领域，分别维护草稿修订、线上发布修订、不可变版本、历史素材引用和幂等操作凭证；一个领域回退不改动另一领域。导航始终为 `HOME 首页`、`CATEGORY 分类`、`CART 购物车`、`ME 我的` 四项，顺序、名称和目标页不可修改。每项可选择一对普通／选中静态图标；未设置时小程序使用随包默认图标，不产生空白导航。客服可关闭或开启；开启时只允许 `PHONE`（ASCII数字构成的有效11位大陆手机号）与 `QR`（一张静态二维码图片）二选一，可设不超过20字的入口文案及可选图标。关闭配置发布后公开接口不返回联系方式与私有素材 URL；草稿及历史仍保留，以便后续编辑或授权回退。仅接受已存在、可绑定、文件可读取且实际格式、尺寸、字节数与SHA256元信息一致的静态 PNG/JPEG 素材，预览、发布和回退均重验。

管理接口为 `GET/PUT /api/v1/admin/navigation/draft` 和 `/api/v1/admin/customer-service/draft`，PUT 精确提交 `{expectedRevision,config}`；`POST .../preview` 精确提交 `{expectedRevision}`；`POST .../publish` 精确提交 `{expectedRevision,expectedPublicationRevision}`，需 `Idempotency-Key` 与 `X-Action-Confirmation`；`GET .../versions` 支持与5.22相同的分页，`GET .../versions/{versionId}` 查看本域快照；`POST .../rollback` 精确提交 `{expectedRevision,expectedPublicationRevision,versionId,reason}`，确认与幂等约束同5.22。草稿包含 `revision/config/publishedRevision/publishedVersionId/publicationRevision`，成功发布／回退返回 `versionId/revision/publicationRevision/draftRevision`。确认动作是 `navigation.publish/rollback`、`customer_service.publish/rollback`，发布绑定对应领域对象与草稿修订；回退绑定 `领域:目标版本UUID` 与当前发布修订。新草稿、预览、历史与回退分别要求本域 `read/edit/publish` 权限，等待行锁后及成功重放前再次核对实时会话与授权；私有响应不可缓存。`member_marketing` 默认含两个领域的 read/edit，不含 publish，主账号可发布，其他账号须显式授权。

公开 `GET /api/v1/app/storefront` 返回 `{navigation:{versionId,items:[{key,label,iconUrl,selectedIconUrl}]},customerService:{versionId,enabled,mode,phone,qrUrl,prompt,iconUrl}}`，只读取两个当前成功发布指针；未发布时固定四项默认图标、客服关闭。小程序在首页、分类、购物车、会员主入口渲染共用四项底栏，详情页保留原底部购买操作；首页、分类、会员及商品／微页面详情按已发布配置显示悬浮客服，并避开固定底栏和购买操作。客服电话由用户点击后触发拨号，二维码由用户点击后预览；读取失败退回固定底栏并隐藏客服。分类带搜索词／分类ID的现有深链仍由原页面承接，底栏切换固定一级页面。

数据库触发器禁止历史版本、历史素材关系和成功操作凭证的 UPDATE／DELETE，校验发布指针的领域归属、修订逐次加一及已发布指针不可置空／删除。历史素材关系 INSERT 仍由受控发布路径建立；数据库本身不解析配置 JSON 来证明关系集合完整。发布或回退先锁领域配置与发布指针，再按 UUID 顺序锁素材并重新检查文件及权限；失败保留原线上版本和草稿。同键同请求返回原回执，不再次切换；旧回执可能已被后续操作取代，客户端必须重读当前线上。PC在不明网络结果下保存原请求键和原正文，只恢复原请求；成功后重读线上指针且保留未保存编辑。公开素材权限只跟随当前可见版本，草稿与历史引用继续阻止误清理。

### 5.24 E2.0：订阅消息契约与持久化任务基础

E2.0先定义三类**候选**事实事件：`ORDER_PAID` 只表示现金订单的可信收款凭证已成功结算，纯积分兑换和仅记录到账但尚未结算均不属于此事件；`ORDER_SHIPPED` 只表示首次创建发货凭证，运单更正不重发；`REFUND_SUCCEEDED` 只表示资金退款已结算成功，零现金权益返还、退款请求受理与未知结果均不属于此事件。事件和实际微信模板的映射、提醒文案及开放范围尚待业务与真实小程序主体核定，三类候选事件默认关闭。

业务域在完成上述事实的同一数据库事务内调用通知域 `record_event(event_type, source_id, member_id, occurred_at)`：来源ID分别为已应用的 `PaymentReceipt.id`、首次 `Shipment.id`、已成功的 `RefundIntent.id`。通知域持久保存事件来源、会员、模板／授权快照、任务状态与尝试记录；数据库以事件类型和来源事实防重复。调用方必须已有业务事务，回滚时任务一同回滚。没有启用且匹配会员小程序主体的模板、没有对应明确有效的用户授权、授权被拒绝或会员身份不匹配时，任务记录明确阻断原因，不投递，也不因之后补配置而追发旧事件。一次授权只可分配给一条任务，不能推断为永久授权。

授权观察使用受控 `observation_id` 唯一键，`record_grant` 对同键同内容返回原记录、异内容拒绝，避免一次回调重试增加可发送次数；E2.0仅允许合成证据登记，真实小程序授权采集与身份校验后续实现。多次独立有效的同模板接受授权可逐次分配给不同事件；追加接受不取消已分配的旧任务。明确拒绝形成新的授权纪元，阻断此前未发送的旧授权，并且不因拒绝记录的期限届满而使旧授权复活；只有拒绝后的新接受可供后续事件分配。会员的小程序主体与openid以摘要绑定授权证据和任务，发送前再次核对。

有界任务调度先在短事务内领取并提交租约，再于事务外调用渠道，最后凭租约令牌写入结果。尚未进入调用阶段的过期租约重新排队；一旦开始调用尝试，超时、进程中断或结果不明标记 `UNKNOWN`，不得自动重发。可证明渠道未接收的可重试失败才按有界退避计划重试。任务只记录受理或明确失败，不表示用户实际阅读、收到或交付成功。`run_subscription_jobs` 默认只恢复过期租约；合成发送必须在本机 `DEBUG` 环境显式加 `--synthetic`，并只处理 `SYNTHETIC_TEST` 授权证据。E2.0不提供管理端启用接口、小程序授权弹窗或真实微信发送适配；本机合成渠道的完成状态明确标为 `SIMULATED`，不能计作真实平台或真机接收证据。真实模板ID、授权范围、主体一致性、正式调度和微信端接收在后续切片分别验收。

### 5.25 E2.1：订阅模板草稿与授权可用性

没有实际小程序账号和平台模板时，PC 只保存三类候选事件的**未核验草稿**，不提供“启用发送”开关。草稿存于 `SubscriptionTemplateDraft`，与 E2.0 可发送的 `SubscriptionTemplate` 隔离；填写完整也只得到 `DRAFT_UNVERIFIED`，不会创建可发送绑定或用户授权。默认三行由迁移预置，事件固定为 `ORDER_PAID`、`ORDER_SHIPPED`、`REFUND_SUCCEEDED`。

| 接口 | 契约 |
| --- | --- |
| `GET /api/v1/admin/subscription-templates` | 需 `notification.read`；返回 `data:{events:[{eventType,revision,draftAppId,draftTemplateId,status,enabled:false}],sendingAvailable:false}`，三行固定顺序。 |
| `PUT /api/v1/admin/subscription-templates/{eventType}` | 需 `notification.manage` 与 CSRF；严格 body `{expectedRevision,draftAppId,draftTemplateId}`，成功返回单行 DTO。过期修订返回 409，未知事件返回 404；写入审计。 |
| `GET /api/v1/app/subscription-messages/availability` | 公开只读，固定返回 `data:{available:false,reasonCode:"PLATFORM_NOT_VERIFIED",events:[{eventType,available:false}]}`；不输出模板 ID。 |

草稿状态：两项均空为 `UNBOUND`，只填一项为 `INCOMPLETE`，两项均有值为 `DRAFT_UNVERIFIED`。标识允许 ASCII 字母、数字、下划线、短横线，AppID 最多 64 字符，模板 ID 最多 128 字符。`expectedRevision` 为非负整数；服务端在行锁内核对修订及当前账号权限，每次成功保存修订加一。当前账号过去一小时成功保存草稿达30次后返回429 `RATE_LIMITED` 与 `Retry-After`，不修改草稿；更广的公开接口流量治理属于部署层。PC 对未知保存结果先重新读取核实，不盲目再提交。只读权限不包含编辑；主账号拥有两项权限，子账号须分别授权。

真实授权采集仍未开放：没有可信的平台回调与可验证的小程序身份时，客户端自报“接受”不能写成可发送凭据。接入真实账号后须在用户触发场景下核定模板与主体、授权回调、openid 绑定、次数语义和拒绝处理，并分别完成平台与真机接收验收。此接口的“不可用”是平台授权入口的当前事实，不影响既有站内订单状态查询。

### 5.26 E2.2：消息任务查询与安全恢复

管理台通过独立的 `notification.read` 查询任务状态和发送尝试；恢复另需 `notification.recover`，不随模板草稿编辑权限自动授予。主账号拥有两项权限，子账号分别授权。响应不包含会员个人信息、微信 openid、身份摘要、模板 ID、授权凭据、租约令牌或异常原文。`SIMULATED` 仅表示本机合成通道运行完毕，不是微信接收；`UNKNOWN` 表示渠道调用可能已发生，必须先核验外部记录，不能在管理台盲目重发。

| 接口 | 契约 |
| --- | --- |
| `GET /api/v1/admin/subscription-message-tasks` | 需 `notification.read`；筛选 `eventType/status/createdFrom/createdTo`，默认近90天、跨度最大90天、每页默认20最多100；按创建时间与任务ID倒序，使用不透明 `cursor` 翻页。返回 `data:{items,nextCursor}`。 |
| `GET /api/v1/admin/subscription-message-tasks/{taskId}` | 需 `notification.read`；返回单任务与按次序排列的发送尝试。无权或不存在分别返回403／404。 |
| `POST /api/v1/admin/subscription-message-tasks/{taskId}/recover-reservation` | 需 `notification.read` 与 `notification.recover`、CSRF、密码二次确认；严格 body `{expectedUpdatedAt,expectedAttemptCount}`。确认动作 `notification.task.recover_reservation`，对象为任务 UUID，修订绑定尝试次数。仅行锁内重新确认状态仍为 `RESERVED`、租约已过期、**本次租约**没有发送尝试且提交快照未过期时，把任务恢复为 `READY`。以往已完成的可重试尝试不妨碍本次安全恢复。其余返回409要求重读；操作本身不发送消息。 |

任务摘要字段为 `taskId/eventType/status/reasonCode/attemptCount/occurredAt/createdAt/updatedAt/nextAttemptAt/leaseUntil/recoverable`；详情另有 `attempts:[{ordinal,outcome,failureCode,startedAt,finishedAt}]`。`recoverable` 是服务端按当前事实计算的提示，不代替 POST 中的行锁重验。筛选只接受固定事件和任务状态；日期可为 `YYYY-MM-DD` 或带时区 ISO 时间。只读和写入均使用当前会话鉴权、私有响应不缓存；写入在领域锁后复查当前权限并记录审计。

过期 `CLAIMED` 沿既有恢复作业转为 `UNKNOWN`，不得回到 `READY`。即使异常数据出现 `RESERVED` 的**当前租约**带发送尝试，也须保守地转为 `UNKNOWN`。重复或并发点击由任务锁与快照条件拒绝；网络结果未知时客户端先重新读取，不自动重复提交。正式微信模板、授权、发送、真实接收和部署调度仍另行验收。

### 5.27 E3.0：服务端代码源码快照与私有版本基础

部署流程完成数据库迁移后执行 `python backend/manage.py build_miniprogram_source`。命令从部署拥有的固定 `mini-program/` 源码目录生成确定性 ZIP：固定的根文件和 `assets/components/lib/pages` 目录、排序后的文件名、固定 ZIP 元数据；忽略测试、依赖、隐藏和私有配置，逐级拒绝符号链接与非法文件，受 `STORAGE_CODE_MAX_BYTES` 限制。整树二次读取摘要不一致时拒绝，部署流程仍须在源码更新完成后才启动命令。它是**源码快照**，不是微信编译包。来源身份以实际文件名和内容计算 SHA-256；本片不把未经证明的 Git 提交号写成可信来源修订。

包以 `0600` 权限先写入与持久化媒体根同卷的私有临时文件，完整同步后按摘要无覆盖安装到 `code/`；包和元信息摘要、文件数、字节数写入由数据库触发器禁止 UPDATE／DELETE 的 `CodeVersion`。重复构建同一源码验证既有文件摘要并复用版本，另产生一次构建任务记录；不同内容产生新版本。构建任务先持久化 `STARTED`，成功记 `SUCCEEDED`，可判定错误记 `FAILED` 和安全错误码；进程中断留下的任务在下次运行时转 `INTERRUPTED`。文件已安装而数据库失败时，按内容摘要保留私有文件供下次核验复用，不能由素材清理器删除。数据库完全不可用时命令非零退出；无失败行不能解释为同步成功。数据库版本的 `READY` 记录是构建成功事实；查询列表以文件存在和大小返回 `STORED_UNVERIFIED`，详情完成摘要复核后才返回当前 `READY`，缺失或不匹配返回 `UNAVAILABLE`。这些状态都不表示微信平台已收到。

| 接口 | 契约 |
| --- | --- |
| `GET /api/v1/admin/code-versions` | 需 `code.version.read`；按创建时间与 UUID 倒序，默认20、最多100条，不透明签名 `cursor` 翻页。返回 `data:{items,nextCursor}`。 |
| `GET /api/v1/admin/code-versions/{versionId}` | 需 `code.version.read`；返回该版本的非敏感元数据，无代码包下载或物理路径。 |
| `GET /api/v1/admin/code-sync-jobs` | 需 `code.version.read`；同样分页，返回最近构建任务及 `STARTED/SUCCEEDED/FAILED` 与安全失败代码。 |

版本 DTO 为 `versionId/versionLabel/sourceRevision/sourceDigest/packageSha256/packageBytes/fileCount/storageStatus/platformStatus/createdAt/completedAt/failureCode`；`sourceRevision` 在没有可验证提交来源时为 `null`，`platformStatus` 本片固定 `NOT_CONFIGURED`。任务 DTO 为 `taskId/versionId/status/failureCode/createdAt/completedAt`。管理台仅查询版本与任务，不提供浏览器上传、构建、预览、提审或发布按钮。接口使用当前后台会话与独立只读权限、私有响应禁止缓存；不返回本地路径、密钥、原始异常或包内容。平台凭据、真实构建／自动上传能力、预览／提审／发布及可信平台回执在后续 E3 切片和真实主体环境分别验收，任何本地 `READY` 都不可作为可提审或已发布状态。

### 5.28 E3.1：可信 Git 来源与部署自动同步

部署入口为 `scripts/deploy-with-code-sync.sh [后续启动命令及参数]`。受信部署控制器须在检出完整提交后，设置 `MALL_RELEASE_REVISION` 为该提交的完整 SHA；可用 `MALL_PYTHON` 指向部署 Python。入口先用不接受替换引用的 Git 核对 HEAD 和干净检出，**再**执行数据库迁移、`sync_deployed_miniprogram --expected-revision`、可选的后续启动命令。预检、迁移或同步任何一步失败均非零退出，后续命令不会启动；目前仓库尚无完整应用编排，部署系统须以此入口接线，E5 再验完整容器升级与恢复。`build_miniprogram_source` 仍保留为不声明 Git 来源的手动本地快照命令。

可信同步要求工作目录是干净的 Git 根目录，完整预期 SHA 与当前 `HEAD` 一致。源码包只从该提交中 `mini-program/` 的 Git blob 读取，禁用 Git 替换引用并剔除外部 `GIT_*` 环境覆盖，按 E3.0 相同规则生成确定性 ZIP；前后重验检出状态。缺失 Git、修订不匹配、脏检出、非法源码、包过大或存储／数据库失败均拒绝成功，并按安全失败码记录任务（数据库完全不可用时不能保证失败行）。Git SHA 证明包字节来自指定提交，不等于提交签名、部署主机可信或微信平台验收。

同内容重复同步复用不可变版本，但每次增加构建任务。成功时在同一事务内写入不可变的版本—提交来源证明；旧 E3.0 版本若与提交包摘要完全相同，也可新增证明而不改写旧版本。版本 DTO 的 `sourceRevision` 只从已验证证明读取，未证明仍为 `null`。任务 DTO 增加 `sourceRevision`：合法格式的预期提交 SHA 在任务开始时记录，失败任务仅表示请求目标、**不代表来源已验证**；成功任务才表示核验通过。同一源码对应多个提交时，各提交分别留证明，版本读模型展示最近一次通过验证的提交；任务展示本次提交。版本、证明及包不提供下载；`platformStatus` 仍为 `NOT_CONFIGURED`，不能据此提审或声称已发布。

### 5.29 E4.0：审计筛选与经营统计口径读模型

| 接口 | 权限与返回 |
| --- | --- |
| `GET /api/v1/admin/audit-logs` | `audit.read`。接受 `from/to`（上海时区日期，含首尾，默认近90个自然日）、`actorId/actionCode/objectType/objectId/result` 精确筛选，`limit` 默认20最多100及签名 `cursor`；返回 `data:{items,nextCursor,from,to,timeZone}`。时间、UUID倒序游标绑定筛选，最长1小时；改筛选须从第一页查起。现有仅返回数组的旧接口同址升级，PC 同步更新。 |
| `GET /api/v1/admin/business-summary` | 独立权限 `business.report.read`。接受 `from/to`，默认近90个自然日，最大90日；返回 `data:{from,to,timeZone,basis,totals,days}`，每天含 `date/paidOrderCount/paidAmountFen/pointsExchangeCount/refundCount/refundAmountFen/netAmountFen`，金额为整数分。 |

现金经营支付只取 `Order` 的 `order_kind=CASH,status=PAID,paid_at`，金额用订单 `payable_fen`；零元现金单计支付笔数、金额0。`POINTS` 已支付订单单列 `pointsExchangeCount`，不混入现金笔数和金额。退款只取 `RefundIntent.status=SUCCEEDED,succeeded_at`，金额用 `amount_fen`；一笔成功意图计一笔退款。分别按上海本地成功日期归属，半开时刻窗 `[起始日00:00,结束日后一天00:00)`；净成交为窗口支付减窗口退款，可为负。外部 `PaymentReceipt.paid_at` 和 `RefundEvidence.refunded_at` 不是本系统确认入账时间。三类聚合在同一 PostgreSQL 语句快照中执行，不以订单与退款连接造成重复计数。

审计列表保留可用的动作、对象和结果元信息；`before/after` 仅在读取时递归输出白名单枚举、数字和布尔值，其余字符串及敏感键遮盖，嵌套输出有深度与节点上限。底层审计证据不改写。两个接口均需实时后台会话鉴权，私有响应禁止缓存；按账号和自然分钟分别限制审计 60 次、经营 30 次，超限返回 429 与 `Retry-After`。限流仅写独立配额桶，不修改交易或审计事实；过期配额桶须在 E5 运维调度中清理。按权限异步导出、下载重验和24小时清理见5.30。

### 5.30 E4.1：审计与经营异步导出

| 接口／命令 | 契约 |
| --- | --- |
| `POST /api/v1/admin/exports` | JSON `{kind,filters,requestKey}`。`kind=AUDIT` 需 `audit.read` 与 `audit.export`，`BUSINESS` 需 `business.report.read` 与 `business.report.export`；`requestKey` 为客户端 UUID。同操作者同键同条件返回原任务，不同条件返回409。筛选按 E4.0 校验并冻结为明确的上海日期区间；只排入持久任务，不在 HTTP 请求中生成文件。 |
| `GET /api/v1/admin/exports`、`GET /exports/{taskId}` | 只显示本人且当前具备对应双权限的任务；列表支持 `kind` 与签名游标、默认20最多100项。DTO 包含 `taskId/requestKey/kind/filters/status/attemptCount/rowCount/fileBytes/failureCode/createdAt/completedAt/expiresAt`，不返回私有对象键。 |
| `GET /api/v1/admin/exports/{taskId}/download` | 本人任务、当前双权限、`READY`、24小时内才提供 CSV；读取固定任务 UUID 对象，校验常规文件、大小和 SHA-256，最终再次校验账号与任务状态。私有禁缓存、`attachment`、`nosniff`；跨账号404、未就绪409、过期410、文件不可用503。 |
| `manage.py run_export_jobs [--limit N] [--watch]`、`purge_expired_exports` | worker 用行锁与租约抢占任务，在独立进程生成 CSV，文件 `0600`、同卷原子安装并同步后才将任务标为 `READY`；失败保留安全失败码，过期租约最多重试3次。`--watch` 持续处理并执行24小时清理；独立清理命令可供调度／恢复使用。正式部署监督与定时调度在 E5 接线。 |

经营 CSV 复用 E4.0 的单语句聚合函数，按已完成的支付和退款事实生成每日行，金额保持整数分；审计 CSV 复用 E4.0 的出站脱敏投影，并将所有可能构成电子表格公式的文本转为安全文本。审计最多20000行，单文件受私有 `export/` 的100 MiB上限约束；总保留文件与在制预留默认不超过1 GiB。每轮先清理过期文件，再领取任务；生成前按全部活跃 worker 的单文件上限预留磁盘，并保留至少256 MiB空闲。每账号每小时最多10次新任务、同时最多3个待处理／生成任务；容量不足返回安全失败码。文件到期即拒绝下载，清理保留任务元数据和审计；文件删除失败可重试，强制中断残留的导出专用临时文件按租约辨认并清理，孤儿文件不能变成可下载成功任务。管理台从**已查询生效**的筛选条件创建任务，未知提交结果保留原请求号核对或同键重试。
