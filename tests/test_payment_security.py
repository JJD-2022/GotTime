import hashlib
import hmac
import unittest
from payment_security import valid_signature, valid_payment_id, payment_problem


class PaymentSecurity(unittest.TestCase):
    def test_signature_is_over_exact_bytes(self):
        raw = '{"message": "₹10"}'.encode()
        signature = hmac.new(b'secret', raw, hashlib.sha256).hexdigest()
        self.assertTrue(valid_signature('secret', raw, signature))
        self.assertFalse(valid_signature('secret', raw + b' ', signature))
        for invalid in (None, '', 'x'*64, '€'*64, 123):
            self.assertFalse(valid_signature('secret', raw, invalid))
        self.assertFalse(valid_signature('', raw, signature))

    def test_payment_ids_cannot_change_provider_url(self):
        self.assertTrue(valid_payment_id('pay_Ab123'))
        for value in (None, '', 'pay_', 'pay_../orders', 'pay_test?other=1', 'pay_é', 'pay_'+'a'*121):
            self.assertFalse(valid_payment_id(value))

    def test_amount_is_integer_paise_and_order_currency_match(self):
        order = dict(provider_order_id='order_test',amount_paise=1,currency='INR')
        payment = dict(id='pay_test',order_id='order_test',amount=1,currency='INR')
        self.assertIsNone(payment_problem(payment,order))
        for amount in (True,'1',1.0,2):
            self.assertIsNotNone(payment_problem({**payment,'amount':amount},order))


if __name__ == '__main__':
    unittest.main()
