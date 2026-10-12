<script setup lang="ts">
const props=defineProps<{modelValue:number|null;label:string;nullable?:boolean;disabled?:boolean}>()
const emit=defineEmits<{ 'update:modelValue':[value:number|null] }>()
function change(event:Event){
 const input=event.target as HTMLInputElement
 if(input.value===''){emit('update:modelValue',props.nullable?null:0);return}
 const amount=Number(input.value),fen=Math.round(amount*100)
 if(Number.isFinite(amount)&&amount>=0&&amount<=10000000&&Number.isSafeInteger(fen))emit('update:modelValue',fen)
}
</script>
<template><label>{{label}}（元）<input class="fen-input" type="number" inputmode="decimal" :aria-label="label" :value="modelValue===null?'':modelValue/100" min="0" max="10000000" step="0.01" :disabled="disabled" @input="change"><small v-if="nullable">留空表示不设置门槛。</small></label></template>
<style scoped>.fen-input{width:100%;min-height:40px;padding:8px 11px;border:1px solid var(--mall-color-border,#dcdfe6);border-radius:4px;font:inherit;color:inherit;background:#fff;box-sizing:border-box}.fen-input:focus{outline:2px solid var(--mall-color-brand,#a3202b);outline-offset:1px}.fen-input:disabled{background:#f5f7fa;color:#909399}</style>
