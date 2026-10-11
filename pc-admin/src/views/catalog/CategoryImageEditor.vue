<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";
import AssetPicker from "../../shared/AssetPicker.vue";
import { api } from "../../api";
import { validateMediaFile, type Asset } from "./types";
const props = defineProps<{
  asset: Asset | null;
  secondary: boolean;
  targetKey: string;
  canUpload: boolean;
  disabled?: boolean;
}>();
const emit = defineEmits<{
  "update:asset": [Asset | null];
  busyChange: [boolean];
}>();
const error = ref(""),
  busy = ref(false),
  blocked = computed(() => !!props.disabled || busy.value);
let generation = 0;
function invalidate() {
  generation++;
  busy.value = false;
  error.value = "";
  emit("busyChange", false);
}
watch(() => [props.targetKey, props.secondary, props.canUpload], invalidate, {
  flush: "sync",
});
onUnmounted(invalidate);
function apply(asset: Asset) {
  if (props.disabled) return;
  if (
    asset.kind !== "IMAGE" ||
    !asset.width ||
    !asset.height ||
    (props.secondary
      ? asset.width <= asset.height
      : asset.width !== asset.height)
  ) {
    error.value = props.secondary
      ? "二级分类需要横向宽图。"
      : "一级分类需要正方形图片。";
    return;
  }
  error.value = "";
  emit("update:asset", { ...asset });
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement,
    file = input.files?.[0];
  input.value = "";
  if (!file || blocked.value || !props.canUpload) return;
  const token = ++generation;
  busy.value = true;
  emit("busyChange", true);
  error.value = "";
  try {
    const invalid = await validateMediaFile(file, "gallery");
    if (token !== generation || props.disabled || !props.canUpload) return;
    if (invalid) {
      error.value = invalid;
      return;
    }
    const data = new FormData();
    data.append("file", file);
    data.append("kind", "IMAGE");
    const asset = await api<Asset>("/assets", { method: "POST", body: data });
    if (token === generation && !props.disabled && props.canUpload)
      apply(asset);
  } catch {
    if (token === generation) error.value = "图片上传失败，请重试。";
  } finally {
    if (token === generation) {
      busy.value = false;
      emit("busyChange", false);
    }
  }
}
</script>
<template>
  <section class="category-imagery">
    <h4>{{ secondary ? "二级分类横幅" : "一级分类入口图" }}</h4>
    <p>
      {{
        secondary
          ? "用于分类商品列表顶部，建议 3:1 横向宽图，保持原比例。"
          : "用于顶部分类入口，正方形图片。"
      }}
      JPG / PNG，最大 10 MB。
    </p>
    <img
      v-if="asset"
      :src="asset.adminUrl"
      :class="{ banner: secondary }"
      alt="当前分类图片"
    /><span v-else>未上传{{ secondary ? "横幅" : "入口图" }}</span>
    <div class="store-actions">
      <label v-if="canUpload" class="secondary-button category-image-upload"
        >{{ busy ? "上传中…" : "上传图片"
        }}<input
          type="file"
          accept="image/jpeg,image/png"
          :disabled="blocked"
          @change="upload" /></label
      ><AssetPicker
        kind="IMAGE"
        :square="!secondary"
        :disabled="blocked"
        :target-key="targetKey"
        @select="apply"
      /><button
        v-if="asset"
        type="button"
        class="text-button"
        :disabled="blocked"
        @click="emit('update:asset', null)"
      >
        移除
      </button>
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>
<style scoped>
.category-image-upload {
  position: relative;
  display: inline-flex;
  align-items: center;
  overflow: hidden;
}
.category-image-upload input {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  max-width: none !important;
  opacity: 0;
  cursor: pointer;
}
.category-image-upload:focus-within {
  outline: 2px solid var(--mall-color-focus);
}
.category-imagery {
  margin: 16px 0;
}
.category-imagery img {
  display: block;
  max-width: 100%;
  width: 96px;
  height: auto;
  object-fit: contain;
}
.category-imagery img.banner {
  width: 360px;
}
.category-imagery input {
  max-width: 180px;
}
.category-imagery .store-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin: 12px 0;
}
</style>
