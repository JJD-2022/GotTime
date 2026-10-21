"""Pure validation shared by checkout callbacks and server-to-server payments."""
import hashlib
import hmac


def valid_signature(secret, body, signature):
    if not secret or not isinstance(signature, str) or len(signature) != 64:
        return False
    if any(char not in "0123456789abcdefABCDEF" for char in signature):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.lower())


def valid_payment_id(value):
    return (isinstance(value, str) and value.startswith("pay_")
            and 4 < len(value) <= 120 and value[4:].isascii() and value[4:].isalnum())


def payment_problem(payment, order):
    if not isinstance(payment, dict) or not valid_payment_id(payment.get("id")):
        return "Invalid payment details."
    if payment.get("order_id") != order["provider_order_id"]:
        return "Payment does not match this order."
    if type(payment.get("amount")) is not int or payment["amount"] != order["amount_paise"]:
        return "Payment amount does not match this order."
    if payment.get("currency") != order["currency"]:
        return "Payment currency does not match this order."
    return None
