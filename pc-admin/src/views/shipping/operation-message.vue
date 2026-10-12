<script setup lang="ts">
import {ElButton} from 'element-plus'
defineProps<{error:string;needsRead:boolean;busy:boolean}>()
defineEmits<{reload:[]}>()
</script>
<template><p v-if="error" role="alert" class="error">{{error}} <ElButton v-if="needsRead" :disabled="busy" @click="$emit('reload')">重新读取配置</ElButton></p></template>
