function coordinate(value,limit){if(typeof value!=='number'&&typeof value!=='string')return null;if(typeof value==='string'&&!value.trim())return null;const n=Number(value);return Number.isFinite(n)&&Math.abs(n)<=limit?n:null}
function located(a){return !!a&&coordinate(a.latitude,90)!==null&&coordinate(a.longitude,180)!==null}
function allowed(a,mode){return !!a&&(mode!=='DELIVERY'||located(a)&&a.deliveryEligibility&&a.deliveryEligibility.code==='ELIGIBLE')}
function present(a){const status=a.deliveryEligibility||{code:located(a)?'UNAVAILABLE':'UNLOCATED',message:located(a)?'配送资格待核对':'需补全收货位置'};return {...a,mainAddress:a.poiName?[a.poiName,a.doorDetail].filter(Boolean).join(' '):a.detail||'',regionLabel:[a.province,a.city,a.district].filter(Boolean).join(' · '),eligibility:status,canDelivery:allowed(a,'DELIVERY'),needsLocation:status.code==='UNLOCATED'}}
function choose(rows,id,mode){const explicit=rows.find(a=>a.id===id);const fallback=rows.find(a=>a.isDefault);const candidate=explicit||fallback;return allowed(candidate,mode)?present(candidate):null}
function context(storeId,mode){return storeId&&mode==='DELIVERY'?`&storeId=${encodeURIComponent(storeId)}&deliveryMode=DELIVERY`:''}
module.exports={coordinate,located,allowed,present,choose,context}
