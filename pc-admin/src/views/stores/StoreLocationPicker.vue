<script setup lang="ts">
import SharedLocationPicker from '../../shared/maps/StoreLocationPicker.vue'
import type {LocationItem} from '../../shared/maps/StoreLocationPicker.vue'
export type {LocationItem} from '../../shared/maps/StoreLocationPicker.vue'
defineProps<{city:string;latitude:number|null;longitude:number|null;contextKey:string;disabled:boolean}>()
defineEmits<{select:[item:LocationItem]}>()
</script>
<template><SharedLocationPicker v-bind="$props" @select="$emit('select',$event)" /></template>
