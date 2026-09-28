<script setup lang="ts">
import type { Account } from '../api'
defineProps<{ account: Account }>()
</script>

<template>
  <section class="page-content dashboard">
    <h1>你好，{{ account.displayName }}</h1>
    <p>当前账号已连接到本机 Django 服务。账号和权限操作已开放。</p>
    <div class="status-card">
      <div><strong>账号状态</strong><span>已启用</span></div>
      <div><strong>登录名</strong><span>{{ account.loginName }}</span></div>
      <div><strong>已授权操作</strong><span>{{ account.permissionCodes.length }} 项</span></div>
    </div>
    <p v-if="account.permissionCodes.length === 0" class="empty-state">当前没有业务操作权限，请联系主账号分配权限组。</p>
  </section>
</template>
