export function sourceReason(source) {
 return ({ORDER_COMPLETED:'订单履约完成',REFUND_COMPLETED:'售后退款完成',FULFILLMENT_REVERSED:'履约撤销',POINTS_EXPIRED:'积分自然到期',ORDER_SETTLEMENT:'订单权益结算',GRANT:'积分发放',EXPIRE:'积分到期',RESERVE:'订单冻结',RELEASE:'冻结释放',CONSUME:'订单抵扣',REFUND:'退款返还',RETURN:'退款返还',CLAWBACK:'消费奖励扣回',EARN:'消费奖励',EARN_REVERSE:'奖励扣回',REVOKE:'奖励扣回',ADJUST:'积分调整'})[source] || '其他积分变动'
}
