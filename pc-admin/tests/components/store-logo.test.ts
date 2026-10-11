import {afterEach,describe,expect,it,vi} from 'vitest'
import {mount,flushPromises} from '@vue/test-utils'
import StoreLogoEditor from '../../src/views/stores/StoreLogoEditor.vue'
import {api} from '../../src/api'
import {cropLogo,cropRect,validateLogoFile} from '../../src/views/stores/logo-crop'
vi.mock('../../src/api',()=>({api:vi.fn()}))
afterEach(()=>vi.restoreAllMocks())
describe('warehouse square logo',()=>{
 it('validates type and five MiB bound before decoding',()=>{
 expect(validateLogoFile(new File(['a'],'x.svg',{type:'image/svg+xml'}))).toContain('JPG')
 expect(validateLogoFile(new File([new Uint8Array(5*1024*1024+1)],'x.png',{type:'image/png'}))).toContain('5 MB')
 expect(validateLogoFile(new File(['a'],'x.webp',{type:'image/webp'}))).toBe('')
 expect(validateLogoFile(new File([],'x.png',{type:'image/png'}))).toContain('为空')
 })
 it('crops square within image with zoom and bounded position',()=>{
 expect(cropRect(800,400,1,0,0)).toEqual({x:200,y:0,size:400})
 expect(cropRect(800,400,2,100,-100)).toEqual({x:600,y:0,size:200})
 })
 it('removes only draft binding, keeps default preview accessible',async()=>{
 const w=mount(StoreLogoEditor,{props:{assetId:'old',url:'/old.png',name:'深圳仓',targetKey:'s1',canUpload:true}})
 await w.get('[data-test="remove-logo"]').trigger('click')
 expect(w.emitted('change')).toEqual([[null,'']]);expect(api).not.toHaveBeenCalled();w.unmount()
 })
 it('does not upload before crop confirmation and cancel preserves old logo',async()=>{
 vi.stubGlobal('createImageBitmap',vi.fn(async()=>({width:800,height:400,close(){}})))
 URL.createObjectURL=vi.fn(()=> 'blob:preview');URL.revokeObjectURL=vi.fn()
 const w=mount(StoreLogoEditor,{props:{assetId:'old',url:'/old.png',name:'深圳仓',targetKey:'s1',canUpload:true},global:{stubs:{ElDialog:{template:'<div><slot/><slot name="footer"/></div>'}}}})
 const input=w.get('input[type=file]');Object.defineProperty(input.element,'files',{value:[new File(['a'],'x.png',{type:'image/png'})]});await input.trigger('change');await flushPromises()
 expect(w.find('.crop-area img').attributes('src')).toBe('blob:preview');expect(api).not.toHaveBeenCalled()
 await w.get('[data-test="cancel-crop"]').trigger('click');expect(w.emitted('change')).toBeUndefined();w.unmount()
 })
 it('uploads400 square only after confirmation; retains binding on failure and ignores late result after warehouse change',async()=>{
 vi.stubGlobal('createImageBitmap',vi.fn(async()=>({width:800,height:400,close(){}})))
 URL.createObjectURL=vi.fn(()=> 'blob:preview');URL.revokeObjectURL=vi.fn()
 const draw=vi.fn();vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue({drawImage:draw} as unknown as CanvasRenderingContext2D)
 vi.spyOn(HTMLCanvasElement.prototype,'toBlob').mockImplementation(callback=>callback(new Blob(['crop'],{type:'image/png'})))
 const w=mount(StoreLogoEditor,{props:{assetId:'old',url:'/old.png',name:'深圳仓',targetKey:'s1',canUpload:true},global:{stubs:{ElDialog:{template:'<div><slot/><slot name="footer"/></div>'}}}})
 async function choose(){const input=w.get('input[type=file]');Object.defineProperty(input.element,'files',{value:[new File(['a'],'x.webp',{type:'image/webp'})],configurable:true});await input.trigger('change');await flushPromises()}
 vi.mocked(api).mockRejectedValueOnce(new Error('上传失败'))
 await choose();await w.findAll('button').find(b=>b.text()==='确认裁切并上传')!.trigger('click');await flushPromises()
 expect(w.text()).toContain('上传失败');expect(w.emitted('change')).toBeUndefined()
 vi.mocked(api).mockResolvedValueOnce({assetId:'new',kind:'IMAGE',width:400,height:400,adminUrl:'/new.png'})
 await w.findAll('button').find(b=>b.text()==='确认裁切并上传')!.trigger('click');await flushPromises()
 expect(w.emitted('change')).toEqual([['new','/new.png']]);expect(draw).toHaveBeenCalledWith(expect.anything(),200,0,400,400,0,0,400,400)
 const body=vi.mocked(api).mock.calls.at(-1)![1]!.body as FormData
 expect((body.get('file') as File).type).toBe('image/png');expect(body.get('kind')).toBe('IMAGE')
 let complete!:(value:unknown)=>void;vi.mocked(api).mockImplementationOnce(()=>new Promise(resolve=>complete=resolve))
 await choose();await w.findAll('button').find(b=>b.text()==='确认裁切并上传')!.trigger('click');await flushPromises();await w.setProps({targetKey:'s2'})
 complete({assetId:'late',kind:'IMAGE',width:400,height:400,adminUrl:'/late.png'});await flushPromises();expect(w.emitted('change')).toHaveLength(1);w.unmount()
 })

 it('supports drag and range controls and reports unreadable images',async()=>{
 vi.stubGlobal('createImageBitmap',vi.fn(async()=>({width:800,height:600,close(){}})))
 URL.createObjectURL=vi.fn(()=> 'blob:preview');URL.revokeObjectURL=vi.fn()
 const w=mount(StoreLogoEditor,{props:{name:'仓',targetKey:'s1',canUpload:true},global:{stubs:{ElDialog:{template:'<div><slot/><slot name="footer"/></div>'}}}})
 const input=w.get('input[type=file]');Object.defineProperty(input.element,'files',{value:[new File(['a'],'x.png',{type:'image/png'})],configurable:true});await input.trigger('change');await flushPromises()
 await w.findAll('input[type=range]')[0]!.setValue('2');await w.get('.crop-area').trigger('pointermove',{clientX:20,clientY:20});await w.get('.crop-area').trigger('pointerdown',{clientX:20,clientY:20});await w.get('.crop-area').trigger('pointermove',{clientX:40,clientY:40});await w.get('.crop-area').trigger('pointerup')
 expect(w.get('.crop-area img').attributes('style')).toContain('left:')
 await w.setProps({canUpload:false});expect(w.find('input[type=file]').exists()).toBe(false)
 await w.setProps({canUpload:true});vi.mocked(createImageBitmap).mockRejectedValueOnce(new Error('损坏图片'))
 await input.trigger('change');await flushPromises();expect(w.text()).toContain('损坏图片');w.unmount()
 })
 it('reports canvas and encoding failure without uploading',async()=>{
 vi.stubGlobal('createImageBitmap',vi.fn(async()=>({width:400,height:400,close(){}})))
 const file=new File(['x'],'x.png',{type:'image/png'})
 vi.spyOn(HTMLCanvasElement.prototype,'getContext').mockReturnValue(null)
 await expect(cropLogo(file,1,0,0)).rejects.toThrow('暂不支持')
 vi.mocked(HTMLCanvasElement.prototype.getContext).mockReturnValue({drawImage:vi.fn()} as unknown as CanvasRenderingContext2D)
 vi.spyOn(HTMLCanvasElement.prototype,'toBlob').mockImplementation(callback=>callback(null))
 await expect(cropLogo(file,1,0,0)).rejects.toThrow('裁切失败')
 })

})
