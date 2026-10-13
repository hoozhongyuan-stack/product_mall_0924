<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { api, ApiError, type Account } from '../../api';
import { amountFen, reconciliationError } from './offline-payment.mjs';
import { redemptionError, shippingQuantity, shipmentQuantityError, shipmentError } from './fulfillment.mjs';
import { actionLabels, localActionBody, orderActions, validatePending, actionPermission, receiptRecoveryMatches, type OrderAction } from './order-actions.mjs';
import {fulfillmentOwnerLabel,redemptionScopeLabel} from '../../shared/fulfillment-policy';
import { orderSettlementValue } from '../../shared/order-settlement.mjs';
import { time, type Order, type Carrier, type RedemptionLookup, type Reconciliation } from './types';
const props = defineProps<{
    account: Account;
    orderId: string;
    action: OrderAction | null;
    reconciliation?: Reconciliation | null;
    redemptionCode?: string;
    redemptionQuantity?: string;
}>();
const emit = defineEmits<{
    close: [
    ];
    updated: [
        order: Order
    ];
    notice: [
        text: string
    ];
}>();
const blank = () => ({ merchantAccountId: '', externalTradeNo: '', amount: '', paidAt: '', note: '', verified: false, person: '', phone: '', pickupCode: '', carrierCode: '', trackingNo: '', reason: '', code: '', quantity: '1' });
const order = ref<Order | null>(null), loading = ref(false), busy = ref(false), error = ref(''), notice = ref(''), storageError = ref(''), verified = ref(false), form = ref(blank());
const carriers = ref<Carrier[]>([]), voucher = ref<RedemptionLookup | null>(null), pickupVerified = ref(false);
type Pending = {
    actor: string;
    orderId: string;
    action: OrderAction;
    key: string;
    path: string;
    body: Record<string, unknown>;
    phase: 'prepare' | 'confirm' | 'write';
};
const pending = ref<Pending | null>(null);
const tracking = ref<{
    carrierName: string;
    trackingNo: string;
    events: {
        time?: string;
        context?: string;
        description?: string;
    }[];
} | null>(null);
const storageKey = computed(() => `mall-order-action:${props.account.accountId}:${props.orderId}:${props.action}`);
const isReceipt = computed(() => props.action === 'receipt' || props.action === 'anomaly');
const readonlyAction = computed(() => ['tracking', 'aftersale'].includes(props.action || ''));
const allowed = computed(() => !!props.action && !!order.value && orderActions(order.value, props.account.permissionCodes).includes(props.action));
const warehouseAction = computed(() => ['prepare','deliver','delivered','pickup'].includes(props.action || ''));
const permitted = computed(() => !!props.action && !warehouseAction.value && props.account.permissionCodes.includes(actionPermission(props.action)));
const locked = computed(() => busy.value || !!pending.value);
const physical = computed(() => order.value?.items.filter(l => l.fulfillmentKind === 'SHIP') || []);
const validation = computed(() => {
    if (!order.value)
        return '请先读取订单。';
    if (!allowed.value && !pending.value)
        return '订单当前不可执行此操作，请刷新核对。';
    if (isReceipt.value)
        return reconciliationError(form.value, order.value.payableFen);
    if (props.action === 'ship' || props.action === 'correct')
        return shipmentError(form.value.carrierCode, form.value.trackingNo, carriers.value) || (props.action === 'ship' ? shipmentQuantityError(order.value.items) : form.value.reason.trim() ? '' : '请填写更正原因。');
    if (props.action === 'prepare' && !form.value.verified)
        return '请核对商品及数量，确认备货完成。';
    if (props.action === 'deliver' && (!form.value.person.trim() || !/^\+?[0-9 -]{7,20}$/.test(form.value.phone)))
        return '请填写配送人员和有效联系电话。';
    if (props.action === 'deliver' && String(localActionBody('deliver', order.value.revision, form.value).note).length > 500)
        return '配送资料与备注合计不能超过500字。';
    if (props.action === 'delivered' && (form.value.note.trim().length < 5 || !form.value.verified))
        return '请填写至少5字交付说明，并确认实际送达。';
    if (props.action === 'pickup' && !pickupVerified.value)
        return '请先核验顾客出示的自提凭证。';
    if (props.action === 'redeem')
        return !voucher.value ? '请先读取并核对凭证。' : !Number.isSafeInteger(voucher.value.availableQuantity) || voucher.value.availableQuantity < 0 || !Number.isSafeInteger(voucher.value.revision) || voucher.value.revision < 0 ? '凭证数量或修订待核查，请重新读取。' : voucher.value.status !== 'READY' ? '此凭证当前不可核销。' : redemptionError(form.value.quantity, voucher.value.availableQuantity);
    return '';
});
let epoch = 0;
function message(e: unknown) { return e instanceof Error ? e.message : '操作失败，请重试。'; }
function persist(value: Pending | null) { try {
    if (value)
        sessionStorage.setItem(storageKey.value, JSON.stringify(value));
    else
        sessionStorage.removeItem(storageKey.value);
    pending.value = value;
    storageError.value = '';
}
catch {
    storageError.value = '浏览器存储不可用，暂不能提交；请恢复存储后重试。';
    throw Error(storageError.value);
} }
function restore() { try {
    const raw = sessionStorage.getItem(storageKey.value);
    if (!raw)
        return;
    const p = JSON.parse(raw) as Pending;
    if (!props.action || !validatePending(p, props.account.accountId, props.orderId, props.action))
        throw Error('原操作记录不完整，请核查订单实际结果。');
    pending.value = p;
    notice.value = '存在上次结果待确认的请求。先查询最新订单，再使用原请求重试。';
}
catch (e) {
    storageError.value = message(e);
} }
async function read(current = epoch) { const result = await api<Order>(`/orders/${encodeURIComponent(props.orderId)}`); if (current === epoch)
    order.value = result; return result; }
