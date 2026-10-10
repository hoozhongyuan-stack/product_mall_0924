# 后端第三阶段最终验证

应用代码已冻结。测试使用隔离 PostgreSQL 测试库 `test_micro_v4_final_1010`，由 Django 创建和销毁；没有迁移开发/生产数据库，没有修改生产运行时闸门，没有连接微信平台。

最终命令：

```sh
.venv/bin/python -m coverage run \
  --source=pages.editor_v4,pages.coupon_views,pages.release_reports,benefits.page_coupons,pages.runtime \
  backend/manage.py test pages benefits catalog.tests.test_page_products --noinput
.venv/bin/python -m coverage report -m
```

数据库凭据仅从既有私有配置读取后注入测试进程，未输出到日志。`pages` 全套、`benefits` 全套和商品卡测试共 **202 项通过，71.380 秒**。日志中的 `Refund settlement failed (RuntimeError)` 是既有失败路径测试的预期日志，整体退出 0、结果 OK。

| 模块 | 总语句 | 未覆盖 | 覆盖率 |
|---|---:|---:|---:|
| benefits/page_coupons.py | 39 | 1 | 97% |
| pages/coupon_views.py | 52 | 0 | 100% |
| pages/editor_v4.py | 18 | 2 | 89% |
| pages/release_reports.py | 143 | 6 | 96% |
| pages/runtime.py（包含前两阶段） | 27 | 1 | 96% |

第三阶段四个新增模块覆盖 **243/252 条语句，96.43%**；连同既有 runtime 模块共 **269/279 条语句，96.42%**。这是指定模块语句覆盖率，不代表整个后端覆盖率。

独立内存配置执行 `makemigrations --check --dry-run` 无模型漂移，Django check 无问题；`git diff --check -- backend docs/contract-v1.md` 退出0。无新增迁移/依赖。

名称差异收尾：真实已发布微页面仅修改name的RED原为KeyError；最终报告diff.nameChanged比较草稿名称和当前线上版本名称，无线上true。名称变化即使配置相同也明确报告。

安全边界：公开券读取绑定当前发布版本；无token游客、无效token401；响应no-store并Vary Authorization/Cookie；MANUAL只输出公开SELF/BOTH活动及动态状态，AUTO只查询当前可领活动；隐藏商品scope名称/ID不出站。发布报告只读，双修订号、公开引用状态、素材文件、PAGE循环、运行时支持均独立检测。公开配置投影清除无用途AUTO campaignIds、CATEGORY productIds、MANUAL categoryId，后台草稿和不可变发布快照保留原值。完整后台preview的componentData仅商品，couponData独立承载券卡；实际领取继续原claim接口与幂等恢复。

详见同目录 backend-final.log、backend-coverage-final.log、backend-checks-final.log、backend-tdd-red.md。测试不证明正式部署、负载验收或小程序真机领取成功。
