const points=value=>Number.isSafeInteger(value)&&value>=0?`${value} 积分`:'待核查'
export function pointRefundState(item){
 switch(item.status){
  case 'PENDING_REVIEW':return {label:'申请待审核',value:'未批准',summary:`申请退回 ${points(item.requestedRefundPoints)}，待审核；当前不会返还积分。`}
  case 'WAITING_RETURN':return {label:'退货验收待定',value:'待验收',summary:'售后申请已获准，最终退回积分尚未确认；等待退货验收，当前不会返还积分。'}
  case 'WAITING_REFUND':return {label:'本笔批准退回积分',value:points(item.pointsToReturn),summary:`本笔批准退回 ${points(item.pointsToReturn)}，尚未返还；授权确认数量与权益后完成。`}
  case 'COMPLETED':return {label:'本笔已退积分',value:points(item.pointsToReturn),summary:`本笔已返还 ${points(item.pointsToReturn)}，不产生资金退款凭证。`}
  case 'REJECTED':return {label:'本笔处理结果',value:'已拒绝，不返还',summary:'申请已拒绝，本笔不返还积分。原申请积分只作为历史记录保留。'}
  case 'WITHDRAWN':return {label:'本笔处理结果',value:'已撤销，不返还',summary:'申请已撤销，本笔不返还积分。原申请积分只作为历史记录保留。'}
  default:return {label:'处理状态待核查',value:'待核查',summary:'当前处理状态无法确认，请重新读取服务端结果。'}
 }
}
