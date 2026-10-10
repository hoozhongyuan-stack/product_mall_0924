# 后端第三阶段实际 RED 证据

以下结果来自本次隔离测试执行，未连接开发数据库或微信平台。

1. `pages.tests.test_editor_v4` 初始 9 项：2 failures、1 error。`test_versioned_coupon_props` 在 schema 4 校验抛出 `PageConfigError: 页面配置字段不正确。`；页面保存 `400 != 200`；release-report `404 != 200`。随后实现 schema 4、券读取端点和报告。
2. `test_report_canonical_uuid_and_chinese_dynamic_warning`：合法大写活动 UUID 被错误标记 `COUPON_REFERENCE_INVALID`，`canPublish=False`。随后按 UUID 身份归一化查询状态，中文展示动态警告。
3. `test_public_auto_coupon_selector_does_not_expose_ignored_private_ids`：公开 AUTO config 的 `campaignIds` 仍含私有草稿活动 UUID，预期 `[]` 的断言失败。随后增加公开投影，并测试不可变发布快照仍保留原后台选择值。
4. `test_public_product_source_projects_unused_selectors_without_mutation`：公开 CATEGORY 商品 config 的 `productIds` 仍含无用途商品 UUID，预期 `[]` 的断言失败。随后投影同时清除 CATEGORY 的 productIds 和 MANUAL 的 categoryId，测试后台原对象未被改写。

会员状态测试曾试图直接更新已发布优惠券条款，真实 PostgreSQL 守卫抛出 `published coupon terms are immutable`。该失败属于测试夹具不符合原权益规则，修复为新建独立范围活动；本人领取次数通过原 `claim_coupon` 生成真实领取凭证，不绕过发行/券/计数守恒约束。权益生产规则未因测试失败放宽。

5. `test_release_report_detects_name_only_public_change`：已发布微页面只修改名称后，报告diff缺少nameChanged，实际RED为 `KeyError: nameChanged`。随后增加endpoint名称差异标识，并验证主题、元数据和组件均不变时仍明确报告公开名称变化。
