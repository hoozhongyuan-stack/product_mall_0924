<script setup lang="ts">
import {ref,onBeforeUnmount} from 'vue'
import {ElSelect,ElOption} from 'element-plus'
import {api} from '../../api'
const props=withDefaults(defineProps<{modelValue:string[];disabled?:boolean;multiple?:boolean;label?:string}>(),{multiple:true,label:'活动适用前置仓'}),emit=defineEmits<{'update:modelValue':[string[]]}>()
const items=ref<{id:string;name:string}[]>([]),loading=ref(false),error=ref('');let epoch=0
async function search(query=''){const current=++epoch;loading.value=true;error.value='';try{const result=await api<{items:{id:string;name:string}[]}>(`/shipping/stores?${new URLSearchParams({search:query,page:'1',pageSize:'50'})}`);if(current===epoch)items.value=result.items}catch(e){if(current===epoch)error.value=e instanceof Error?e.message:'前置仓读取失败，请重试。'}finally{if(current===epoch)loading.value=false}}
onBeforeUnmount(()=>epoch++)
</script>
<template><label>{{label}}<ElSelect :model-value="multiple?modelValue:modelValue[0]" :disabled="disabled" :multiple="multiple" filterable remote :remote-method="search" :loading="loading" :aria-label="label" :placeholder="multiple?'留空表示全部前置仓':'选择前置仓'" @visible-change="v=>{if(v&&!items.length)search()}" @update:model-value="emit('update:modelValue',Array.isArray($event)?$event:$event?[$event]:[])"><ElOption v-for="s in items" :key="s.id" :label="s.name" :value="s.id"/></ElSelect><small v-if="multiple">留空表示全部；选择后仅这些前置仓参加活动。</small><p v-if="error" role="alert">{{error}}</p></label></template>
