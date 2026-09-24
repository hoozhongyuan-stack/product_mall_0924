# B2C 商城 V1.0｜数据与 API 契约草案

状态：阶段 A 的分类、状态、编码与规格、素材上限、默认权限和主账号方案已获项目确认，2026-09-24；接口细节及阶段 B、C 交易契约仍是设计稿。本文是研发契约，不改变《B2C商城小程序V1.0需求文档.md》的业务规则。当前没有可运行工程、数据库迁移或联调结果。

## 1. 契约通则

- 服务端：Django 模块化单体，PostgreSQL；PC 后台：Vue 3、TypeScript、Vite、Element Plus、Vue Router；小程序：微信原生。PC 框架已由用户确认，具体依赖版本在建项时锁定。
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
| `category` | `id`、`parent_id`、`name`、`sort_order`、`status ACTIVE/INACTIVE`、`revision` | 最多二级；父级不能指向自身或二级分类。商品只绑定启用的二级分类；一级分类只用于导航。停用有在售商品的分类须先处理关联商品。 |
| `product` | `id`、`product_no`、`name`、`category_id`、`fulfillment_kind SHIP/REDEEM`、`status DRAFT/ON_SALE/OFF_SALE`、`description_content`、`revision` | 新建先保存草稿。`product_no` 全库大小写不重复，1—64 字符；名称最多 120 字符。富文本必须净化。商品、SKU 和分类状态共同决定可售性。 |
| `product_spec_axis`、`product_spec_option`、`sku_spec_selection` | 规格项名、排序；规格值、排序；SKU 与选中值 | 每商品最多 2 个规格项、每项最多 20 个值、组合最多 100 个。选中值必须属于该商品的规格项；同商品 SKU 组合由稳定 ID 生成唯一键，不用展示名称。 |
| `sku` | `id`、`product_id`、`sku_code`、`list_price_fen bigint`、`sale_status ON_SALE/OFF_SALE`、`unit_version_id`、`revision` | `sku_code` 在单商户内大小写不重复，1—64 字符；与商品编号、规格名值分列。`list_price_fen >= 0`。已有订单或库存流水的 SKU 编码及规格含义不直接覆盖。 |
| `member_grade` | `id`、`code`、`name`、`rank`、`enabled`、`revision` | 阶段 A 建立等级字典，供 SKU 等级价引用和展示；默认等级按需求文档，自动升降级及运营配置留待阶段 D。`code` 和 `rank` 各自唯一。 |
| `sku_grade_price` | `sku_id`、`grade_id`、`price_fen bigint`、`effective_at` | 同一 SKU、等级和生效版本不能重复。实际成交价由服务端根据会员等级选取，历史订单保留快照。 |
| `sku_unit_version` | `id`、`sku_id`、`base_unit`、`sale_unit`、`ratio_positive_int`、`effective_at` | 比例为正整数；启用新版本不改写历史订单和库存流水。阶段 A 建立版本结构，阶段 B 接入实际记账。 |
| `media_asset`、`product_media` | 素材 ID、存储键、MIME、字节数、宽高、摘要、状态；商品关联与排序 | 校验声明与实际文件类型、尺寸、用途和引用；商品最多 9 张图片与 1 条视频。本机可配置临时上传上限：图片 10 MB、GIF 5 MB、视频 50 MB；正式体积与加载目标经目标设备验证后定稿。存储键不等于公开 URL。 |
| `micro_page`、`page_config_version`、`page_publication` | 页面 `id`、类型 `HOME/MICRO`、名称、状态；版本 `id`、`page_id`、`revision`、`config_json jsonb`、草稿/已发布状态、发布人/时间；每页当前发布版本指针 | 首页是唯一的 `HOME` 页面，可关联已发布微页面。草稿可改；发布时从草稿生成不可变版本，并原子切换指针。校验组件字段、素材、链接目标发布状态和权限；失败保留旧版。首页主题三色属于版本配置。历史版本在阶段 A 保存，页面回退操作在阶段 E 交付。 |
| `startup_config_version`、`startup_publication` | 配置版本 `id`、`gif_asset_id`、`fallback_asset_id`、`revision`、状态、发布人/时间；当前发布版本指针 | GIF 与兜底图独立引用；发布时生成不可变版本并原子切换指针，失败保留旧素材。用户端只读已发布版本。3 秒倒计时从页面实际呈现后开始，分享/扫码目标由客户端恢复。 |

