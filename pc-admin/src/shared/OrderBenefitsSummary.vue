<script setup lang="ts">
import {type OrderBenefits} from './benefits'
defineProps<{summary:OrderBenefits;orderKind?:string}>()
const money=(fen:number)=>`¥${(fen/100).toFixed(2)}`
</script>
<template><section class="panel order-section"><h2>订单累计权益结果</h2><p>这些结果汇总原订单全部已完成售后，不代表本笔申请单独的权益变动。</p><p v-if="orderKind==='POINTS'" class="order-note">兑换订单不产生有效消费或消费奖励积分；累计返还按原成交积分快照计算。</p><p v-else-if="summary.status==='PENDING'" class="order-note">等待全部履约完成且无进行中的售后，消费积分与有效消费才结算。</p><p v-else-if="summary.status==='SKIPPED'" class="order-warning">历史规则快照缺失，当前未自动结算，请核查原订单规则。</p><p v-else class="order-note">权益已按原订单规则结算。</p><dl v-if="summary.status!=='SKIPPED'" class="order-facts"><div><dt>最终有效消费</dt><dd>{{money(summary.effectiveSpendFen)}}</dd></div><div><dt>当前有效消费积分</dt><dd>{{summary.earnedPoints}}</dd></div><div><dt>{{orderKind==='POINTS'?'累计返还兑换积分':'累计返还抵扣积分'}}</dt><dd>{{summary.returnedPoints}}</dd></div><div><dt>累计扣回消费积分</dt><dd>{{summary.clawedBackPoints}}</dd></div><div><dt>优惠券恢复</dt><dd>{{summary.couponRestored?'已按原有效期恢复':'未恢复'}}</dd></div></dl></section></template>
