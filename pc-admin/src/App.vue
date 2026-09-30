<script setup lang="ts">
import { computed, onMounted, onUnmounted, provide, ref, watch } from 'vue'
import { ElConfigProvider, ElMessage } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { api, type Account } from './api'
import { useRoute, useRouter } from 'vue-router'
import { authorizedSections, canAccessRoute } from './navigation'
import PasswordDialog from './components/PasswordDialog.vue'

const account = ref<Account | null>(null)
provide('admin-account', account)
const loginName = ref('')
const password = ref('')
const busy = ref(false)
const loading = ref(true)
const message = ref('')
const passwordNotice = ref('')
const route = useRoute()
const router = useRouter()
const menuOpen = ref(false)
const passwordDialogOpen = ref(false)
const rememberLogin = ref(false)
const preferenceNotice = ref('')
const rememberedLoginKey = 'mall.admin.remembered-login'
const pageTitle = computed(() => String(route.meta.title || '概览'))
const canOpenPage = computed(() => canAccessRoute(route.meta, account.value?.permissionCodes || []))
const sections = computed(() => authorizedSections(router, account.value?.permissionCodes || []))
const currentLink = computed(() => sections.value.flatMap(section => section.links.map(link => ({ ...link, section: section.name })))
  .filter(link => route.path === link.path || (link.path !== '/' && route.path.startsWith(`${link.path}/`)))
  .sort((a, b) => b.path.length - a.path.length)[0])
const currentSection = computed(() => sections.value.find(section => section.name === currentLink.value?.section))
watch(() => route.fullPath, () => { menuOpen.value = false })
watch(rememberLogin, value => {
  if (value) return
  try { localStorage.removeItem(rememberedLoginKey) }
  catch { preferenceNotice.value = '浏览器未允许保存登录偏好；不会影响本次登录。' }
})

function sessionExpired() {
  if (!account.value) return
  account.value = null
  passwordDialogOpen.value = false
  password.value = ''
  message.value = '会话已失效，请重新登录。'
}

async function passwordChanged() {
  passwordDialogOpen.value = false
  account.value = null
  password.value = ''
  message.value = ''
  passwordNotice.value = '密码已修改，请使用新密码重新登录。'
  await router.push('/')
}

