import io
import json
import os
import unittest
import urllib.error
from unittest.mock import patch
from app.cloudflare import complete,DraftError,selected_model
from app.ai_budget import AIPaused


class CloudflareTests(unittest.TestCase):
    def test_retiring_model_overrides_migrate_but_other_explicit_models_remain(self):
        for key in ('QOFENO_AI_MODEL','DASH_RATTLE_MODEL'):
            with patch.dict(os.environ,{key:'@cf/qwen/qwen3-30b-a3b-fp8'},clear=True):
                self.assertEqual(selected_model(),'@cf/google/gemma-4-26b-a4b-it')
        with patch.dict(os.environ,{'QOFENO_AI_MODEL':'@cf/google/gemma-4-26b-a4b-it'},clear=True):
            self.assertEqual(selected_model(),'@cf/google/gemma-4-26b-a4b-it')
    def test_glm_uses_documented_schema_envelope_and_bounded_nonreasoning_output(self):
        schema={'type':'object','properties':{'text':{'type':'string'}},'required':['text'],'additionalProperties':False}
        response=io.StringIO(json.dumps({'success':True,'result':{'choices':[{'message':{'content':'{"text":"😂"}'}}]}}))
        with patch.dict(os.environ,{'CLOUDFLARE_ACCOUNT_ID':'a'*32,'CLOUDFLARE_API_TOKEN':'test'}),patch('urllib.request.urlopen',return_value=response) as request:
            self.assertEqual(complete('Return JSON',{},schema=schema,max_tokens=200,model='@cf/zai-org/glm-4.7-flash'),{'text':'😂'})
        payload=json.loads(request.call_args.args[0].data)
        self.assertEqual(payload['response_format']['json_schema'],{'name':'qofeno_response','schema':schema,'strict':True})
        self.assertEqual(payload['max_completion_tokens'],200)
        self.assertNotIn('max_tokens',payload)
        self.assertEqual(payload['chat_template_kwargs'],{'enable_thinking':False})

    def test_cloudflare_request_uses_native_schema_when_supplied(self):
        schema={'type':'object','properties':{'allowed':{'type':'boolean'}},'required':['allowed']}
        response=io.StringIO(json.dumps({'success':True,'result':{'response':{'allowed':False}}}))
        with patch.dict(os.environ,{'CLOUDFLARE_ACCOUNT_ID':'a'*32,'CLOUDFLARE_API_TOKEN':'test','DASH_RATTLE_MODEL':'@cf/meta/llama-3.3-70b-instruct-fp8-fast'}),patch('urllib.request.urlopen',return_value=response) as call:
            self.assertEqual(complete('classify',{},schema=schema),{'allowed':False})
        self.assertEqual(json.loads(call.call_args.args[0].data)['response_format'],{'type':'json_schema','json_schema':schema})

    def test_json_mode_generation_failure_is_retryable_but_rate_limits_are_not(self):
        mode_error=urllib.error.HTTPError('https://api.cloudflare.com',400,'bad',{},io.BytesIO(json.dumps({'errors':[{'message':"JSON Mode couldn't be met"}]}).encode()))
        quota_error=urllib.error.HTTPError('https://api.cloudflare.com',429,'limit',{},io.BytesIO(b'{}'))
        for error,expected in ((mode_error,DraftError),(quota_error,AIPaused)):
            with patch.dict(os.environ,{'CLOUDFLARE_ACCOUNT_ID':'a'*32,'CLOUDFLARE_API_TOKEN':'test'}),patch('urllib.request.urlopen',side_effect=error),self.assertRaises(expected):complete('rules',{})

    def test_provider_diagnostics_redact_before_truncating(self):
        token='secret-test-token-value-crossing-the-log-limit'
        message='x'*350+token+'a'*32
        error=urllib.error.HTTPError('https://api.cloudflare.com',400,'bad',{},io.BytesIO(json.dumps({'errors':[{'message':message}]}).encode()))
        with patch.dict(os.environ,{'CLOUDFLARE_ACCOUNT_ID':'a'*32,'CLOUDFLARE_API_TOKEN':token}),patch('urllib.request.urlopen',side_effect=error),patch('builtins.print') as output,self.assertRaises(urllib.error.HTTPError):complete('rules',{})
        logged=output.call_args.args[0]
        self.assertNotIn('secret-test',logged);self.assertNotIn('a'*8,logged)
        self.assertIn('[redacted]',logged);self.assertIn('[account]',logged)

    def test_quota_pause_and_provider_logs_do_not_expose_credentials(self):
        token='fixture-super-secret-token'
        account='a'*32
        message=f'daily free allocation of neurons exhausted: {token} {account} Bearer other-private-value'
        error=urllib.error.HTTPError('https://api.cloudflare.com',429,'limit',{},io.BytesIO(json.dumps({'errors':[{'message':message}]}).encode()))
        with patch.dict(os.environ,{'CLOUDFLARE_ACCOUNT_ID':account,'CLOUDFLARE_API_TOKEN':token}),\
             patch('urllib.request.urlopen',side_effect=error),patch('builtins.print') as output,self.assertRaises(AIPaused) as raised:
            complete('rules',{})
        logged='\n'.join(call.args[0] for call in output.call_args_list)
        for value in (token,account,'other-private-value'):
            self.assertNotIn(value,logged)
            self.assertNotIn(value,str(raised.exception))
        self.assertIn('[redacted]',logged)
        self.assertIn('[account]',logged)

    def test_model_receives_native_script_in_the_review_message(self):
        response=io.StringIO(json.dumps({'success':True,'result':{'response':{'safe':True}}}))
        with patch.dict(os.environ,{'CLOUDFLARE_ACCOUNT_ID':'a'*32,'CLOUDFLARE_API_TOKEN':'test'}),patch('urllib.request.urlopen',return_value=response) as request:
            complete('review',{'text':'雨刷在设置里。'})
        self.assertIn('雨刷在设置里。',json.loads(request.call_args.args[0].data)['messages'][1]['content'])

    def test_qwen_decodes_native_scripts_and_chat_completions(self):
        response=io.StringIO(json.dumps({'success':True,'result':{'choices':[{'message':{'content':'{"text":"雨が止んだ。"}'}}]}}))
        with patch.dict(os.environ,{'CLOUDFLARE_ACCOUNT_ID':'a'*32,'CLOUDFLARE_API_TOKEN':'test','DASH_RATTLE_MODEL':'@cf/qwen/qwen3-30b-a3b-fp8'}),patch('urllib.request.urlopen',return_value=response) as req:
            self.assertEqual(complete('write',{},model='@cf/qwen/qwen3-30b-a3b-fp8'),{'text':'雨が止んだ。'})
        self.assertNotIn('response_format',json.loads(req.call_args.args[0].data))