function localDateInput(value: string) { const date = new Date(value); if (!Number.isFinite(date.getTime()))
    return ''; return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16); }
function restoreForm() { const p = pending.value; if (p && isReceipt.value) {
    const b = p.body;
    form.value = { ...form.value, merchantAccountId: String(b.merchantAccountId || ''), externalTradeNo: String(b.externalTradeNo || ''), amount: b.amountFen ? (Number(b.amountFen) / 100).toFixed(2) : '', paidAt: b.paidAt ? localDateInput(String(b.paidAt)) : '', note: String(b.note || ''), verified: true };
} }
async function initialize() {
    const current = ++epoch;
    order.value = null;
    pending.value = null;
    voucher.value = null;
    tracking.value = null;
    pickupVerified.value = false;
    error.value = '';
    notice.value = '';
    storageError.value = '';
    verified.value = false;
    carriers.value = [];
    busy.value = false;
    if (!props.action || !props.orderId)
        return;
    loading.value = true;
    form.value = { ...blank(), code: props.redemptionCode || '', quantity: props.redemptionQuantity || '1' };
    try {
        restore();
        if (!pending.value && props.reconciliation && isReceipt.value && props.reconciliation.actorId === props.account.accountId) {
            const r = props.reconciliation;
            persist({ actor: props.account.accountId, orderId: props.orderId, action: props.action, key: crypto.randomUUID(), path: `/payments/offline-reconciliations/${r.reconciliationId}/confirm`, phase: 'confirm', body: { merchantAccountId: r.merchantAccountId, externalTradeNo: r.externalTradeNo, amountFen: r.amountFen, paidAt: r.paidAt, note: r.note } });
        }
        const result = await read(current);
        if (current !== epoch)
            return;
        form.value = { ...form.value, merchantAccountId: result.paymentInstructions?.merchantAccountId || '', amount: (result.payableFen / 100).toFixed(2), carrierCode: result.shipment?.carrierCode || '', trackingNo: result.shipment?.trackingNo || '' };
        restoreForm();
        if (['ship', 'correct'].includes(props.action)) {
            const response = await api<{
                items: Carrier[];
            }>('/fulfillment/carriers');
            if (current === epoch)
                carriers.value = response.items.filter(c => c.enabled !== false);
        }
        if (props.action === 'tracking') {
            const result = await api<NonNullable<typeof tracking.value>>(`/orders/${encodeURIComponent(props.orderId)}/tracking`);
            if (current === epoch)
                tracking.value = result;
        }
    }
    catch (e) {
        if (current === epoch)
            error.value = message(e);
    }
    finally {
        if (current === epoch)
            loading.value = false;
    }
    if (current === epoch && props.action === 'redeem' && props.redemptionCode && !pending.value && !error.value) await lookup();
}
async function recover() { if (busy.value)
    return; const current = epoch; busy.value = true; error.value = ''; try {
    const latest = await read(current);
    if (current === epoch) {
        if (pending.value?.phase === 'confirm' && !receiptRecoveryMatches(latest, pending.value))
            throw Error('当前订单的收款记录与原请求不一致，已阻止重试；请核查收款记录。');
        verified.value = true;
        notice.value = '最新订单已读取。重试会沿用原请求及防重复标识，请核对实际记录。';
    }
}
catch (e) {
    if (current === epoch)
        error.value = message(e);
}
finally {
    if (current === epoch)
        busy.value = false;
} }
async function lookup() { if (busy.value || !order.value)
    return; const current = epoch; busy.value = true; error.value = ''; voucher.value = null; pickupVerified.value = false; try {
    if (props.action === 'pickup') {
        const result = await api<{
            verified: boolean;
            revision: number;
        }>(`/orders/${order.value.orderId}/pickup-preview`, { method: 'POST', body: JSON.stringify({ pickupCode: form.value.pickupCode.trim() }) });
        if (current === epoch) {
            if (!result.verified || result.revision !== order.value.revision)
                throw Error('订单或凭证已变化，请刷新后核验。');
            pickupVerified.value = true;
        }
    }
    else {
        const result = await api<RedemptionLookup>('/redemptions/lookup', { method: 'POST', body: JSON.stringify({ code: form.value.code.trim() }) });
        if (current === epoch) {
            if (result.orderId !== order.value.orderId)
                throw Error('此凭证不属于当前订单，请核对顾客出示的凭证。');
            voucher.value = result;
        }
    }
}
catch (e) {
    if (current === epoch)
        error.value = message(e);
}
finally {
    if (current === epoch)
        busy.value = false;
} }
function request(): Pending { const o = order.value!, a = props.action!, f = form.value; const base = { actor: props.account.accountId, orderId: o.orderId, action: a, key: crypto.randomUUID() }; if (isReceipt.value)
    return { ...base, phase: 'prepare', path: `/orders/${o.orderId}/offline-reconciliations`, body: { expectedRevision: o.revision, merchantAccountId: f.merchantAccountId.trim(), externalTradeNo: f.externalTradeNo.trim(), amountFen: amountFen(f.amount), paidAt: new Date(f.paidAt).toISOString(), note: f.note.trim(), verified: true } }; if (a === 'ship' || a === 'correct')
    return { ...base, phase: 'write', path: `/orders/${o.orderId}/shipment${a === 'correct' ? '/correct' : ''}`, body: { expectedRevision: o.revision, carrierCode: f.carrierCode, trackingNo: f.trackingNo.trim(), ...(a === 'correct' ? { reason: f.reason.trim() } : {}) } }; if (a === 'redeem')
    return { ...base, phase: 'write', path: `/orders/${o.orderId}/redemptions/${voucher.value!.voucherId}/use`, body: { expectedRevision: voucher.value!.revision, quantity: Number(f.quantity) } }; if (a === 'query')
    return { ...base, phase: 'write', path: `/orders/${o.orderId}/wechat/query`, body: {} }; return { ...base, phase: 'write', path: `/orders/${o.orderId}/local-fulfillment`, body: localActionBody(a, o.revision, f) }; }
