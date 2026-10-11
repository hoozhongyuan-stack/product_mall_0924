/** Crop geometry stays in decoded source pixels; position is -100..100. */
export function cropRect(width:number,height:number,zoom:number,x:number,y:number){
 const size=Math.min(width,height)/Math.max(1,Math.min(3,zoom))
 const position=(value:number)=>Math.max(0,Math.min(1,(value+100)/200))
 return {x:(width-size)*position(x),y:(height-size)*position(y),size}
}
export function validateLogoFile(file:File){
 if(!['image/jpeg','image/png','image/webp'].includes(file.type))return '请选择 JPG、PNG 或 WebP 图片。'
 if(file.size>5*1024*1024)return 'Logo 图片不能超过 5 MB。'
 if(!file.size)return '图片为空，请重新选择。'
 return ''
}
export async function cropLogo(file:File,zoom:number,x:number,y:number):Promise<File>{
 const image=await createImageBitmap(file)
 try{
 const rect=cropRect(image.width,image.height,zoom,x,y),canvas=document.createElement('canvas')
 canvas.width=400;canvas.height=400
 const context=canvas.getContext('2d');if(!context)throw new Error('浏览器暂不支持图片裁切，请换用新版浏览器。')
 context.drawImage(image,rect.x,rect.y,rect.size,rect.size,0,0,400,400)
 const blob=await new Promise<Blob>((resolve,reject)=>canvas.toBlob(value=>value?resolve(value):reject(new Error('图片裁切失败，请重新选择。')),'image/png'))
 return new File([blob],'warehouse-logo.png',{type:'image/png'})
 }finally{image.close()}
}
