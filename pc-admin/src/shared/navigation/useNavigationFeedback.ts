import { shallowRef } from 'vue'
import { isNavigationFailure, NavigationFailureType } from 'vue-router'
import type { RouteLocationNormalized, RouteMeta, Router } from 'vue-router'

export interface NavigationFeedback {
  id: number
  target: string
  title: string
  phase: 'loading' | 'slow' | 'error' | 'blocked' | 'recovering'
}

/** Presentation only: every attempt still runs the router's original guards. */
export function useNavigationFeedback(router: Router, authenticated: () => boolean, allowed: (meta: RouteMeta) => boolean) {
  const state = shallowRef<NavigationFeedback | null>(null)
  const intents = new Map<string, number>()
  const routes = new WeakMap<RouteLocationNormalized, { id: number; manual: boolean }>()
  let sequence = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  let active = true
  function stopTimer() { if (timer !== undefined) clearTimeout(timer); timer = undefined }
  function clear() { stopTimer(); sequence++; state.value = null; intents.clear() }
  function begin(target: string, title: string) {
    stopTimer()
    const id = ++sequence
    state.value = { id, target, title, phase: 'loading' }
    timer = setTimeout(() => {
      if (active && state.value?.id === id && state.value.phase === 'loading') state.value = { ...state.value, phase: 'slow' }
    }, 3000)
    return id
  }
  function finish(id: number, failed = false) {
    if (!active || state.value?.id !== id) return
    stopTimer()
    if (failed) state.value = { ...state.value, phase: 'error' }
    else state.value = null // Success, cancelled/aborted guards, and duplicated navigation are settled.
  }
  async function navigate(target: string) {
    if (!active || !authenticated()) { clear(); return }
    const destination = router.resolve(target)
    const id = begin(destination.fullPath, String(destination.meta.title || '目标页面'))
    if (!allowed(destination.meta)) { stopTimer(); state.value = { ...state.value!, phase: 'blocked' }; return }
    intents.set(destination.fullPath, id)
    try { await router.push(destination.fullPath); finish(id) }
    catch { finish(id, true) }
    finally { if (intents.get(destination.fullPath) === id) intents.delete(destination.fullPath) }
  }
  const removeBefore = router.beforeEach(to => {
    if (!active || !authenticated()) return
    const intent = intents.get(to.fullPath) ?? (to.redirectedFrom ? intents.get(to.redirectedFrom.fullPath) : undefined)
    const id = intent ?? begin(to.fullPath, String(to.meta.title || '目标页面'))
    routes.set(to, { id, manual: intent !== undefined })
  })
  const removeAfter = router.afterEach(to => {
    const attempt = routes.get(to)
    if (attempt && !attempt.manual) finish(attempt.id)
  })
  const removeError = router.onError((_error, to) => {
    const attempt = routes.get(to)
    if (attempt && !attempt.manual) finish(attempt.id, true)
  })
  function retry() { const target = state.value?.target; if (target) return navigate(target) }
  async function safeRefresh() {
    const previous = state.value
    if (!active || previous?.phase !== 'error') return
    if (!authenticated()) { clear(); return }
    const destination = router.resolve(previous.target)
    if (!allowed(destination.meta)) { state.value = { ...previous, phase: 'blocked' }; return }
    const href = destination.href
    // A document navigation is explicit and same-origin. It clears failed module
    // fetches only after the original page has allowed leaving through its guards.
    if (new URL(href, window.location.href).origin !== window.location.origin) return
    const home = router.resolve('/')
    const id = ++sequence
    stopTimer()
    state.value = { ...previous, id, phase: 'recovering' }
    intents.set(home.fullPath, id)
    try {
      const failure = await router.push(home.fullPath)
      if (!active || state.value?.id !== id) return
      if (!authenticated()) { clear(); return }
      if (!allowed(destination.meta)) { state.value = { ...state.value, phase: 'blocked' }; return }
      if ((failure && !isNavigationFailure(failure, NavigationFailureType.duplicated)) || router.currentRoute.value.fullPath !== home.fullPath) { finish(id, true); return }
      window.location.assign(href)
    } catch { finish(id, true) }
    finally { if (intents.get(home.fullPath) === id) intents.delete(home.fullPath) }
  }
  function dispose() { active = false; clear(); removeBefore(); removeAfter(); removeError() }
  return { state, navigate, retry, safeRefresh, clear, dispose }
}
