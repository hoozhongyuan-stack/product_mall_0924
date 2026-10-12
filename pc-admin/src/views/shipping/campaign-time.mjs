// Activity scheduling uses Beijing time independent of the device timezone.
export function activityInputTime(instant) {
 const date=new Date(instant)
 if(!Number.isFinite(date.getTime()))return ''
 const parts=Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(date).map(p=>[p.type,p.value]))
 return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}`
}
export function activityInstant(input) {
 if(!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(input))throw new Error('活动时间无效。')
 const instant=new Date(`${input}+08:00`)
 if(!Number.isFinite(instant.getTime())||activityInputTime(instant)!==input)throw new Error('活动时间无效。')
 return instant.toISOString()
}
