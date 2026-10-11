export interface MapPoint {getLat():number;getLng():number}
export interface MapEvent {lnglat?:MapPoint;target?:{getPosition():MapPoint}}
export interface MapInstance {on(name:string,callback:(event:MapEvent)=>void):void;destroy():void;setCity?(city:string):void;setCenter(position:number[]):void;add(marker:MarkerInstance):void}
export interface MarkerInstance {on(name:string,callback:(event:MapEvent)=>void):void;setPosition(position:number[]):void;setMap(map:MapInstance|null):void}
export interface AMapSDK {Map:new(container:HTMLElement,options:Record<string,unknown>)=>MapInstance;Marker:new(options:Record<string,unknown>)=>MarkerInstance}
export interface MapRuntime {jsApiKey:string;securityProxyUrl:string}
type MapWindow=Window&{AMap?:AMapSDK;_AMapSecurityConfig?:{serviceHost:string}}
let pending:Promise<AMapSDK>|null=null,signature='',sequence=0
export function loadAMap(config:MapRuntime):Promise<AMapSDK>{
 const host=new URL(config.securityProxyUrl,window.location.origin)
 if(host.origin!==window.location.origin||host.pathname!=='/api/v1/admin/stores/amap-proxy/_AMapService'||!!host.search||!!host.hash||!config.jsApiKey) return Promise.reject(new Error('地图配置不可用'))
 const next=JSON.stringify(config),target=window as MapWindow
 if(signature&&signature!==next)return Promise.reject(new Error('地图配置已变化，请刷新页面'))
 if(pending)return pending
 signature=next;target._AMapSecurityConfig={serviceHost:host.href}
 pending=new Promise((resolve,reject)=>{
  const script=document.createElement('script'),url=new URL('https://webapi.amap.com/maps')
  const callback=`__warehouseAMapReady${Date.now()}_${++sequence}`
  const callbacks=window as unknown as Record<string,unknown>
  const cleanup=()=>{window.clearTimeout(timer);script.onerror=null;delete callbacks[callback]}
  const fail=()=>{cleanup();script.remove();pending=null;signature='';reject(new Error('地图未能加载，请重试'))}
  const timer=window.setTimeout(fail,15000)
  callbacks[callback]=()=>{if(typeof target.AMap?.Map!=='function'||typeof target.AMap?.Marker!=='function'){fail();return}cleanup();resolve(target.AMap)}
  url.search=new URLSearchParams({v:'2.0',key:config.jsApiKey,callback}).toString();script.src=url.href;script.async=true
  script.onerror=fail
  document.head.append(script)
 })
 return pending
}
