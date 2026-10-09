import pytest
from exodiscovery import assistant, store
from exodiscovery.schemas import AIConfig


@pytest.mark.parametrize('provider',['openai','anthropic','gemini','openrouter','local'])
def test_provider_real_protocol_translation_and_tool_result_roundtrip(provider,monkeypatch,isolated_store):
    calls=[]
    def transport(cfg,method,route,payload=None):
        calls.append((route,payload))
        if provider=='openai':
            if len(calls)==1:
                return {'output':[{'type':'function_call','call_id':'c1','name':'list_investigations','arguments':'{}'}]}
            assert any(x.get('type')=='function_call_output' and x['call_id']=='c1' for x in payload['input'])
            return {'output':[{'type':'message','content':[{'type':'output_text','text':'No saved investigations.'}]}]}
        if provider=='anthropic':
            if len(calls)==1:
                return {'content':[{'type':'tool_use','id':'c1','name':'list_investigations','input':{}}]}
            assert payload['messages'][-1]['content'][0]['type']=='tool_result'
            return {'content':[{'type':'text','text':'No saved investigations.'}]}
        if len(calls)==1:
            return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'c1','type':'function','function':{'name':'list_investigations','arguments':'{}'}}]}}]}
        assert payload['messages'][-1]['role']=='tool'
        return {'choices':[{'message':{'role':'assistant','content':'No saved investigations.'}}]}
    monkeypatch.setattr(assistant,'request',transport)
    cfg=AIConfig(provider=provider,model='protocol-test')
    transcript=[{'role':'user','content':'List investigations'}]
    msg,tool_calls,_=assistant.turn(cfg,transcript,assistant.tool_specs())
    result=assistant.execute_tool(tool_calls[0]['name'],{},None,{'analyses':0},cfg)
    assert result==[]
    transcript.extend([msg,{'role':'tool','tool_call_id':'c1','content':'[]'}])
    msg,tools,_=assistant.turn(cfg,transcript,assistant.tool_specs())
    assert not tools
    assert 'No saved' in msg['content']


def test_cloud_spending_guard_blocks_before_network(isolated_store,monkeypatch):
    cfg=AIConfig(model='test',input_usd_per_million=100,output_usd_per_million=100,budget_usd=.01)
    monkeypatch.setattr(assistant,'config',lambda:cfg)
    monkeypatch.setattr(assistant,'turn',lambda *_:pytest.fail('Budget should block request'))
    j=store.create_job('chat',{})
    store.message('conversation','user','Inspect data')
    with pytest.raises(ValueError,match='budget guard'):
        assistant.investigate({'conversation_id':'conversation'},j['id'])
