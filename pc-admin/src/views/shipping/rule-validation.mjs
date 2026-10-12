const integer=(n,min=0)=>Number.isSafeInteger(n)&&n>=min&&n<=1000000000
const validPoint=p=>p&&Number.isFinite(p.latitude)&&Number.isFinite(p.longitude)&&Math.abs(p.latitude)<=90&&Math.abs(p.longitude)<=180
export function templateIssue(t){
 if(!t.name.trim())return '请填写模板名称。'
 if(!['FIXED','PIECE','WEIGHT'].includes(t.mode))return '请选择计费方式。'
 if(!integer(t.firstFeeFen)||!integer(t.extraFeeFen)||t.freeThresholdFen!==null&&!integer(t.freeThresholdFen))return '金额必须为有效的非负整数分。'
 if(t.mode!=='FIXED'&&(!integer(t.firstUnits,1)||!integer(t.extraUnits,1)))return '首段和续段计费单位必须为正整数。'
 return ''
}
export function zoneIssue(z){
 if(!z.name.trim())return '请填写围栏名称。'
 if(z.kind==='CIRCLE'&&(!validPoint(z.center)||!integer(z.radiusMeters,1)))return '请在地图中选择圆心并填写配送半径。'
 if(z.kind!=='CIRCLE'&&(!Array.isArray(z.polygon)||z.polygon.length<3||z.polygon.some(p=>!validPoint(p))))return '多边形至少需要三个有效地图点。'
 if(z.kind==='EXCLUDE')return ''
 if(!integer(z.priority,1))return '优先级必须为正整数，数字越小越优先。'
 if([z.feeFen,z.extraFeeFen,z.minimumFen].some(n=>!integer(n))||z.freeThresholdFen!==null&&!integer(z.freeThresholdFen))return '金额必须为有效的非负整数分。'
 if(z.feeMode==='DISTANCE'&&(!integer(z.firstMeters,1)||!integer(z.extraMeters,1)))return '首段和续段距离必须为正整数米。'
 if(!/^([01]\d|2[0-3]):[0-5]\d$/.test(z.startTime)||!/^([01]\d|2[0-3]):[0-5]\d$/.test(z.endTime)||z.startTime===z.endTime)return '请选择有效且不同的配送时段起止时间。'
 return ''
}
