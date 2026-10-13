<script setup lang="ts">
import {computed, ref, watch, onUnmounted} from 'vue'
import {ElDialog} from 'element-plus'
import {api, type Account} from '../../api'
import {time, money, type Order} from './types'
import {paymentStatus} from './offline-payment.mjs'
import {fulfillmentOwnerLabel,redemptionScopeLabel} from '../../shared/fulfillment-policy'
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
        <div><dt>履约方</dt><dd>{{fulfillmentOwnerLabel(order)}}</dd></div><div><dt>扣库仓</dt><dd>{{order.inventoryWarehouse?.name || order.fulfillmentWarehouse?.name || '无实物扣库'}}</dd></div><div><dt>交付方式</dt><dd>{{({PICKUP:'到仓自提',DELIVERY:'配送到家',EXPRESS:'快递发货',REDEEM:'权益核销'} as Record<string,string>)[order.deliveryMode||'']||'按商品履约'}}</dd></div>
        <div><dt>订单归属</dt><dd>{{order.dining?`${order.dining.name} · 桌号 ${order.dining.tableNumber}`:'普通商城'}}</dd></div>
      </dl>
      <p v-if="order.address">收货：{{order.address.recipientName}} · {{order.address.phone}}<br>{{order.address.province}}{{order.address.city}}{{order.address.district}}{{order.address.detail}}</p>
      <p v-if="order.dining">配送地址：{{order.dining.address}}</p>
      <h3>订单商品</h3>
      <div class="table-wrap admin-list-table admin-list-no-actions" tabindex="0" aria-label="订单商品，可横向滚动"><table><thead><tr><th>商品 / SKU</th><th>数量</th><th>履约方式</th><th>履约状态</th><th>{{isPoints?'兑换积分':'商品实付'}}</th></tr></thead><tbody><tr v-for="line in order.items" :key="line.orderLineId"><td>{{line.name}}<small class="order-muted">{{line.skuCode}}</small></td><td>{{line.quantity}} {{line.saleUnit}}<small v-if="line.fulfillmentKind==='SHIP'" class="order-muted">已发出 {{line.fulfillment?.dispatchedQuantity||0}} · 已交付 {{line.fulfillment?.deliveredQuantity||0}}<br>售后冻结 {{line.fulfillment?.frozenQuantity||0}} · 当前可交付 {{line.fulfillment?.availableQuantity??'待核查'}}</small></td><td>{{line.fulfillmentKind==='REDEEM'?'权益核销':({PICKUP:'到仓自提',DELIVERY:'前置仓配送',EXPRESS:'快递发货'} as Record<string,string>)[order.deliveryMode||'']||'快递发货'}}</td><td><AdminStatusBadge :status="line.fulfillment?.status||'PENDING'" :label="fulfillmentLabel(line.fulfillment?.status)" /></td><td>{{isPoints?`${line.pointsTotal??'待核查'} 积分`:money(line.payableFen)}}</td></tr></tbody></table></div>
      <p v-if="['DELIVERY','PICKUP'].includes(order.deliveryMode||'')" class="order-note">由对应前置仓中心备货和交付；平台后台查看履约记录并处理售后。</p>
      <p v-if="order.failureReason" class="order-warning">配送异常：{{order.failureReason}}</p>
      <section v-for="line in order.items.filter(item=>item.fulfillmentKind==='REDEEM')" :key="line.orderLineId" class="order-section"><h3>{{line.name}} · 核销权益</h3><p>核销范围：{{redemptionScopeLabel(line.fulfillment || line)}}</p><p>已核销 {{line.fulfillment?.redeemedQuantity||0}} · 剩余 {{line.fulfillment?.remainingQuantity??'待核查'}} · 售后冻结 {{line.fulfillment?.frozenQuantity||0}} · 当前可核销 {{line.fulfillment?.availableQuantity??'待核查'}}</p><p>有效至 {{line.fulfillment?.validUntil||'待确认'}}；各授权端共用同一份剩余权益。</p><p v-for="record in line.fulfillment?.events||[]" :key="record.eventId">{{record.kind==='REVERSE'?'撤销核销':'核销'}} {{record.quantity}} · {{record.actorKind==='STORE'?record.storeName||'前置仓中心':'平台后台'}} · {{record.actorName||'授权操作员'}} · {{time(record.occurredAt)}}</p></section>
      <OrderBenefitsSummary v-if="order.orderBenefits" :summary="order.orderBenefits" :order-kind="order.orderKind" />
      <template v-if="order.shipment"><h3>物流信息</h3><p>{{order.shipment.carrierName}} · {{order.shipment.trackingNo}}<br>发货：{{time(order.shipment.shippedAt)}} · {{order.shipment.shippedByName||'授权操作员'}}</p></template>
      <template v-if="order.shipments?.length"><h3>发货批次</h3><p v-for="shipment in order.shipments" :key="shipment.shipmentId">{{shipment.carrierName}} · {{shipment.trackingNo}} · {{time(shipment.shippedAt)}} · {{shipment.confirmedAt?'已收货':'运输中'}}</p></template>
      <template v-if="order.localDeliveryEvents?.length"><h3>备货与交付记录</h3><p v-for="record in order.localDeliveryEvents" :key="record.eventId">{{({PREPARE:'备货完成',DISPATCH:'开始配送',COMPLETE:'交付完成',FAIL_DELIVERY:'配送异常',RETRY_DELIVERY:'恢复配送'} as Record<string,string>)[record.action]||record.action}} · {{time(record.occurredAt)}} · {{record.actorName}} · {{record.note||'无备注'}}</p></template>
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
