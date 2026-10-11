<script setup lang="ts">
import {computed, ref, watch, onUnmounted} from 'vue'
import {ElDialog} from 'element-plus'
import {api, type Account} from '../../api'
import {time, money, type Order} from './types'
import {paymentStatus} from './offline-payment.mjs'
import {fulfillmentLabel} from './fulfillment.mjs'
import {isPointsOrder, orderSettlementValue, paymentMethodLabel} from '../../shared/order-settlement.mjs'
import AdminStatusBadge from '../../shared/list/AdminStatusBadge.vue'
import OrderBenefitsSummary from '../../shared/OrderBenefitsSummary.vue'
const props=defineProps<{account:Account;orderId:string}>()
const emit=defineEmits<{close:[]}>()
const order=ref<Order|null>(null),loading=ref(false),error=ref('')
const isPoints=computed(()=>isPointsOrder(order.value))
let generation=0
async function load(){
  const request=++generation
  if(!props.orderId){order.value=null;return}
  order.value=null;loading.value=true;error.value=''
  try{const result=await api<Order>(`/orders/${encodeURIComponent(props.orderId)}`);if(request===generation)order.value=result}
  catch(reason){if(request===generation)error.value=reason instanceof Error?reason.message:'订单详情读取失败，请重试。'}
  finally{if(request===generation)loading.value=false}
}
watch(()=>`${props.account.accountId}:${props.orderId}`,load,{immediate:true})
onUnmounted(()=>generation++)
</script>
<template>
  <ElDialog :model-value="Boolean(orderId)" title="订单详情" width="1120px" class="admin-order-detail-dialog" :close-on-click-modal="false" @close="emit('close')">
    <p v-if="loading" role="status">正在读取订单详情…</p>
    <div v-else-if="error" class="notice" role="alert">{{error}} <button class="text-button" @click="load">重新读取</button></div>
    <template v-else-if="order">
      <dl class="order-facts">
        <div><dt>订单号</dt><dd>{{order.orderNo}}</dd></div>
        <div><dt>创建时间</dt><dd>{{time(order.createdAt)}}</dd></div>
        <div><dt>支付方式</dt><dd>{{paymentMethodLabel(order)}}</dd></div>
        <div><dt>结算状态</dt><dd><AdminStatusBadge :status="order.status" :label="paymentStatus(order)" :tone="order.paymentReviewStatus==='ANOMALY' ? 'danger' : order.status==='PAID' ? 'success' : order.status==='CLOSED' ? 'neutral' : 'pending'" /></dd></div>
        <div><dt>履约进度</dt><dd><AdminStatusBadge :status="order.fulfillmentStatus||'PENDING'" :label="order.status==='PAID'?fulfillmentLabel(order.fulfillmentStatus):'待结算'" :tone="order.status!=='PAID'?'pending':undefined" /></dd></div>
        <div><dt>{{isPoints?'兑换积分':'应付金额'}}</dt><dd>{{orderSettlementValue(order)}}</dd></div>
        <div><dt>前置仓 / 仓库</dt><dd>{{order.storeName||'平台订单'}} / {{order.fulfillmentWarehouse?.name||'—'}}</dd></div>
        <div><dt>订单归属</dt><dd>{{order.dining?`${order.dining.name} · 桌号 ${order.dining.tableNumber}`:'普通商城'}}</dd></div>
      </dl>
      <p v-if="order.address">收货：{{order.address.recipientName}} · {{order.address.phone}}<br>{{order.address.province}}{{order.address.city}}{{order.address.district}}{{order.address.detail}}</p>
      <p v-if="order.dining">配送地址：{{order.dining.address}}</p>
      <h3>订单商品</h3>
      <div class="table-wrap admin-list-table admin-list-no-actions" tabindex="0" aria-label="订单商品，可横向滚动"><table><thead><tr><th>商品 / SKU</th><th>数量</th><th>履约方式</th><th>履约状态</th><th>{{isPoints?'兑换积分':'商品实付'}}</th></tr></thead><tbody><tr v-for="line in order.items" :key="line.orderLineId"><td>{{line.name}}<small class="order-muted">{{line.skuCode}}</small></td><td>{{line.quantity}} {{line.saleUnit}}</td><td>{{line.fulfillmentKind==='REDEEM'?'到店核销':({PICKUP:'到店自提',DELIVERY:'前置仓配送',EXPRESS:'快递发货'} as Record<string,string>)[order.deliveryMode||'']||'快递发货'}}</td><td><AdminStatusBadge :status="line.fulfillment?.status||'PENDING'" :label="fulfillmentLabel(line.fulfillment?.status)" /></td><td>{{isPoints?`${line.pointsTotal??'待核查'} 积分`:money(line.payableFen)}}</td></tr></tbody></table></div>
      <OrderBenefitsSummary v-if="order.orderBenefits" :summary="order.orderBenefits" :order-kind="order.orderKind" />
      <template v-if="order.shipment"><h3>物流信息</h3><p>{{order.shipment.carrierName}} · {{order.shipment.trackingNo}}<br>发货：{{time(order.shipment.shippedAt)}} · {{order.shipment.shippedByName||'授权操作员'}}</p></template>
      <template v-if="order.localDeliveryEvents?.length"><h3>备货与交付记录</h3><p v-for="record in order.localDeliveryEvents" :key="record.eventId">{{({PREPARE:'备货完成',DISPATCH:'开始配送',COMPLETE:'交付完成'} as Record<string,string>)[record.action]||record.action}} · {{time(record.occurredAt)}} · {{record.actorName}} · {{record.note||'无备注'}}</p></template>
      <template v-if="!isPoints"><h3>收款记录</h3><p v-if="(order.receiptCount||0)>50">共 {{order.receiptCount}} 条，显示最近50条；更早记录请按权限查询审计存档。</p><p v-if="!order.receipts?.length">尚无到账凭证。</p><p v-for="receipt in order.receipts" :key="receipt.receiptId">{{receipt.externalTradeNo}} · {{money(receipt.amountFen)}} · {{time(receipt.paidAt)}} · {{receipt.anomaly?.status==='OPEN'?'异常待核查':receipt.appliedAt?'已结算':'待处理'}}</p></template>
      <p class="hint">详情弹窗用于核对记录。收款、发货与交付操作请使用列表对应的操作弹窗。</p>
    </template>
    <template #footer><button class="secondary-button" @click="emit('close')">关闭</button><button class="primary-button" :disabled="loading" @click="load">刷新详情</button></template>
  </ElDialog>
</template>
<style>
.admin-order-detail-dialog .order-facts{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;margin-bottom:24px}
.admin-order-detail-dialog dt{color:var(--mall-color-muted);font-size:13px}.admin-order-detail-dialog dd{margin:6px 0 0;overflow-wrap:anywhere}
@media(max-width:700px){.admin-order-detail-dialog .order-facts{grid-template-columns:1fr}}
</style>