async function submit() {
    if (busy.value || loading.value || storageError.value || !order.value || !props.action || readonlyAction.value || !permitted.value || (!pending.value && validation.value) || (pending.value && !verified.value))
        return;
    const current = epoch;
    busy.value = true;
    error.value = '';
    notice.value = '';
    try {
        if (!pending.value)
            persist(request());
        let p = pending.value!;
        if (p.phase === 'prepare') {
            const result = await api<{
                reconciliationId: string;
            }>(p.path, { method: 'POST', headers: { 'Idempotency-Key': p.key }, body: JSON.stringify(p.body) });
            if (current !== epoch)
                return;
            if (typeof result.reconciliationId !== 'string' || !/^[a-zA-Z0-9-]+$/.test(result.reconciliationId))
                throw Error('收款核对回执不完整，请先查询原操作结果。');
            p = { ...p, path: `/payments/offline-reconciliations/${result.reconciliationId}/confirm`, phase: 'confirm' };
            persist(p);
        }
        const result = await api<{
            outcome?: string;
            order?: Order;
            status?: string;
        }>(p.path, { method: 'POST', headers: { 'Idempotency-Key': p.key }, body: JSON.stringify(p.phase === 'confirm' ? {} : p.body) });
        if (current !== epoch)
            return;
        if (result.outcome === 'PENDING') {
            verified.value = false;
            notice.value = '到账已登记，结算仍待处理。请查询订单后重试原请求。';
            return;
        }
        const updated = result.order || await read(current);
        if (current !== epoch)
            return;
        persist(null);
        emit('updated', updated);
        emit('notice', result.outcome === 'ANOMALY' ? '到账已登记为异常，订单不会恢复或重复结算。' : props.action === 'query' && updated.status === 'PENDING_PAYMENT' ? '微信尚未确认支付成功，请按查询结果继续核查。' : `${actionLabels[props.action]}已完成。`);
        emit('close');
    }
    catch (e) {
        if (current === epoch) {
            if (e instanceof ApiError && e.status >= 400 && e.status < 500 && e.status !== 408 && e.status !== 429)
                persist(null);
            verified.value = false;
            error.value = `${message(e)}${pending.value ? ' 操作结果待确认，请先查询订单后使用原请求重试。' : ''}`;
        }
    }
    finally {
        if (current === epoch)
            busy.value = false;
    }
}
function close() { if (!busy.value)
    emit('close'); }
