<script setup lang="ts">
import {computed,onUnmounted,ref,watch} from 'vue'
import {ElButton,ElDialog} from 'element-plus'
import {api} from '../../api'
import type {Asset} from '../../shared/media'
import {cropLogo,cropRect,validateLogoFile} from './logo-crop'
const props=defineProps<{assetId?:string|null;url?:string|null;name:string;targetKey:string;canUpload:boolean;disabled?:boolean}>()
const emit=defineEmits<{change:[assetId:string|null,url:string];busyChange:[value:boolean]}>()
const source=ref<File|null>(null),preview=ref(''),error=ref(''),busy=ref(false),zoom=ref(1),x=ref(0),y=ref(0),width=ref(0),height=ref(0)
let generation=0
let drag:{x:number;y:number;startX:number;startY:number}|null=null
function startDrag(event:PointerEvent){if(busy.value)return;drag={x:event.clientX,y:event.clientY,startX:x.value,startY:y.value};(event.currentTarget as HTMLElement).setPointerCapture?.(event.pointerId)}
function moveDrag(event:PointerEvent){if(!drag)return;const rect=cropRect(width.value,height.value,zoom.value,x.value,y.value);const scale=240/rect.size;const offset=(size:number)=>size>0?200/(size*scale):0;x.value=Math.max(-100,Math.min(100,drag.startX-(event.clientX-drag.x)*offset(width.value-rect.size)));y.value=Math.max(-100,Math.min(100,drag.startY-(event.clientY-drag.y)*offset(height.value-rect.size)))}
function endDrag(){drag=null}
const blocked=computed(()=>props.disabled||busy.value),open=computed(()=>!!source.value)
const imageStyle=computed(()=>{const rect=cropRect(width.value,height.value,zoom.value,x.value,y.value);return {width:`${width.value/rect.size*100}%`,height:`${height.value/rect.size*100}%`,left:`${-rect.x/rect.size*100}%`,top:`${-rect.y/rect.size*100}%`}})
function setBusy(value:boolean){busy.value=value;emit('busyChange',value)}
function clear(){drag=null;source.value=null;if(preview.value)URL.revokeObjectURL(preview.value);preview.value=''}
function invalidate(){generation++;clear();setBusy(false);error.value=''}
watch(()=>[props.targetKey,props.canUpload],invalidate,{flush:'sync'})
onUnmounted(invalidate)
function cancel(){if(busy.value)return;generation++;clear();error.value=''}
async function select(event:Event){
 const input=event.target as HTMLInputElement,file=input.files?.[0];input.value=''
 if(!file||blocked.value||!props.canUpload)return
 const token=++generation;error.value=validateLogoFile(file);if(error.value)return
 setBusy(true)
 try{const image=await createImageBitmap(file);const dimensions={width:image.width,height:image.height};image.close()
 if(token!==generation||props.disabled||!props.canUpload)return
 if(!dimensions.width||!dimensions.height||dimensions.width*dimensions.height>40_000_000)throw new Error('图片尺寸过大或无效，请使用不超过 4000 万像素的图片。')
 clear();width.value=dimensions.width;height.value=dimensions.height;zoom.value=1;x.value=0;y.value=0;source.value=file;preview.value=URL.createObjectURL(file)
 }catch(reason){if(token===generation)error.value=reason instanceof Error?reason.message:'图片读取失败，请重新选择。'}finally{if(token===generation)setBusy(false)}
}
async function confirm(){
 if(!source.value||blocked.value||!props.canUpload)return
 const token=++generation,expectedAsset=props.assetId,file=source.value
 const current=()=>token===generation&&!props.disabled&&props.canUpload&&expectedAsset===props.assetId
 setBusy(true);error.value=''
 try{const cropped=await cropLogo(file,zoom.value,x.value,y.value);if(!current())return
 const body=new FormData();body.append('file',cropped);body.append('kind','IMAGE')
 const asset=await api<Asset>('/assets',{method:'POST',body});if(!current())return
 if(asset.kind!=='IMAGE'||asset.width!==asset.height||!asset.width)throw new Error('上传结果不是正方形图片，请重试。')
 emit('change',asset.assetId,asset.adminUrl);clear()
 }catch(reason){if(current())error.value=reason instanceof Error?reason.message:'Logo 上传失败，原 Logo 已保留。'}finally{if(token===generation)setBusy(false)}
}
function remove(){if(!blocked.value)emit('change',null,'')}
</script>
<template><section class="warehouse-logo"><h2>前置仓 Logo</h2><div class="logo-layout"><div class="logo-square"><img v-if="url" :src="url" alt="当前前置仓 Logo"><span v-else aria-label="默认前置仓图标">仓</span></div><div><p>正方形图片，用于小程序首页店招和前置仓中心。</p><p class="help-text">JPG / PNG / WebP，最大 5 MB；裁切后为 400 × 400 像素。</p><label v-if="canUpload" class="secondary-button logo-upload">{{busy?'正在处理…':url?'更换 Logo':'上传 Logo'}}<input type="file" accept="image/jpeg,image/png,image/webp" :disabled="blocked" @change="select"></label><ElButton v-if="assetId" data-test="remove-logo" :disabled="blocked" @click="remove">移除</ElButton><p v-if="canUpload" class="help-text">保存前置仓后生效；未上传时展示默认图标。</p></div><div class="logo-live"><small>小程序首页 / 前置仓中心预览</small><div><img v-if="url" :src="url" alt="店招 Logo 预览"><span v-else>仓</span><strong>{{name||'前置仓名称'}}</strong></div></div></div><p v-if="error" role="alert" class="error">{{error}}</p><ElDialog :model-value="open" title="裁切正方形 Logo" width="480px" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @close="cancel"><div class="crop-area" @pointerdown="startDrag" @pointermove="moveDrag" @pointerup="endDrag" @pointercancel="endDrag"><img :src="preview" :style="imageStyle" alt="待裁切图片" draggable="false"></div><label class="crop-control">缩放<input v-model.number="zoom" type="range" min="1" max="3" step="0.01" :disabled="busy"></label><label class="crop-control">左右移动<input v-model.number="x" type="range" min="-100" max="100" :disabled="busy"></label><label class="crop-control">上下移动<input v-model.number="y" type="range" min="-100" max="100" :disabled="busy"></label><p class="help-text">调整图片位置，确认后上传。取消不会替换原 Logo。</p><template #footer><ElButton data-test="cancel-crop" :disabled="busy" @click="cancel">取消</ElButton><ElButton type="primary" :loading="busy" :disabled="disabled||!canUpload" @click="confirm">确认裁切并上传</ElButton></template></ElDialog></section></template>
<style scoped>
.warehouse-logo{grid-column:1/-1;margin:8px 0 16px}.warehouse-logo h2{font-size:16px}.logo-layout{display:flex;align-items:center;gap:20px;flex-wrap:wrap}.logo-square{width:96px;height:96px;display:grid;place-items:center;border:1px solid var(--mall-color-border);border-radius:8px;background:var(--mall-color-brand-soft);color:var(--mall-color-brand)}.logo-square img{width:100%;height:100%;object-fit:contain}.logo-layout p{margin:8px 0}.logo-upload{position:relative;display:inline-flex}.logo-upload input{position:absolute;inset:0;opacity:0;width:100%;cursor:pointer}.logo-upload:focus-within{outline:2px solid var(--mall-color-focus)}.logo-live{margin-left:auto;padding:16px;border:1px solid var(--mall-color-border);border-radius:8px}.logo-live small{color:var(--mall-color-muted)}.logo-live div{display:flex;gap:12px;align-items:center;margin-top:12px}.logo-live img,.logo-live span{width:44px;height:44px;object-fit:contain;background:var(--mall-color-brand-soft);display:grid;place-items:center;color:var(--mall-color-brand)}.crop-area{position:relative;overflow:hidden;width:240px;height:240px;margin:0 auto 20px;background:#f4f5f5;touch-action:none;cursor:grab}.crop-area img{position:absolute;max-width:none}.crop-control{display:flex;gap:16px;align-items:center;margin:12px 0}.crop-control input{flex:1;min-width:0}.warehouse-logo :deep(.el-dialog){max-width:calc(100vw - 32px)}@media(max-width:600px){.logo-live{margin-left:0;width:100%}}
</style>