建议索引：`sku(product_id, sale_status)`、`product(category_id, status)`、`product_spec_selection(sku_id)`、`page_config_version(page_id, revision)`；唯一索引包括 `upper(product_no)`、`upper(sku_code)`、`member_grade(code)`、`(page_id, revision)`、`(product_id, spec_key)`，以及仅对 `kind='OWNER'` 生效的唯一约束。`product_no` 和 `sku_code` 只接受英文字母、数字、`-`、`_`；`spec_key` 由服务端按稳定规格项 ID 排序后连接所选规格值 ID，同商品内唯一，不用展示名称或顺序生成身份。无规格商品使用固定的单 SKU 键。具体索引 SQL 在首批迁移时核对。

公开读取仅把启用分类下、商品与 SKU 均为 `ON_SALE` 且后续具备真实可售库存的组合标为可购买。下架或不可售的旧链接返回明确状态与提示，不允许继续提交购买。

阶段 A 尚未实现库存和下单时，小程序商品接口必须返回明确的不可购买或库存未配置状态；不能用样板数字伪造可售库存或开放提交订单。

## 3. 阶段 A API 字段表

表中列出业务字段；通用响应包装、`requestId` 和审计规则按第 1 节执行。所有写接口服务端重新校验权限、类型、范围和资源状态。

| 方法与路径 | 请求或查询关键字段 | 响应关键字段与约束 |
|---|---|---|
| `POST /api/v1/admin/auth/login` | `loginName`、`password` | `accountId`、`displayName`、`permissionCodes`、会话状态；不返回密码哈希。失败次数与锁定时限在服务端执行。 |
| `GET /api/v1/admin/me`、`POST /api/v1/admin/auth/logout` | 会话 Cookie | 当前账号、权限；退出使会话失效。高影响操作仍重新确认当前会话。 |
| `GET/POST /api/v1/admin/accounts`、`PATCH /api/v1/admin/accounts/{id}` | 登录名、状态、权限组 ID、`expectedRevision` | 账号摘要；主账号和子账号权限边界在服务端执行，停用立即失效。凭据重置单独动作并审计。 |
| `GET/POST /api/v1/admin/permission-groups`、`PATCH /api/v1/admin/permission-groups/{id}` | 组名、`permissionCodes[]`、`expectedRevision` | 权限组及成员数；不能通过自改组提升自己的权限。 |
| `GET/POST /api/v1/admin/categories`、`PATCH /api/v1/admin/categories/{id}` | `parentId`、名称、排序、状态、`expectedRevision` | 二级分类树；删除或停用前检查商品引用。 |
| `GET /api/v1/admin/member-grades` | 无 | 返回阶段 A 的等级字典供 SKU 等级价编辑；阶段 D 再提供等级规则管理。 |
| `GET /api/v1/admin/sku-rows` | `keyword`、`categoryId`、状态、`page`、`pageSize`、排序 | 一行一个 SKU：`skuId`、`skuCode`、`productId`、`productNo`、`productName`、有序 `specs[{name,value}]`、日常价、等级价、状态。`rowKey=skuId`。 |
| `POST /api/v1/admin/products`、`GET/PATCH /api/v1/admin/products/{id}` | 商品基础字段、规格项和值、SKU 列表、媒体 ID、`expectedRevision` | 商品与 SKU 独立 ID/编码；服务端校验规格上限、组合唯一和历史不可改字段。 |
| `POST /api/v1/admin/products/batch-category/preview` | 当前页选中 `skuIds[]`、目标 `categoryId` | 服务端按商品去重，返回影响的商品、其他 SKU 数和不可操作原因，供确认弹窗展示。 |
| `POST /api/v1/admin/products/batch-category` | `skuIds[]`、目标 `categoryId`、各商品预期修订号、`Idempotency-Key`、已确认的影响摘要 | 逐商品重新校验，返回逐项成功/失败与原因；摘要或修订不匹配时要求重新预览。更改筛选或分页后客户端清空选中项。 |
| `PATCH /api/v1/admin/skus/{id}/status`、`PUT /api/v1/admin/skus/{id}/grade-prices` | SKU 状态或等级价数组、`expectedRevision` | 最新 SKU 修订号及审核信息；上下架作用于该 SKU，等级价在 SKU 维度。 |
| `POST /api/v1/admin/assets` | 文件及用途 `PRODUCT_IMAGE/PRODUCT_VIDEO/HOME/STARTUP_GIF/STARTUP_FALLBACK` | `assetId`、元信息、校验结果。上传方式在存储选型后细化；本机可采用服务端接收并写持久目录。 |
| `GET/POST /api/v1/admin/pages`、`GET/PUT /api/v1/admin/pages/{id}/draft` | 页面类型/名称；组件列表、顺序、显隐、内部链接；首页附三项主题颜色；`expectedRevision` | 独立微页面及首页草稿、修订号；写草稿不改变线上版本。首页关联只允许指向已发布微页面。 |
| `POST /api/v1/admin/pages/{id}/preview`、`POST /api/v1/admin/pages/{id}/publish` | `expectedRevision`、`Idempotency-Key`；发布需二次确认 | 预览返回校验后的配置；过期草稿返回 `REVISION_CONFLICT`；相同发布请求只生成一个不可变版本，失败保留旧版。页面回退 API 在阶段 E 实现。 |
| `GET/PUT /api/v1/admin/startup/draft`、`POST /api/v1/admin/startup/publish` | GIF 与静态兜底素材 ID、`expectedRevision`；发布含 `Idempotency-Key` | 草稿/发布版本与验证结果；重复请求只发布一次，过期草稿拒绝发布。正式素材取代示意 GIF 前不得标为上线素材。 |
| `GET /api/v1/app/bootstrap`、`GET /api/v1/app/home`、`GET /api/v1/app/pages/{id}` | 可选已见配置版本 | 只返回已发布首页、微页面、启动素材和版本号；草稿不得泄露，未发布目标返回不可用。 |
| `GET /api/v1/app/categories`、`GET /api/v1/app/products`、`GET /api/v1/app/products/{id}` | 分类、关键词、页码及 SKU 选择 | 商品、规格、适用价与可售状态由服务端返回。游客用日常价；阶段 B 接入真实可售库存前不允许购买。 |

