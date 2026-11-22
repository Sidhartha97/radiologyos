import unittest
from saas.money import CURRENCY,parse_rupees,format_rupees
from saas.permissions import SaaSError


class MoneyTests(unittest.TestCase):
    def test_inr_exact_integer_paise(self):
        self.assertEqual(CURRENCY,'INR')
        for text,paise in [('0.01',1),('100',10000),('100.5',10050),('999.99',99999)]:
            self.assertEqual(parse_rupees(text),paise)
            self.assertEqual(parse_rupees(format_rupees(paise)),paise)
        self.assertEqual(format_rupees(0),'0.00')

    def test_rejects_rounding_float_and_invalid_money(self):
        for value in ('0','-1','1.001','1e3','1,000',' 100','NaN','01','99999999999',1.25,True,None):
            with self.subTest(value=value),self.assertRaises(SaaSError): parse_rupees(value)
        for value in (True,-1,1.2,10**13):
            with self.assertRaises(SaaSError): format_rupees(value)
