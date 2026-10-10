import { onMounted, onUnmounted, ref } from 'vue'
// Keep displayed lifecycle and button state fresh while a campaign remains open.
export function useCampaignClock() {
  const now = ref(Date.now())
  let timer: ReturnType<typeof setInterval> | undefined
  const refresh = () => { now.value = Date.now() }
  onMounted(() => {
    timer = setInterval(refresh, 1000)
    document.addEventListener('visibilitychange', refresh)
  })
  onUnmounted(() => {
    clearInterval(timer)
    document.removeEventListener('visibilitychange', refresh)
  })
  return now
}
