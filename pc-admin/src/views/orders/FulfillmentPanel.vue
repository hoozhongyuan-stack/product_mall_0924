<script setup lang="ts">
import {computed} from 'vue'
import type {Account} from '../../api'
import {shipmentEligible,fulfillmentLabel,shippingQuantity,shipmentQuantityError} from './fulfillment.mjs'
import {time,type Order} from './types'
const props=defineProps<{account:Account;order:Order}>()
const emit=defineEmits<{updated:[order:Order];operate:[action:'ship'|'correct']}>()
const hasShipLines=computed(()=>props.order.items.some(l=>l.fulfillmentKind==='SHIP'))
const shipLines=computed(()=>props.order.items.filter(l=>l.fulfillmentKind==='SHIP'))
const quantityError=computed(()=>shipmentQuantityError(props.order.items))
const canShip=computed(()=>props.account.permissionCodes.includes('fulfillment.ship'))
const history=computed(()=>props.order.shipments?.filter(shipment=>shipment.shipmentId!==props.order.shipment?.shipmentId)||[])
const canCorrect=computed(()=>canShip.value&&!!props.order.shipment)
</script>

<template>
  <section v-if="hasShipLines" class="panel order-section" aria-labelledby="shipping-heading">
    <div class="fulfillment-heading"><div><h2 id="shipping-heading">实物发货</h2><p>按下单锁定的发货仓处理；{{ order.orderKind === 'POINTS' ? '兑换成功' : '收款确认' }}时已扣库存，发货不会再次扣减。</p></div><div class="fulfillment-actions"><button v-if="canShip&&shipmentEligible(order)" class="primary-button" :disabled="!!quantityError" @click="emit('operate','ship')">登记发货</button><button v-if="canCorrect" class="secondary-button" @click="emit('operate','correct')">更正运单</button></div></div>
    <p v-if="order.dining" class="order-note"><strong>配送到餐饮店：{{order.dining.name}} · 桌号 {{order.dining.tableNumber}}</strong><br>{{order.dining.address}}</p><template v-if="!order.shipment"><p v-for="line in shipLines" :key="line.orderLineId" class="order-note"><strong>{{line.name}} · {{line.skuCode}}</strong>：<template v-if="shippingQuantity(line)">原购买 {{line.quantity}} {{line.saleUnit}} · 已完成售后退回 {{shippingQuantity(line)!.refunded}} {{line.saleUnit}} · {{line.fulfillment?.status==='AFTER_SALE'?'未发':'本次待发'}} {{shippingQuantity(line)!.remaining}} {{line.saleUnit}}</template><span v-else>发货数量待核查</span></p><p v-if="quantityError" class="order-warning" role="alert">{{quantityError}}</p></template>    <template v-if="order.shipment"><dl class="order-facts"><div><dt>发货仓</dt><dd>{{ order.shipment.warehouseName || order.shipment.warehouseId || '下单锁定仓库' }}</dd></div><div><dt>快递公司</dt><dd>{{ order.shipment.carrierName }}</dd></div><div><dt>运单号</dt><dd>{{ order.shipment.trackingNo }}</dd></div><div><dt>发货人 / 时间</dt><dd>{{ order.shipment.shippedByName || '授权操作员' }} · {{ time(order.shipment.shippedAt) }}</dd></div><div><dt>确认收货时间</dt><dd>{{ time(order.shipment.confirmedAt) }}</dd></div></dl><p class="order-note">物流查询暂不可用时，快递公司和运单号仍可查看；发货状态不受影响。</p><p v-if="(order.shipment.correctionCount || 0) > 20" class="order-note">共 {{ order.shipment.correctionCount }} 次更正，当前展示最近 20 次。</p><article v-for="(change, index) in order.shipment.corrections || []" :key="index" class="order-report"><strong>运单更正 · {{ time(change.correctedAt) }}</strong><p>{{ change.oldCarrierName }} {{ change.oldTrackingNo }} → {{ change.newCarrierName }} {{ change.newTrackingNo }}</p><p>原因：{{ change.reason }} · {{ change.actorName || '授权操作员' }}</p></article></template>
    <p v-else-if="order.status === 'PAID'">{{ fulfillmentLabel(order.fulfillmentStatus) }} · 尚未发货。</p>
    <p v-else>确认收款后才可发货。</p>

    <template v-if="history.length"><h3>历史发货批次</h3><article v-for="shipment in history" :key="shipment.shipmentId" class="order-report"><strong>{{shipment.carrierName}} · {{shipment.trackingNo}}</strong><p>{{time(shipment.shippedAt)}} · {{shipment.shippedByName||'授权操作员'}} · {{shipment.warehouseName||'平台总仓'}}</p><p>{{shipment.confirmedAt?`已收货 · ${time(shipment.confirmedAt)}`:'运输中'}}</p></article></template>
  </section>
</template>
