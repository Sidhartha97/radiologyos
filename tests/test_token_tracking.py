import unittest
from unittest.mock import Mock
from saas.token_tracking import Runtime,counters


class TokenTrackingTests(unittest.TestCase):
    def test_request_and_response_unchanged_no_text_stored(self):
        tracker=Mock(); tracker.begin.return_value=('event','scope')
        client=Mock(); response={'output':{'message':{'content':[{'text':'PRIVATE REPORT'}]}},
            'usage':{'inputTokens':20,'outputTokens':10,'totalTokens':30,'cacheReadInputTokens':5},'ResponseMetadata':{'RequestId':'request-123'}}
        client.converse.return_value=response
        payload={'modelId':'test-model','system':[{'text':'PRIVATE SYSTEM'}],'messages':[{'role':'user','content':[{'text':'PRIVATE PROMPT'}]}],'inferenceConfig':{'temperature':0.1,'maxTokens':2400}}
        self.assertIs(Runtime(client,tracker).converse(**payload),response)
        client.converse.assert_called_once_with(**payload)
        stored=tracker.finish.call_args.args[1]
        self.assertEqual(stored['input_tokens'],20); self.assertEqual(stored['output_tokens'],10)
        self.assertEqual(stored['total_tokens'],30); self.assertEqual(stored['cache_read_tokens'],5)
        self.assertNotIn('PRIVATE',str(stored))

    def test_missing_usage_is_not_invented_zero(self):
        usage=counters({'usage':{'inputTokens':9}})
        self.assertEqual(usage['state'],'USAGE_MISSING'); self.assertIsNone(usage['output_tokens'])
        self.assertEqual(counters({'usage':{'inputTokens':True,'outputTokens':0,'totalTokens':1}})['state'],'USAGE_MISSING')

    def test_timeout_remains_unknown_and_not_retried(self):
        client=Mock(); error=TimeoutError('private provider details'); client.converse.side_effect=error
        tracker=Mock(); tracker.begin.return_value=('event','scope')
        with self.assertRaises(TimeoutError) as caught: Runtime(client,tracker).converse(modelId='m')
        self.assertIs(caught.exception,error); client.converse.assert_called_once()
        self.assertEqual(tracker.finish.call_args.args[1],{'state':'OUTCOME_UNKNOWN'})

    def test_persistence_failure_does_not_regenerate_report(self):
        client=Mock(); client.converse.return_value={'usage':{'inputTokens':1,'outputTokens':2,'totalTokens':3}}
        tracker=Mock(); tracker.begin.return_value=('event','scope'); tracker.finish.side_effect=RuntimeError('DB unavailable')
        self.assertIs(Runtime(client,tracker).converse(modelId='m'),client.converse.return_value)
        client.converse.assert_called_once()
