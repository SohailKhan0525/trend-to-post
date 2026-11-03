"""Workers AI structured responses with bounded, redacted diagnostics."""
import json
import os
import re
import secrets
import urllib.error
import urllib.request


class DraftError(ValueError):
    pass


def json_mode_failed(payload):
    # Provider-side constrained decoding can fail before a response is returned.
    # Match only the documented format-generation failure, not quota/auth errors.
    message=re.sub(r'[^a-z]','',str(payload).casefold())
    return 'jsonmode' in message and any(term in message for term in ('couldntbemet','couldnotbemet','cannotbemet'))


def complete(system,data,temperature=.8,max_tokens=450,schema=None):
    account=os.environ.get('CLOUDFLARE_ACCOUNT_ID','').strip()
    token=os.environ.get('CLOUDFLARE_API_TOKEN','').strip()
    model=os.environ.get('CLOUDFLARE_AI_MODEL') or '@cf/meta/llama-3.1-8b-instruct'
    if not re.fullmatch(r'[a-fA-F0-9]{32}',account) or not token:
        raise ValueError('Cloudflare configuration required')
    if not re.fullmatch(r'@cf/[\w.-]+/[\w.-]+',model):
        raise ValueError('Invalid model identifier')
    payload={'messages':[{'role':'system','content':system},{'role':'user','content':json.dumps(data,ensure_ascii=False)}],
             'seed':secrets.randbelow(999999999)+1,'temperature':temperature,'max_tokens':max_tokens,'response_format':{'type':'json_object'}}
    if schema is not None:
        payload['response_format']={'type':'json_schema','json_schema':schema}
    request=urllib.request.Request(f'https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}',
        data=json.dumps(payload).encode(),headers={'Authorization':f'Bearer {token}','Content-Type':'application/json'},method='POST')
    try:
        with urllib.request.urlopen(request,timeout=60) as response:
            result=json.load(response)
    except urllib.error.HTTPError as exc:
        body=exc.read(8192).decode('utf-8',errors='replace')
        if exc.code==400 and json_mode_failed(body):
            raise DraftError('Cloudflare could not meet the requested JSON schema') from exc
        # Log only the provider's bounded JSON error messages, not exception
        # reprs, request headers or response dumps. Explicitly redact credentials.
        try:
            errors=json.loads(body).get('errors',[])
            for error in errors[:2]:
                message=str(error.get('message',''))
                message=message.replace(token,'[redacted]').replace(account,'[account]')
                message=re.sub(r'(?i)Bearer\s+\S+','Bearer [redacted]',message)
                print('Cloudflare HTTP '+str(exc.code)+': '+json.dumps(message[:400],ensure_ascii=True))
        except (ValueError,AttributeError,TypeError):
            print('Cloudflare HTTP '+str(exc.code)+'; no structured error details')
        raise
    if result.get('success') is not True:
        if json_mode_failed(result.get('errors',[])):
            raise DraftError('Cloudflare could not meet the requested JSON schema')
        raise ValueError('Cloudflare generation failed')
    raw=result.get('result',{}).get('response')
    if isinstance(raw,dict):
        return raw
    if isinstance(raw,str):
        raw=raw.strip()
        if raw.startswith('```'):
            raw=re.sub(r'^```(?:json)?\s*|\s*```$', '',raw)
    try:
        return json.loads(raw,strict=False)
    except (json.JSONDecodeError,TypeError) as exc:
        raise DraftError('Invalid JSON from Cloudflare') from exc
