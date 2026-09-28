<script setup lang="ts">
import { computed, onMounted, onUnmounted, provide, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, type Account } from './api'
import { useRoute, useRouter } from 'vue-router'

const account = ref<Account | null>(null)
provide('admin-account', account)
const loginName = ref('')
const password = ref('')
const busy = ref(false)
const loading = ref(true)
const message = ref('')
const route = useRoute()
const router = useRouter()
const pageTitle = computed(() => String(route.meta.title || '概览'))
const requiredPermission = computed(() => String(route.meta.permission || ''))
const canOpenPage = computed(() => {
  const any = route.meta.permissionsAny as string[] | undefined
  if (any?.length) return any.some(code => account.value?.permissionCodes.includes(code))
  return !requiredPermission.value || !!account.value?.permissionCodes.includes(requiredPermission.value)
})

function sessionExpired() {
  account.value = null
  password.value = ''
  message.value = '会话已失效，请重新登录。'
}

onMounted(async () => {
  window.addEventListener('admin-session-expired', sessionExpired)
  try {
    account.value = await api<Account>('/me')
  } catch {
    account.value = null
  } finally {
    loading.value = false
  }
})
onUnmounted(() => window.removeEventListener('admin-session-expired', sessionExpired))

async function signIn() {
  if (!loginName.value || !password.value) {
    message.value = '请输入登录名和密码。'
    return
  }
  busy.value = true
  message.value = ''
  try {
    account.value = await api<Account>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ loginName: loginName.value, password: password.value }),
    })
    password.value = ''
    await router.push('/')
    ElMessage.success('登录成功')
  } catch (error) {
    message.value = error instanceof Error ? error.message : '登录失败，请重试。'
  } finally {
    busy.value = false
  }
}

async function signOut() {
  busy.value = true
  try {
    await api('/auth/logout', { method: 'POST', body: '{}' })
    account.value = null
    password.value = ''
    await router.push('/')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '退出失败')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <main v-if="loading" class="loading" aria-live="polite">正在检查登录状态…</main>
  <main v-else-if="!account" class="login-layout">
    <section class="intro">
      <p class="eyebrow">PRODUCT MALL · 管理后台</p>
      <h1>把每项操作，交给有权限的人。</h1>
      <p>产品研发中 · 库存基础能力</p>
    </section>
    <section class="login-card" aria-labelledby="login-title">
      <p class="eyebrow">安全登录</p>
      <h2 id="login-title">欢迎回来</h2>
      <p class="help">使用主账号或授权子账号进入后台。</p>
      <form @submit.prevent="signIn">
        <label for="login-name">登录名</label>
        <el-input id="login-name" v-model="loginName" autocomplete="username" placeholder="请输入登录名" size="large" />
        <label for="login-password">密码</label>
        <el-input id="login-password" v-model="password" type="password" autocomplete="current-password" show-password placeholder="请输入密码" size="large" />
        <p v-if="message" class="error" role="alert">{{ message }}</p>
        <el-button type="primary" native-type="submit" size="large" :loading="busy" class="submit">登录后台</el-button>
      </form>
      <p class="footnote">连续失败会暂时限制登录；闲置 30 分钟后需要重新登录。</p>
    </section>
  </main>
  <div v-else class="admin-shell">
    <aside class="sidebar">
      <div class="brand">商城管理后台 <small>阶段 D · 售后与会员</small></div>
      <nav class="nav" aria-label="后台导航">
        <RouterLink to="/" class="nav-item">概览</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('account.read')" to="/accounts" class="nav-item">子账号</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('permission.read')" to="/permission-groups" class="nav-item">权限组</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('audit.read')" to="/audit-logs" class="nav-item">操作日志</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('notification.read')" to="/subscription-messages" class="nav-item">订阅消息</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('notification.read')" to="/subscription-message-tasks" class="nav-item">消息任务</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('asset.read') || account.permissionCodes.includes('asset.upload')" to="/assets" class="nav-item">素材中心</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('catalog.read')" to="/catalog" class="nav-item">商品管理</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('member.read')" to="/members" class="nav-item">会员</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('member.rules.read') || account.permissionCodes.includes('member.rules.manage')" to="/members/rules" class="nav-item">等级与积分规则</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('coupon.read')" to="/coupons" class="nav-item">优惠券活动</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('exchange.read')" to="/exchange-offers" class="nav-item">积分商城商品</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('order.read')" to="/orders" class="nav-item">订单管理</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('aftersale.read')" to="/aftersales" class="nav-item">售后管理</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('fulfillment.redeem')" to="/redemptions" class="nav-item">到店核销</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('fulfillment.settings.manage')" to="/fulfillment/settings" class="nav-item">履约设置</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('payment.settings.manage')" to="/store/payments" class="nav-item">付款配置</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('inventory.read')" to="/inventory" class="nav-item">库存管理</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('page.read')" to="/pages/home" class="nav-item">首页装修</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('page.read')" to="/pages/micro" class="nav-item">独立微页面</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('startup.read')" to="/store/info" class="nav-item">店铺信息</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('navigation.read')" to="/store/navigation" class="nav-item">底部导航</RouterLink>
        <RouterLink v-if="account.permissionCodes.includes('customer_service.read')" to="/store/customer-service" class="nav-item">客服悬浮入口</RouterLink>
      </nav>
    </aside>
    <div class="workspace">
      <header class="topbar">
        <span>{{ pageTitle }}</span>
        <div class="identity"><span>{{ account.displayName }} · {{ account.kind === 'OWNER' ? '主账号' : '子账号' }}</span><el-button text :loading="busy" @click="signOut">退出</el-button></div>
      </header>
      <section v-if="!canOpenPage" class="page-content" role="alert">
        <h1>无权访问{{ pageTitle }}</h1>
        <p>当前账号没有此页面的读取权限。</p>
        <RouterLink to="/" class="text-link">返回概览</RouterLink>
      </section>
      <RouterView v-else v-slot="{ Component }">
        <component :is="Component" :account="account" />
      </RouterView>
    </div>
  </div>
</template>
