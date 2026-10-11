<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { api, type Account } from "../api";
const props = defineProps<{ account: Account }>();
const recordNo = ref(""),
  revision = ref<number | null>(null),
  saved = ref(""),
  busy = ref(false),
  loading = ref(false),
  error = ref(""),
  notice = ref(""),
  needsRead = ref(false);
const readable = computed(() =>
  props.account.permissionCodes.includes("navigation.read"),
);
const permitted = computed(() =>
  props.account.permissionCodes.includes("navigation.edit"),
);
let generation = 0;
async function load() {
  if (!readable.value || busy.value) return;
  const token = ++generation;
  loading.value = true;
  error.value = "";
  try {
    const result = await api<{ revision: number; recordNo: string }>(
      "/storefront/filing",
    );
    if (token === generation) {
      recordNo.value = result.recordNo;
      saved.value = result.recordNo;
      revision.value = result.revision;
      needsRead.value = false;
    }
  } catch {
    if (token === generation) {
      revision.value = null;
      error.value = "备案号读取失败，请重新读取。";
    }
  } finally {
    if (token === generation) loading.value = false;
  }
}
async function save() {
  if (
    !permitted.value ||
    busy.value ||
    loading.value ||
    revision.value === null ||
    needsRead.value ||
    recordNo.value.trim() === saved.value
  )
    return;
  const token = ++generation;
  busy.value = true;
  error.value = "";
  try {
    const result = await api<{ revision: number; recordNo: string }>(
      "/storefront/filing",
      {
        method: "PUT",
        body: JSON.stringify({
          expectedRevision: revision.value,
          recordNo: recordNo.value.trim(),
        }),
      },
    );
    if (token === generation) {
      recordNo.value = result.recordNo;
      saved.value = result.recordNo;
      revision.value = result.revision;
      notice.value = "备案号已保存，个人中心将同步展示；留空时隐藏。";
    }
  } catch {
    if (token === generation) {
      error.value = "保存结果未确认，请重新读取后核对。";
      needsRead.value = true;
    }
  } finally {
    if (token === generation) busy.value = false;
  }
}
watch(
  () =>
    JSON.stringify([props.account.accountId, props.account.permissionCodes]),
  () => {
    generation++;
    busy.value = false;
    loading.value = false;
    recordNo.value = "";
    saved.value = "";
    revision.value = null;
    error.value = "";
    notice.value = "";
    void load();
  },
);
onMounted(load);
onUnmounted(() => {
  generation++;
});
</script>
<template>
  <section v-if="readable" class="panel filing-settings">
    <h2>个人中心备案号</h2>
    <p>同步显示于小程序个人中心底部。请填写已取得的正式备案号，留空则隐藏。</p>
    <p v-if="loading" role="status">正在读取备案号…</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <form @submit.prevent="save">
      <label for="global-record-no"
        >备案号<input
          id="global-record-no"
          v-model="recordNo"
          maxlength="100"
          :disabled="!permitted || busy || loading || revision === null"
      /></label>
      <div class="actions">
        <button
          type="button"
          class="secondary-button"
          :disabled="busy || loading"
          @click="load"
        >
          重新读取</button
        ><button
          class="primary-button"
          :disabled="
            !permitted ||
            busy ||
            loading ||
            revision === null ||
            needsRead ||
            recordNo.trim() === saved
          "
        >
          {{ busy ? "保存中…" : "保存备案号" }}
        </button>
      </div>
    </form>
  </section>
</template>
<style scoped>
.filing-settings {
  margin-top: 24px;
  padding: 24px;
}
.filing-settings input {
  max-width: 480px;
  display: block;
}
.actions {
  display: flex;
  gap: 12px;
  margin-top: 16px;
}
</style>
