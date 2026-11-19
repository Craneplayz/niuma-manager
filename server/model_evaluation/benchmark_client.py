"""Evaluation-only Qiniu transport with per-request budget reservation and usage."""
import json
import secrets
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener
from ddl_text.chat_client import ChatClient, NoRedirect, MAX_RESPONSE_BYTES
from ddl_text.adapters import AgentError
from ddl_text.call_observation import actual_usage
from .budget import BudgetExceeded


class BenchmarkClient(ChatClient):
    def __init__(self,key,trace,profile,budget):
        self.trace,self.profile,self.budget=trace,profile,budget
        super().__init__(key,call_observer=self.observe)

    def observe(self,event):
        with self.trace.with_suffix('.calls.jsonl').open('a')as stream:
            stream.write(json.dumps(event,allow_nan=False)+'\n')

    def complete_json(self,instructions,input_json,settings):
        raw=super().complete_json(instructions,input_json,settings)
        with self.trace.with_suffix('.raw.jsonl').open('a')as stream:
            stream.write(json.dumps(raw,ensure_ascii=False)+'\n')
        return raw

    def _complete_json(self,instructions,input_json,settings,event):
        profile=self.profile
        maximum=profile['max_output_tokens']
        payload=dict(model=profile['id'],messages=[dict(role='system',content=instructions),dict(role='user',content=input_json)],
                     stream=False,max_tokens=maximum,response_format=dict(type='json_object'))
        if profile['family']=='GPT':
            payload['max_completion_tokens']=payload.pop('max_tokens')
            payload['reasoning_effort']='none'
        elif profile.get('thinking') is not None:payload['thinking']=dict(type=profile['thinking'])
        encoded=json.dumps(payload,ensure_ascii=False).encode()
        # Byte bound over text plus base64 images exceeds their tokenized size for these fixed fixtures.
        # A bounded-output, no-tool request cannot create a search or cache-creation charge.
        upper=(len(encoded)+4096)*profile['input_yuan_per_token_max']+maximum*profile['output_yuan_per_token_max']
        request_id=secrets.token_hex(16)
        if self.budget.summary()['boundExceeded']:raise BudgetExceeded('previous billing bound exceeded')
        self.budget.reserve(request_id,upper)
        event.update(budget_request_id=request_id,reserved_yuan=upper,pricing_basis='catalog_max_tier_standard_estimate')
        request=Request(profile['endpoint']+'/chat/completions',data=encoded,
                        headers={'Authorization':'Bearer '+self._key,'Content-Type':'application/json'})
        started=time.monotonic()
        try:
            with build_opener(NoRedirect()).open(request,timeout=settings.timeout_seconds)as response:
                raw=response.read(MAX_RESPONSE_BYTES+1)
            if len(raw)>MAX_RESPONSE_BYTES:raise ValueError('response too large')
            envelope=json.loads(raw)
            event.update(actual_usage(envelope))
            usage=envelope.get('usage',{})
            event['provider_request_id']=envelope.get('id')
            event['usage_details']={k:usage[k]for k in ('prompt_tokens_details','completion_tokens_details')if isinstance(usage.get(k),dict)}
            input_tokens,event_output=event.get('input_tokens'),event.get('output_tokens')
            cost=None
            if type(input_tokens)==int and type(event_output)==int:
                cost=input_tokens*profile['input_yuan_per_token_max']+event_output*profile['output_yuan_per_token_max']
            event.update(estimated_yuan=cost,request_seconds=round(time.monotonic()-started,4))
            self.budget.settle(request_id,cost)
            choices=envelope.get('choices',[])
            if len(choices)!=1:raise ValueError('one answer required')
            choice=choices[0];message=choice.get('message',{})
            if choice.get('finish_reason')!='stop' or message.get('refusal') or message.get('tool_calls'):raise ValueError('incomplete output')
            text=message.get('content')
            if not isinstance(text,str) or not text.strip():raise ValueError('empty output')
            event['result_code']='ok'
            return text
        except BudgetExceeded:raise
        except HTTPError as error:
            event.update(http_status=error.code,result_code='rate_limited' if error.code==429 else 'http_error')
            try:
                detail=json.loads(error.read(8192)).get('error',{})
                for name in ('code','type','param'):
                    value=detail.get(name) if isinstance(detail,dict) else None
                    if isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',value):event['provider_error_'+name]=value
            except (ValueError,AttributeError):pass
            error.close();raise AgentError('agent_unavailable',False,'model_unavailable')from None
        except (TimeoutError,URLError,OSError):
            event['result_code']='timeout_or_network';raise AgentError('agent_timeout',False)from None
        except (ValueError,KeyError,TypeError):
            event['result_code']='invalid_response';raise AgentError('invalid_agent_output',False,'provider_response')from None
