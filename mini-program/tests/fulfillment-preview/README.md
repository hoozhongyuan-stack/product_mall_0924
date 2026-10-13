# 履约治理小程序源码预览

此开发工具加载实际 `pages/store-center/index`、`pages/store-center/order`、`pages/orders/detail` 的 Page JavaScript、WXML 与 WXSS，将原生节点和事件映射到浏览器。业务判断、请求、防重与页面状态均调用原 Page 代码。所有 API 经 loopback 代理进入隔离本地后端，不允许连接 UAT 或生产接口。

准备仓库外的私有 fixture JSON（不提交令牌）：

```json
{
  "apiBaseUrl": "http://127.0.0.1:8004",
  "memberToken": "本地隔离会员会话",
  "storeId": "本地前置仓 UUID",
  "orders": [{"orderId": "本地订单 UUID", "name": "配送测试订单"}]
}
```

运行：

```sh
python3 mini-program/tests/fulfillment-preview/server.py --config /tmp/fulfillment-preview.json --port 8874
```

访问 `http://127.0.0.1:8874/`，选择前置仓工作台、前置仓订单或顾客详情。请求可以真实改变隔离数据库的测试订单，应只使用专门的本地数据。修改源码后刷新浏览器，不能从此桥推断微信模拟器、相机扫码、地图、电话、真机或微信平台已验证。

桥实现源代码加载、WXML 条件/循环/表达式、输入/点击/确认事件及 WXSS 导入与 rpx 转换。当前三个页面可验证履约及核销动作。微信布局与组件行为仍以官方编译器、开发者工具和真机验证为准；地图、电话、相机使用降级提示。

预览服务检查 Host 与 Origin，写请求必须来自本预览页面；请求体上限 1 MiB。凭证及合成会员令牌不会写进仓库。恢复中的履约请求仅持久保存账号指纹、仓/订单、原请求键与不含明文凭证的请求字段；重新打开后优先读取真实履约记录，需要重试时重新输入相同凭证。浏览器桥仅将这类恢复信息保存到本地 localStorage。