onMounted(async () => {
  window.addEventListener('admin-session-expired', sessionExpired)
  try {
    loginName.value = localStorage.getItem(rememberedLoginKey) || ''
    rememberLogin.value = Boolean(loginName.value)
  } catch { preferenceNotice.value = '浏览器未允许保存登录偏好；不会影响本次登录。' }
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
  passwordNotice.value = ''
  try {
    account.value = await api<Account>('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ loginName: loginName.value, password: password.value }),
    })
    try {
      if (rememberLogin.value) localStorage.setItem(rememberedLoginKey, loginName.value)
      else localStorage.removeItem(rememberedLoginKey)
    } catch { preferenceNotice.value = '浏览器未允许保存登录偏好；不会影响本次登录。' }
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
  <ElConfigProvider :locale="zhCn">
  <main v-if="loading" class="loading" aria-live="polite">正在检查登录状态…</main>
  <main v-else-if="!account" class="login-layout">
    <section class="intro" aria-label="商城管理后台">
      <div class="login-brand">商城管理后台</div>
      <div class="login-message"><h1>让日常经营<br>清晰、有序</h1><p>商品、订单、库存与会员运营，<br>统一管理工作入口。</p></div>
      <div class="login-preview" aria-hidden="true"><span>运营工作台</span><strong>经营概览 · 业务待办</strong><div><i></i><i></i><i></i></div></div>
      <p class="login-brand-foot">B2C 商城管理平台 · 安全登录</p>
    </section>
    <section class="login-form-panel">
      <div class="login-card" aria-labelledby="login-title">
        <h2 id="login-title">欢迎登录</h2>
        <p class="help">登录商城管理后台，继续处理店铺业务</p>
        <form @submit.prevent="signIn">
          <label for="login-name">账号</label>
          <el-input id="login-name" v-model="loginName" autocomplete="username" placeholder="输入登录账号" size="large" :disabled="busy" />
          <label for="login-password">密码</label>
          <el-input id="login-password" v-model="password" type="password" autocomplete="current-password" show-password placeholder="输入密码" size="large" :disabled="busy" />
          <div class="login-options"><label class="remember-account"><input v-model="rememberLogin" type="checkbox" :disabled="busy">记住账号</label><span>仅记住账号，不保存密码</span></div>
          <p v-if="message" class="error" role="alert">{{ message }}</p>
          <p v-if="passwordNotice" class="help-text" role="status">{{ passwordNotice }}</p>
          <p v-if="preferenceNotice" class="help-text" role="status">{{ preferenceNotice }}</p>
          <el-button type="primary" native-type="submit" size="large" :loading="busy" class="submit">登录</el-button>
        </form>
        <p class="login-contact">账号问题请联系主账号管理员</p>
        <p class="footnote">连续登录失败会暂时限制登录；闲置 30 分钟后需要重新登录。</p>
      </div>
    </section>
  </main>
  <div v-else class="admin-shell">
    <a class="skip-link" href="#main-content">跳到主要内容</a>
    <header class="topbar">
      <div class="shell-brand"><button type="button" class="menu-toggle" aria-label="打开导航菜单" :aria-expanded="menuOpen" @click="menuOpen = true"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M4 12h16M4 18h16" /></svg></button><span>商城管理后台</span></div>
      <div class="identity"><span>{{ account.displayName }}<small>{{ account.kind === 'OWNER' ? '主账号' : '子账号' }}</small></span><el-button text :disabled="busy" @click="passwordDialogOpen = true">修改密码</el-button><el-button text :loading="busy" @click="signOut">退出</el-button></div>
    </header>
    <aside class="sidebar">
      <nav class="nav" aria-label="后台主导航">
        <RouterLink v-for="section in sections" :key="section.name" :to="section.links[0]!.path" class="nav-item" :class="{ current: currentSection?.name === section.name }" :aria-current="currentSection?.name === section.name ? 'true' : undefined">{{ section.name }}</RouterLink>
      </nav>
    </aside>
    <el-drawer v-model="menuOpen" title="商城管理后台" direction="ltr" size="min(320px, 88vw)" class="mobile-navigation" append-to-body>
      <nav class="nav" aria-label="移动后台导航">
        <RouterLink v-for="section in sections" :key="section.name" :to="section.links[0]!.path" class="nav-item" :class="{ current: currentSection?.name === section.name }" :aria-current="currentSection?.name === section.name ? 'true' : undefined" @click="section.links[0]!.path === route.path && (menuOpen = false)">{{ section.name }}</RouterLink>
      </nav>
    </el-drawer>
    <main id="main-content" class="workspace" tabindex="-1">
      <div class="workspace-navigation">
        <nav class="breadcrumbs" aria-label="页面位置"><span v-if="currentSection">{{ currentSection.name }}</span><template v-if="currentLink && currentLink.path !== route.path"><span aria-hidden="true">/</span><RouterLink :to="currentLink.path">{{ currentLink.title }}</RouterLink></template><span aria-hidden="true">/</span><span aria-current="page">{{ pageTitle }}</span></nav>
        <nav v-if="currentSection?.hasSubnavigation" class="secondary-nav" :aria-label="`${currentSection.name}功能`">
          <RouterLink v-for="link in currentSection.links" :key="link.path" :to="link.path" :class="{ active: currentLink?.path === link.path }" :aria-current="currentLink?.path === link.path ? 'page' : undefined">{{ link.title }}</RouterLink>
        </nav>
      </div>
      <section v-if="!canOpenPage" class="page-content" role="alert"><h1>无权访问{{ pageTitle }}</h1><p>当前账号没有此页面的读取权限。</p><RouterLink to="/" class="text-link">返回概览</RouterLink></section>
      <RouterView v-else v-slot="{ Component }"><component :is="Component" :account="account" /></RouterView>
    </main>
    <PasswordDialog v-if="passwordDialogOpen" @close="passwordDialogOpen = false" @changed="passwordChanged" />
  </div>
  </ElConfigProvider>
</template>
