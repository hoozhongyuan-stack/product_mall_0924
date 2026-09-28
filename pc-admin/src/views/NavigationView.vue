<script setup lang="ts">
import type { Account } from '../api'
import SingletonConfigView from './SingletonConfigView.vue'
defineProps<{ account: Account }>()
</script>
<template><SingletonConfigView :account="account" domain="navigation" /></template>
