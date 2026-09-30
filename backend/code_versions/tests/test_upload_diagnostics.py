from types import SimpleNamespace
from unittest import TestCase

from code_versions.upload_diagnostics import public_diagnostic


class UploadDiagnosticTests(TestCase):
    def test_wechat_wxss_failure_has_a_specific_source_package_action(self):
        job = SimpleNamespace(status='FAILED', failure_code='WECHAT_REJECTED',
                              inner_platform_error_code=-80056,
                              platform_reason='INNER_UPLOAD_FAILED')
        message, action = public_diagnostic(job)
        self.assertIn('WXSS', message)
        self.assertIn('@import', action)
        self.assertIn('重新构建', action)

    def test_unknown_inner_error_keeps_generic_diagnostic(self):
        job = SimpleNamespace(status='FAILED', failure_code='WECHAT_REJECTED',
                              inner_platform_error_code=-99999,
                              platform_reason='INNER_UPLOAD_FAILED')
        message, _ = public_diagnostic(job)
        self.assertNotIn('WXSS', message)
