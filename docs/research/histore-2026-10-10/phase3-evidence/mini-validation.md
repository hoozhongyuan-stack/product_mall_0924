# 第三阶段小程序验证记录

2026-10-10。本记录仅描述本轮实际执行，未上传、提审、发布或提交代码。

## 实现

- `mini-program/lib/home.js` 支持 schema 1–4，新增 COUPON_LIST；未来 schema 5 受控拒绝。
- `mini-program/lib/page-coupons.js` 通过已发布 pageId/versionId 读取一次券 hydration，并复用 `couponPage(true)` 的原 `claim`、`retryClaim`、`performClaim`、身份隔离与 `lib/coupons.js` 持久防重凭证。
- HOME hydration 响应上下文为 `home`，MICRO 为请求中的 UUID；版本必须与当前已读取发布版完全一致。版本冲突后的重试先重新读取公开页面，再取得新券数据。
- 不接受不属于当前组件/当前 hydration 的活动事件；隐藏、卸载、重载和身份切换废弃迟到结果；仅读取当前会员的 pending。
- 显示未登录、可领取、上限、抢光、过期、未开始、不可领取、空、加载、失败、待确认和已确认成功。
- 后端过滤私有/不可见适用商品后，使用 `scopeRestricted` 保留指定商品范围，不将空公开商品数组误显示为全场券。
- WXML 共用券卡和原权益领取入口；LIST / SCROLL 布局。分享沿既有发布成功与公开封面安全规则，不导出私人领取资料。

## 命令与结果

| 命令/边界 | 实际结果 | 证据 |
|---|---|---|
| 新功能首次 `npm test` RED | 210 用例，203 过、7 个预期失败 | `mini-red.log` |
| 适用范围边界 RED | 原显示全场，期望指定商品，1 个预期失败 | `mini-scope-red.log` |
| 页面发布版改变后重试 RED | 原请求仍读旧版本券，1 个预期失败 | `mini-version-red.log` |
| 最终 `npm test` | 214 / 214 通过 | `mini-tests-final.log` |
| `npm run test:coverage` | 行 96.04%、分支 87.46%、函数 87.58%，80% 门槛通过 | `mini-coverage-final.log` |
| 新 page-coupons adapter 覆盖 | 行 100%、分支 83.58%、函数 100% | 同上 |
| 官方 wcc 静态编译 | 23 个 WXML，退出 0，无 stderr | `mini-official-compilers.json` |
| 官方 wcsc 静态编译 | 30 个 WXSS，退出 0，无 stderr | 同上 |
| `git diff --check` | 通过 | 工具命令回执 |

测试涵盖本人 UNKNOWN 查询与 COMPLETED 回执复用、同 key 防重、错误会员 pending 隔离、身份切换、隐藏后迟到 hydration、错误 page/version、失败/拒绝、版本变化后重新加载、私有商品范围真相。

官方编译只说明语法/静态包处理通过；本轮未执行微信模拟器、真机、真实微信登录/领取、上传、提审、正式发布。真实服务接口联调及部署也不能从这些小程序单元测试推定。

## 后端独立静态安全审查

检查 `pages/coupon_views.py`、`editor_v4.py`、`release_reports.py`、`benefits/page_coupons.py`、`catalog/coupon_targets.py` 及原权益领取入口。未发现 HIGH / CRITICAL：

- 只读当前公开发布版，严格版 ID；无效 Authorization 401，不降级匿名。
- `private_response` 为成功/失败响应设置 no-store，并 Vary Authorization/Cookie。
- 活动仅 PUBLISHED 且 SELF/BOTH；匿名无个人计数，会员身份仍经启用状态/AppID/authVersion 校验。
- 商品范围名称/ID仅从已公开上架商品取得，不披露草稿商品；原领取继续校验时段、额度、模式、成员资格并持久幂等。
- 发布报告绑定草稿修订和发布修订，读页面图锁；失效公开券动态状态为 warning，不绕过正式发布/回退校验。

范围风险：最多 40 组件、每券组件最多 10 卡，查询和数组有界；本轮未做响应字节上限、压力或 P95 验证，不宣称性能验收。报告及 hydration 是读模型，实际领取时原权益事务重新校验规则。
