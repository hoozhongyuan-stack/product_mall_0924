"""Fixed public diagnostics for mini-program uploads; never expose SDK prose."""

FAILURE_TEXT = {
    'WECHAT_REJECTED': ('微信明确拒绝了代码上传请求。',
                        '检查微信返回的错误码及小程序后台配置，修正后创建新任务。'),
    'COMPILE_FAILED': ('小程序代码编译失败。', '检查所选代码版本的编译配置与源文件，修正后创建新任务。'),
    'ENVIRONMENT_FAILED': ('上传服务的本地运行环境出错。', '联系管理员检查上传服务的磁盘空间和文件权限。'),
    'SIGNATURE_FAILED': ('代码上传签名失败。', '检查代码上传私钥与小程序 AppID 是否匹配，然后创建新任务。'),
    'INPUT_INVALID': ('上传请求参数无效。', '重新读取代码版本与配置后创建新任务。'),
    'PROJECT_INVALID': ('暂存的小程序工程无效。', '检查代码包构建结果并重新部署有效版本。'),
    'PROJECT_APPID_MISMATCH': ('代码包 AppID 与上传目标不一致。', '核对小程序 AppID 与代码包配置。'),
    'TARGET_CONFIG_INVALID': ('目标小程序配置无效。', '核对上传通道与代码包配置。'),
    'WORKER_MISCONFIGURED': ('上传服务配置不可用。', '联系管理员检查上传服务配置。'),
    'PACKAGE_INVALID': ('代码包内容无效。', '重新构建并选择完整的代码版本。'),
    'PACKAGE_UNAVAILABLE': ('代码包无法读取或校验。', '检查私有存储同步后重新选择代码版本。'),
    'UPLOAD_KEY_CHANGED': ('上传私钥已变更。', '重新读取密钥状态并创建新任务。'),
    'UPLOAD_KEY_UNAVAILABLE': ('上传私钥无法读取。', '重新配置小程序代码上传私钥。'),
    'APP_ID_CHANGED': ('小程序 AppID 已变更。', '重新读取小程序配置并创建新任务。'),
    'DEVELOPER_APP_ID_CHANGED': ('开发小程序 AppID 已变更。', '重新读取第三方平台配置并创建新任务。'),
    'AUTHORIZATION_NOT_READY': ('第三方平台授权已失效。', '重新检查目标小程序授权后创建新任务。'),
    'CONFIGURATION_UNAVAILABLE': ('小程序配置暂不可用。', '联系管理员检查凭据与部署密钥。'),
    'STORAGE_UNAVAILABLE': ('上传暂存空间不可用。', '联系管理员检查上传服务存储。'),
    'JOB_STATE_CHANGED': ('上传任务状态已变化。', '重新读取任务记录。'),
    'RESULT_UNKNOWN': ('无法确认微信是否接收了代码上传请求。',
                       '先到微信小程序后台核对开发版本与上传记录，确认结果前不要重复上传。'),
}

PLATFORM_REASONS = {
    'IP_NOT_ALLOWED': ('微信提示服务器 IP 未获代码上传许可。',
                       '核对服务器实际出口 IP，并将其加入微信小程序代码上传 IP 白名单；保存后创建新任务。'),
    'SIGNATURE_INVALID': ('微信提示代码上传签名无效。',
                          '核对小程序 AppID 与代码上传私钥，更新错误配置后创建新任务。'),
    'PACKAGE_TOO_LARGE': ('微信提示代码包超过允许大小。',
                          '检查所选不可变版本的代码包大小，压缩后重新构建并创建新任务。'),
    'FILE_MISSING': ('微信提示上传文件缺失。',
                     '重新构建并核验小程序代码包，选择完整版本后创建新任务。'),
    'INNER_UPLOAD_FAILED': ('微信内部上传阶段返回失败。',
                            '根据内层错误码核对微信平台状态和小程序配置，排除问题后创建新任务。'),
    'TICKET_REQUEST_FAILED': ('微信上传票据申请失败。',
                              '根据内层错误码检查小程序上传权限与微信平台状态，排除问题后创建新任务。'),
}


def public_diagnostic(job):
    if not job.failure_code:
        return '', ''
    if job.status == 'RESOLVED':
        return '此前上传结果未确认，任务已按核查说明人工关闭。', ''
    if job.failure_code == 'WECHAT_REJECTED':
        # Official DevTools: DEV_COMPILE_WXSS_FAIL = -80056.
        if job.inner_platform_error_code == -80056:
            return ('微信编译 WXSS 样式失败。',
                    '检查代码包是否包含 @import 引用的全部样式文件，并修正样式语法后重新构建上传。')
        detail = PLATFORM_REASONS.get(job.platform_reason)
        if detail:
            return detail
    return FAILURE_TEXT.get(job.failure_code, ('上传任务失败，原因待核查。',
                                               '重新读取任务记录并联系管理员核查。'))
