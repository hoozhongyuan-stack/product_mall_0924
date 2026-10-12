import test from 'node:test'
import assert from 'node:assert/strict'
import {templateIssue,zoneIssue} from '../src/views/shipping/rule-validation.mjs'
const template={name:'模板',mode:'WEIGHT',firstFeeFen:800,firstUnits:1000,extraFeeFen:200,extraUnits:1000,freeThresholdFen:19900}
test('weight units require positive integers and fees preserve cents',()=>{assert.equal(templateIssue(template),'');assert.match(templateIssue({...template,extraUnits:0}),/计费单位/);assert.match(templateIssue({...template,firstFeeFen:0.5}),/金额/);assert.match(templateIssue({...template,name:' '}),/名称/)})
const zone={name:'围栏',kind:'POLYGON',polygon:[{latitude:22,longitude:114},{latitude:22.1,longitude:114},{latitude:22,longitude:114.1}],center:null,radiusMeters:null,priority:10,feeMode:'FIXED',feeFen:600,extraFeeFen:200,firstMeters:3000,extraMeters:1000,minimumFen:3900,freeThresholdFen:9900,startTime:'09:00',endTime:'23:00'}
test('fences require geometry, integer thresholds and a real time interval',()=>{assert.equal(zoneIssue(zone),'');assert.match(zoneIssue({...zone,polygon:zone.polygon.slice(0,2)}),/至少/);assert.match(zoneIssue({...zone,endTime:'09:00'}),/时段/);assert.match(zoneIssue({...zone,minimumFen:-1}),/金额/);assert.equal(zoneIssue({...zone,kind:'EXCLUDE',feeFen:-1,minimumFen:-1}),'')})
test('all template modes reject invalid input without converting display values',()=>{
 assert.equal(templateIssue({...template,mode:'FIXED',firstUnits:0,extraUnits:0,freeThresholdFen:null}),'')
 assert.equal(templateIssue({...template,mode:'PIECE',firstUnits:1,extraUnits:1}),'')
 for(const change of [{mode:'unknown'},{firstFeeFen:-1},{extraFeeFen:-1},{freeThresholdFen:-1},{firstUnits:0},{extraUnits:1.1}])assert.notEqual(templateIssue({...template,...change}),'')
})
test('circle and distance validation cover invalid locations and midnight windows',()=>{
 const circle={...zone,kind:'CIRCLE',center:{latitude:22.5,longitude:114.2},radiusMeters:1800,feeMode:'DISTANCE'}
 assert.equal(zoneIssue(circle),'');assert.equal(zoneIssue({...circle,startTime:'23:00',endTime:'02:00',freeThresholdFen:null}),'')
 for(const change of [{name:''},{center:null},{center:{latitude:91,longitude:180}},{radiusMeters:0},{priority:0},{feeFen:-1},{extraFeeFen:0.5},{freeThresholdFen:-1},{firstMeters:0},{extraMeters:0},{startTime:'24:00'},{endTime:'invalid'}])assert.notEqual(zoneIssue({...circle,...change}),'')
 assert.notEqual(zoneIssue({...zone,polygon:[{latitude:100,longitude:0},...zone.polygon]}),'')
})

test('campaign dates round trip in Beijing time regardless of device timezone',async()=>{
 const {activityInputTime,activityInstant}=await import('../src/views/shipping/campaign-time.mjs')
 assert.equal(activityInputTime('2026-10-12T08:35:00Z'),'2026-10-12T16:35:00')
 assert.equal(activityInstant('2026-10-12T16:35:00'),'2026-10-12T08:35:00.000Z')
 assert.equal(activityInputTime('invalid'),'')
 assert.throws(()=>activityInstant('2026-02-30T00:00:00'),/无效/)
 assert.throws(()=>activityInstant('bad'),/无效/)
})
