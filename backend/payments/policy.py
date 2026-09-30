"""Named policy access for order snapshots; edits affect future orders only."""
from .models import OfflinePaymentPolicy


def policy_data(policy):
    from .availability import enabled_payment_methods
    from .wechat_config import WechatGatewayError, load_wechat_config
    methods = enabled_payment_methods(policy)
    try:
        load_wechat_config()
        wechat_status = 'PENDING_VERIFICATION'
    except WechatGatewayError:
        wechat_status = 'NOT_CONFIGURED'
    return {'offlineEnabled': 'OFFLINE' in methods, 'wechatEnabled': 'WECHAT' in methods,
            'wechatConfigurationStatus': wechat_status, 'availablePaymentMethods': list(methods), 'instructions': policy.instructions, 'merchantAccountId': policy.merchant_account_id,
            'revision': policy.revision, 'wechatTimeoutMinutes': policy.wechat_timeout_minutes,
            'offlineTimeoutMinutes': policy.offline_timeout_minutes,
            'configured': bool(policy.instructions.strip() and policy.merchant_account_id.strip())}


def order_payment_snapshot(method):
    from .service import PaymentError
    policy = OfflinePaymentPolicy.objects.select_for_update().filter(pk=1).first()
    if policy is None:
        raise PaymentError('付款配置不可用。', 'PAYMENT_POLICY_UNAVAILABLE', 503)
    from .availability import enabled_payment_methods
    if method not in enabled_payment_methods(policy):
        raise PaymentError('该支付方式已停用，请重新选择。', 'PAYMENT_METHOD_DISABLED', 409)
    timeout = policy.wechat_timeout_minutes if method == 'WECHAT' else policy.offline_timeout_minutes
    if method == 'OFFLINE' and not policy_data(policy)['configured']:
        raise PaymentError('线下付款说明和收款账户尚未配置。', 'OFFLINE_POLICY_UNCONFIGURED', 503)
    snapshot = {'revision': policy.revision, 'timeoutMinutes': timeout}
    if method == 'OFFLINE':
        snapshot.update(instructions=policy.instructions, merchantAccountId=policy.merchant_account_id)
    return snapshot
