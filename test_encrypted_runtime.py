"""Run existing delivery and admin behavior against converted, encrypted state."""
import os
import unittest
import test_service
import test_operator_web
import test_connect
import test_operator_accounts
from state_encryption import migrate, finalize


def enable(service):
    job = service.root/'cd-deployments'/'encrypted-regression'
    migrate(service.root,job)
    finalize(service.root,job)


@unittest.skipUnless(os.name == 'nt', 'Windows production key protection')
class EncryptedServiceTests(test_service.ServiceTests):
    def setUp(self):
        super().setUp()
        enable(self.service)


@unittest.skipUnless(os.name == 'nt', 'Windows production key protection')
class EncryptedOperatorTests(test_operator_web.OperatorTests):
    def setUp(self):
        super().setUp()
        enable(self.service)


@unittest.skipUnless(os.name == 'nt', 'Windows production key protection')
class EncryptedConnectionFlowTests(test_connect.ConnectionFlowTests):
    def setUp(self):
        super().setUp()
        enable(self.service)


@unittest.skipUnless(os.name == 'nt', 'Windows production key protection')
class EncryptedLinkedAccountAdminTests(test_operator_accounts.LinkedAccountAdminTests):
    def setUp(self):
        super().setUp()
        enable(self.service)
