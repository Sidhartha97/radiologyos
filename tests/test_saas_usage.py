import unittest
from saas.usage import Rate,settlement
from saas.permissions import SaaSError


class UsagePricingTests(unittest.TestCase):
    def setUp(self):
        # Synthetic credit weights, NOT production prices.
        self.rate=Rate('test-v1','test-model',3,7)

    def test_round_once_and_no_output_quality_discount(self):
        self.assertEqual(self.rate.price(1,1),1)
        result=settlement(self.rate,'test-model',{'inputTokens':1000,'outputTokens':1000,'totalTokens':2000})
        self.assertEqual(result['creditUnits'],10)
        self.assertNotIn('report',result)

    def test_missing_inconsistent_or_invalid_usage_is_not_free(self):
        for usage in ({}, {'inputTokens':1,'outputTokens':1},
                      {'inputTokens':1,'outputTokens':1,'totalTokens':1},
                      {'inputTokens':True,'outputTokens':0,'totalTokens':1},
                      {'inputTokens':-1,'outputTokens':1,'totalTokens':0}):
            with self.subTest(usage=usage),self.assertRaises(SaaSError):
                settlement(self.rate,'test-model',usage)

    def test_cache_and_model_mismatch_require_reconciliation(self):
        base={'inputTokens':10,'outputTokens':10,'totalTokens':20}
        for usage in ({**base,'cacheReadInputTokens':1},{**base,'cacheWriteInputTokens':1},
                      {**base,'cacheDetails':[{'inputTokens':1}]}):
            with self.assertRaises(SaaSError): settlement(self.rate,'test-model',usage)
        with self.assertRaises(SaaSError): settlement(self.rate,'other-model',base)

    def test_zero_usage_allowed_only_with_explicit_provider_counters(self):
        self.assertEqual(settlement(self.rate,'test-model',{'inputTokens':0,'outputTokens':0,'totalTokens':0})['creditUnits'],0)

    def test_rates_are_immutable_and_not_floating_point(self):
        for weight in (True,0,-1,1.2):
            with self.assertRaises(ValueError): Rate('v','m',weight,1)
        with self.assertRaises(AttributeError): self.rate.version='changed'


if __name__=='__main__': unittest.main()