建议错误码：`AUTH_REQUIRED`、`SESSION_EXPIRED`、`PERMISSION_DENIED`、`VALIDATION_FAILED`、`REVISION_CONFLICT`、`CATEGORY_IN_USE`、`SKU_CODE_DUPLICATE`、`SKU_SPEC_IMMUTABLE`、`MEDIA_INVALID`、`PUBLISH_TARGET_INVALID`、`STOCK_NOT_READY`、`RATE_LIMITED`、`INTERNAL_ERROR`。状态码分别使用 400/401/403/404/409/422/429/500；业务错误码是客户端稳定判断依据，文案可调整。

### 3.1 商品创建与修改结构

建议商品只能绑定二级分类。创建请求的业务结构如下；服务端生成的 ID、修订号和操作时间由响应返回，客户端不自行指定。

| 对象 | 请求字段 | 校验 |
|---|---|---|
| 商品 | `productNo`、`name`、`categoryId`、`fulfillmentKind`、`status`、`descriptionHtml`、`mainImageAssetId`、`galleryAssetIds[]`、可选 `videoAssetId` | 新建状态为 `DRAFT`；编号 1—64 字符且大小写不重复，名称最多 120 字符；分类为已启用二级分类；履约类型固定为发货或核销；富文本净化；主图计入图片总数，合计最多 9 张、视频最多 1 条。 |
| 规格项 | `specAxes[{clientKey,name,sortOrder,options:[{clientKey,value,sortOrder}]}]` | 每商品最多 2 项、每项最多 20 值；同项内值不重复，排序唯一。`clientKey` 只用于单次请求关联，不进入业务编号。 |
| SKU | `skus[{id?,skuCode,specOptionKeys[],listPriceFen,saleStatus,gradePrices:[{gradeId,priceFen}],unit:{baseUnit,saleUnit,ratio}}]` | 最多 100 个不同组合；规格值必须来自本商品；编码 1—64 字符且大小写不重复，金额非负、换算比为正整数；等级 ID 必须存在且启用。规格组合唯一键使用服务端稳定 ID，不信任客户端名称。 |

创建响应返回 `productId`、`productRevision`、每个 `clientKey` 对应的服务端规格 ID、每个 SKU 的 `skuId` 和 `skuRevision`。修改请求同时带 `expectedRevision`，已有 SKU 带 `id` 与 `expectedSkuRevision`；新增 SKU 无 `id`。若 SKU 已产生订单或库存流水，更改编码、规格选值或已生效单位版本必须被拒绝，并返回该 SKU 的具体错误。商品及其 SKU 的一次保存要么全部成功，要么全部失败。

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

