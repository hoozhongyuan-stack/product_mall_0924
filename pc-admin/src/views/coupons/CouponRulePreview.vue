<script setup lang="ts">
import { computed } from 'vue'
import { yuanToFen } from '../../shared/money.mjs'
import { money } from './types'
const props = defineProps<{ title: string; kind: string; minimum: string; discount: string; productCount: number }>()
const amount = (value: string) => { const fen = yuanToFen(value); return fen === null ? '待填写' : money(fen) }
const rule = computed(() => props.kind === 'CASH' ? `现金券 ${amount(props.discount)}` : `满 ${amount(props.minimum)} 减 ${amount(props.discount)}`)
</script>
<template><aside class="coupon-rule-preview" aria-label="券面摘要"><div><span class="coupon-note">券面摘要</span><strong>{{ title.trim() || '优惠券活动' }}</strong></div><p class="coupon-rule-value">{{ rule }}</p><span class="coupon-note">{{ productCount ? `指定 ${productCount} 个商品` : '全场商品' }} · 不抵扣运费</span></aside></template>