watch(() => `${props.account.accountId}:${props.account.permissionCodes.join(',')}:${props.orderId}:${props.action}`, initialize, { immediate: true });
onBeforeUnmount(() => epoch++);
</script>
<template>
<el-dialog :model-value="!!action" :title="action ? actionLabels[action] : ''" width="700" class="order-confirm-dialog" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @update:model-value="close">
<p v-if="loading" role="status">正在读取最新订单…</p><p v-if="storageError" class="order-warning" role="alert">{{storageError}}</p><p v-if="error" class="order-warning" role="alert">{{error}} <button v-if="!pending" class="text-button" :disabled="busy" @click="initialize">重新读取</button></p>
<template v-if="order&&!loading"><div class="order-action-summary"><strong>{{order.orderNo}} · {{orderSettlementValue(order)}}</strong><p>履约方：{{fulfillmentOwnerLabel(order)}}<span v-if="order.fulfillmentWarehouse"> · 仓库：{{order.fulfillmentWarehouse.name}}</span></p><p v-if="order.dining">归属餐饮店：{{order.dining.name}} · 桌号 {{order.dining.tableNumber}}<br>收货位置：{{order.dining.address}}</p><p v-else-if="order.address">{{order.address.recipientName}} · {{order.address.phone}}<br>{{order.address.province}}{{order.address.city}}{{order.address.district}}{{order.address.detail}}</p></div>
<p v-if="warehouseAction" class="order-warning">配送、自提由前置仓中心执行。平台可查询原操作结果，不能重新提交交付动作。</p><p v-else-if="!allowed&&!pending" class="order-warning">订单当前不可执行此操作，或当前账号没有操作权限。</p><p v-if="notice" class="order-note" role="status">{{notice}}</p><div v-if="pending" class="order-warning"><h3>原请求内容 · 修订 {{pending.body.expectedRevision??'已保存收款凭证'}}</h3><p v-if="pending.body.paidAt">到账时间：{{time(String(pending.body.paidAt))}}</p><p v-if="pending.body.note">说明：{{pending.body.note}}</p><p v-if="pending.body.carrierCode">快递：{{pending.body.carrierCode}} · 运单：{{pending.body.trackingNo}}</p><p v-if="pending.body.reason">更正原因：{{pending.body.reason}}</p><p v-if="pending.body.quantity">本次核销：{{pending.body.quantity}}份</p><p v-if="pending.body.pickupCode">自提凭证：已保存原核验凭证</p><p>原请求已固定，请先查询最新订单并核对实际处理结果。关闭弹窗后仍会保留待确认记录。</p><button class="secondary-button" :disabled="busy" @click="recover">查询订单结果</button></div>
<fieldset :disabled="locked||!allowed" class="order-action-fields">
<template v-if="isReceipt"><p>请按银行或渠道账单核对实际到账，用户报告付款不作为收款依据。{{action==='anomaly'?'原单已关闭，到账只登记异常资金。':''}}</p><div class="order-form"><label>收款账户标识<input v-model="form.merchantAccountId" name="merchantAccountId" maxlength="80"></label><label>实际到账流水号<input v-model="form.externalTradeNo" name="externalTradeNo" maxlength="128"></label><label>实际到账金额（元）<input v-model="form.amount" name="amount" inputmode="decimal"></label><label>实际到账时间<input v-model="form.paidAt" name="paidAt" type="datetime-local"></label><label class="wide">核对备注 / 金额差异原因<textarea v-model="form.note" maxlength="500" rows="3"></textarea></label><label class="wide check-row"><input v-model="form.verified" name="verified" type="checkbox">已核对实际账户、流水、金额和到账时间</label></div></template>
<template v-if="['prepare','deliver','delivered','pickup','ship'].includes(action||'')"><h3>本次剩余实物数量</h3><p v-for="line in physical" :key="line.orderLineId">{{line.name}} · {{line.skuCode}} · {{shippingQuantity(line)?.remaining??'待核查'}} {{line.saleUnit}}</p><p>已退回数量不再交付，履约仓沿用订单快照。</p></template>
<label v-if="action==='prepare'" class="check-row"><input v-model="form.verified" type="checkbox">已核对商品、规格及剩余数量，备货完成</label>
<div v-if="action==='deliver'" class="order-form"><label>配送人员<input v-model="form.person" maxlength="40"></label><label>联系电话<input v-model="form.phone" type="tel" maxlength="20"></label></div>
<label v-if="['prepare','deliver','delivered'].includes(action||'')">{{action==='delivered'?'实际交付说明（至少5字）':'操作备注（选填）'}}<textarea v-model="form.note" maxlength="500" rows="3"></textarea></label><label v-if="action==='delivered'" class="check-row"><input v-model="form.verified" type="checkbox">以上剩余商品已实际送达，确认完成配送</label>
<div v-if="action==='pickup'"><p>自提按整单剩余实物数量交付；请核验顾客出示的自提码。</p><label>自提凭证<input v-model="form.pickupCode" maxlength="40" @input="pickupVerified=false"></label><button class="secondary-button" :disabled="busy||!form.pickupCode.trim()" @click="lookup">核验自提凭证</button><p v-if="pickupVerified" class="order-success">凭证核验通过，本次交付上述全部剩余实物数量。</p></div>
<div v-if="action==='ship'||action==='correct'" class="order-form"><label>快递公司<select v-model="form.carrierCode"><option value="">请选择</option><option v-for="c in carriers" :key="c.code" :value="c.code">{{c.name}}</option></select></label><label>运单号<input v-model="form.trackingNo" maxlength="80"></label><label v-if="action==='correct'" class="wide">更正原因<textarea v-model="form.reason" rows="3" maxlength="500"></textarea></label></div>
<div v-if="action==='redeem'"><label>顾客出示的核销凭证<input v-model="form.code" maxlength="40" @input="voucher=null"></label><button class="secondary-button" :disabled="!form.code.trim()||busy" @click="lookup">读取凭证</button><template v-if="voucher"><p>{{voucher.name}} · {{redemptionScopeLabel(voucher)}} · 售后冻结 {{voucher.frozenQuantity||0}} 份 · 剩余 {{voucher.remainingQuantity}} 份 · 当前可核销 {{voucher.availableQuantity}} 份</p><label>本次核销数量<input v-model="form.quantity" inputmode="numeric"></label></template></div>
<p v-if="action==='query'">向微信查询真实支付结果，支付成功后由服务端结算；查询未付款不会人工标记为已付款。</p>
</fieldset>
<template v-if="action==='tracking'&&tracking"><h3>{{tracking.carrierName}} · {{tracking.trackingNo}}</h3><p v-if="!tracking.events.length">暂无可用物流轨迹，快递公司与运单号仍可核对。</p><p v-for="(event,index) in tracking.events" :key="index">{{event.time}} · {{event.context||event.description}}</p></template>
<p v-if="action==='aftersale'">本单售后正在处理。<RouterLink :to="{path:'/aftersales',query:{orderNo:order.orderNo}}">进入本单售后工作台</RouterLink></p>
<p v-if="validation&&!readonlyAction&&!pending" class="order-note">{{validation}}</p></template>
<template #footer><div class="dialog-footer"><button class="secondary-button" :disabled="busy" @click="close">{{readonlyAction?'关闭':'取消'}}</button><button v-if="!readonlyAction" class="primary-button" data-test="action-submit" :disabled="busy||loading||!!storageError||!order||!permitted||(!pending&&!!validation)||(!!pending&&!verified)" @click="submit">{{busy?'处理中…':pending?'使用原请求重试':action ? actionLabels[action]:''}}</button></div></template>
</el-dialog>
</template>
<style scoped>
.order-action-summary{padding:14px 16px;background:var(--mall-color-canvas-admin,#f4f5f5);border-radius:8px;margin-bottom:20px}.order-action-fields{border:0;margin:16px 0;padding:0;min-width:0}.order-action-fields>label{display:grid;gap:7px;margin:16px 0}.order-action-fields .check-row{display:flex}input:not([type=checkbox]),textarea,select{width:100%;padding:9px 11px;border:1px solid var(--mall-color-border,#e7e9eb);border-radius:6px;background:white;font:inherit;color:inherit}input[type=checkbox]{accent-color:var(--mall-color-brand,#a3202b)}h3{font-size:15px;margin:18px 0 8px}:deep(.el-dialog){max-width:calc(100vw - 32px)}
</style>
