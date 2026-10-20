"""Connection configuration tests: no live database or credentials required."""
import os
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['GOTTIME_AUTO_MIGRATE'] = '0'
import app as backend


class DatabaseConnectionConfig(unittest.TestCase):
    def test_local_connection_uses_default_port_without_custom_tls(self):
        with patch.dict(backend.app.config, MYSQL_HOST='localhost', MYSQL_PORT=3306, MYSQL_SSL_CA=''):
            with patch.object(backend.pymysql, 'connect') as connect:
                with backend.app.app_context():
                    backend.get_db()
                    options = connect.call_args.kwargs
                    self.assertEqual(options['host'], 'localhost')
                    self.assertEqual(options['port'], 3306)
                    self.assertIsNone(options['ssl'])

    def test_hosted_connection_uses_port_and_verified_tls(self):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with patch.dict(backend.app.config, MYSQL_HOST='database.example.test', MYSQL_PORT=21957,
                        MYSQL_SSL_CA='/etc/secrets/ca.pem'):
            with patch.object(backend.ssl, 'create_default_context', return_value=context) as factory:
                with patch.object(backend.pymysql, 'connect') as connect:
                    with backend.app.app_context():
                        first = backend.get_db()
                        self.assertIs(first, backend.get_db())
                        connect.assert_called_once()
                        factory.assert_called_once_with(cafile='/etc/secrets/ca.pem')
                        options = connect.call_args.kwargs
                        self.assertEqual(options['port'], 21957)
                        self.assertIs(options['ssl'], context)
                        self.assertTrue(context.check_hostname)
                        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_missing_certificate_does_not_attempt_plaintext_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(backend.app.config, MYSQL_SSL_CA=str(Path(directory) / 'missing.pem')):
                with patch.object(backend.pymysql, 'connect') as connect:
                    with backend.app.app_context():
                        with self.assertRaises(FileNotFoundError):
                            backend.get_db()
                        connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