链接结构为 `{type: PRODUCT|CATEGORY|PAGE|FUNCTION, targetId}`；不接受任意外链。`PAGE` 目标必须是另一已发布微页面，不能形成自引用或循环；商品和分类目标须符合上架/启用状态。发布请求先验证完整草稿与链接，再在一个事务中生成不可变版本、切换 `page_publication` 指针、写审计与待发送事件。`expectedRevision` 过期时拒绝；相同 `Idempotency-Key` 和相同内容只产生一个版本。页面回退沿用历史版本重新发布并留痕，操作接口在阶段 E 实现。

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
| `coupon_hold`、`points_hold`、`points_ledger` | 订单关联、暂占/冻结/核销/释放状态与数量 | 同一权益不能被两个有效订单重复占用；零元订单和取消/超时路径完整结算。 |
| `payment_attempt`、`payment_event`、`payment_anomaly` | 渠道、商户订单号、微信交易号、金额、状态；通知 ID、验签/处理结果；迟到账异常 | 商户订单号和外部交易号按适用范围唯一；重复通知不能重复扣款、库存或权益；关闭后到账不自动恢复。 |
| `idempotency_record`、`outbox_event` | 操作者/范围/请求键/内容摘要/业务结果；事件类型、业务 ID、投递状态与次数 | `(actor, scope, key)` 唯一；关键事件与业务写入同一事务，投递可重试。保留期在评审中定稿。 |

## 5. 交易关键 API 与并发结果

| 方法与路径 | 核心输入 | 结果与幂等约束 |
|---|---|---|
| `POST /api/v1/app/checkout/quotes` | SKU 与数量、地址、优惠券、积分抵扣选择 | 返回服务端计算的商品/优惠/积分分摊、实付金额、变化提示和报价标识；报价不锁库。 |
| `POST /api/v1/app/orders` | 报价标识、支付方式、用户确认的变化版本；`Idempotency-Key` 请求头 | 重新校验并原子占用全单库存/券/积分、创建订单与快照；应付大于零时为待付款，零元时在同一事务内直接确认付款、结算库存/券/积分并进入履约，且不调用支付渠道。任一失败全部回滚；相同键且相同内容返回同一结果，内容不同返回冲突。 |
| `GET /api/v1/app/orders/{id}` | 登录用户与订单 ID | 只返回本人订单；明确支付、履约、优惠及异常状态，客户端超时后以此查询最终结果。 |
| `POST /api/v1/app/orders/{id}/wechat-prepay` | 订单 ID、幂等键 | 仅固定微信支付且仍待付款的订单可发起；预支付受理不等于订单已付款。 |
| `POST /api/v1/callbacks/wechat/pay` | 微信原始通知与签名头 | 验签、解密并核对商户号、订单号、金额后，按事件 ID 与订单状态幂等处理；关闭后到账进入异常队列。 |
| `POST /api/v1/admin/orders/{id}/confirm-offline-payment` | 实际到账金额、方式、凭证引用、必要备注、二次确认 | 仅授权人员核实到账后调用；金额不符拒绝确认并记录原因；只允许一次有效收款。 |
| `POST /api/v1/app/orders/{id}/cancel`、内部超时任务 | 订单 ID、幂等键或到期时间快照 | 仅待付款订单释放占用并关闭；收款确认与关单竞争同一订单锁，只能有一个有效状态结果。 |

并发锁序建议：提交订单按 `(warehouse_id, sku_id)` 排序后锁定余额行，再锁券和积分记录；收款、关单先锁订单行，再按固定顺序处理占用与余额。外部支付请求与回调处理不占用长数据库事务。需要用 PostgreSQL 并发用例验证多 SKU 部分不足、重复提交、回调与超时竞争、迟到通知及零元订单。具体隔离级别、锁等待超时和死锁重试次数由实现试验定稿。

## 6. 阶段 A 评审与未决项

1. 项目已确认：商品只绑定启用的二级分类；分类、商品、SKU 状态枚举；商品编号与 SKU 编码长度/字符规则及大小写唯一；规格稳定 ID 组合唯一；本机临时素材上限；阶段 A 默认权限组；唯一主账号交互初始化。首批迁移需验证约束与索引实际可执行。
2. 商品请求和页面组件结构仍是候选字段表；实施时定稿其他展示字段长度、本机媒体目录、富文本净化白名单及上传失败清理。正式素材尺寸、体积和性能经目标手机实测。
3. 会话与 CSRF 方案按第 3.3 节实施；本机 HTTP 与未来 HTTPS 的 Cookie 配置应分环境验证。主账号凭据保管与交接人在实际初始化前指定。
4. 微信账号的自动提审、发布和回退路径仍在实施待验证清单 T-01；没有真实账号结论前，代码版本管理接口不写成已可自动发布。
5. 微信支付商户号尚无，交易接口均为设计契约；不能用模拟回调替代真实验签、退款和资金结果联调。

阶段 A 的接口、数据库迁移及服务端权限测试通过后，再按《开发交付说明》第 6 节记录页面、前后端联调和真机验证证据。本文在评审通过前保持草案状态。
