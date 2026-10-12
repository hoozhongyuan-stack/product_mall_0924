<script setup lang="ts">
import {ref,onBeforeUnmount} from 'vue'
import {api} from '../../api'
import {ElButton,ElCascader,ElTag,type CascaderProps} from 'element-plus'
import type {Region} from './types'
const props=defineProps<{modelValue:Region[];label:string;disabled?:boolean}>(),emit=defineEmits<{'update:modelValue':[value:Region[]]}>()
const choice=ref<string[]>([]),error=ref(''),regions=ref<Record<string,Region>>({});let epoch=0
const cascade:CascaderProps={checkStrictly:true,lazy:true,emitPath:true,async lazyLoad(node,resolve,reject){const current=epoch;error.value='';try{const parent=node.level===0?'':String(node.value),r=await api<{items:Region[]}>(`/shipping/regions?${parent?new URLSearchParams({parentAdcode:parent}):'keywords=中国'}`);if(current!==epoch)return;regions.value={...regions.value,...Object.fromEntries(r.items.map(region=>[region.code,region]))};resolve(r.items.map(region=>({value:region.code,label:region.name,leaf:region.level==='district'})))}catch(e){if(current===epoch){error.value=e instanceof Error?e.message:'地区查询失败，请重试。';reject?.()}}}}
function add(){const code=choice.value.at(-1),r=code?regions.value[code]:undefined;if(r&&!props.modelValue.some(i=>i.code===r.code)){emit('update:modelValue',[...props.modelValue,{code:r.code,name:r.name,level:r.level}]);choice.value=[]}}
onBeforeUnmount(()=>epoch++)
</script>
<template><fieldset class="region-picker" :disabled="disabled"><legend>{{label}}</legend><div class="inline-fields"><ElCascader v-model="choice" :props="cascade" :aria-label="`选择${label}省市区`" :disabled="disabled" clearable placeholder="按省、市、区县逐级选择，可选择任一级" style="flex:1;min-width:220px"/><ElButton :disabled="disabled||!choice.length" @click="add">添加地区</ElButton></div><p v-if="error" role="alert">{{error}}。关闭选择框后重新展开以重试。</p><div class="region-tags"><ElTag v-for="r in modelValue" :key="r.code" :closable="!disabled" @close="emit('update:modelValue',modelValue.filter(i=>i.code!==r.code))">{{r.name}}</ElTag><small v-if="!modelValue.length">未选择{{label==='可配送地区'?'，表示全国可配送':'，表示无禁配地区'}}。</small></div></fieldset></template>
