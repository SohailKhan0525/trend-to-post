import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch
from app.cloudflare import complete,DraftError


class CloudflareTests(unittest.TestCase):
    def test_cloudflare_request_uses_native_schema_when_supplied(self):
        schema={'type':'object','properties':{'allowed':{'type':'boolean'}},'required':['allowed']}
        response=io.StringIO(json.dumps({'success':True,'result':{'response':{'allowed':False}}}))
        with patch.dict(os.environ,{'CLOUDFLARE_ACCOUNT_ID':'a'*32,'CLOUDFLARE_API_TOKEN':'test'}),patch('urllib.request.urlopen',return_value=response) as call:
            self.assertEqual(complete('classify',{},schema=schema),{'allowed':False})
        self.assertEqual(json.loads(call.call_args.args[0].data)['response_format'],{'type':'json_schema','json_schema':schema})

    def test_json_mode_generation_failure_is_retryable_but_rate_limits_are_not(self):
        mode_error=urllib.error.HTTPError('https://api.cloudflare.com',400,'bad',{},io.BytesIO(json.dumps({'errors':[{'message':"JSON Mode couldn't be met"}]}).encode()))
        quota_error=urllib.error.HTTPError('https://api.cloudflare.com',429,'limit',{},io.BytesIO(b'{}'))
        for error,expected in ((mode_error,DraftError),(quota_error,urllib.error.HTTPError)):
            with patch.dict(os.environ,{'CLOUDFLARE_ACCOUNT_ID':'a'*32,'CLOUDFLARE_API_TOKEN':'test'}),patch('urllib.request.urlopen',side_effect=error),self.assertRaises(expected):complete('rules',{})

    def test_provider_diagnostics_redact_before_truncating(self):
        token='secret-test-token-value-crossing-the-log-limit'
        message='x'*350+token+'a'*32
        error=urllib.error.HTTPError('https://api.cloudflare.com',400,'bad',{},io.BytesIO(json.dumps({'errors':[{'message':message}]}).encode()))
        with patch.dict(os.environ,{'CLOUDFLARE_ACCOUNT_ID':'a'*32,'CLOUDFLARE_API_TOKEN':token}),patch('urllib.request.urlopen',side_effect=error),patch('builtins.print') as output,self.assertRaises(urllib.error.HTTPError):complete('rules',{})
        logged=output.call_args.args[0]
        self.assertNotIn('secret-test',logged);self.assertNotIn('a'*8,logged)
        self.assertIn('[redacted]',logged);self.assertIn('[account]',logged)

    def test_model_receives_native_script_in_the_review_message(self):
        response=io.StringIO(json.dumps({'success':True,'result':{'response':{'safe':True}}}))
        with patch.dict(os.environ,{'CLOUDFLARE_ACCOUNT_ID':'a'*32,'CLOUDFLARE_API_TOKEN':'test'}),patch('urllib.request.urlopen',return_value=response) as request:
            complete('review',{'text':'雨刷在设置里。'})
        self.assertIn('雨刷在设置里。',json.loads(request.call_args.args[0].data)['messages'][1]['content'])
