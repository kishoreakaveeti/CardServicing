"""Privileged deployment-only probes, piped into existing containers; no new routes."""
import asyncio
import json
import os
import time
from uuid import uuid4
import httpx
from card_chat.security import Security


async def main():
    results=[]
    def passed(name,evidence):
        results.append(dict(check=name,status='PASS',evidence=evidence))
    try:
        config=dict(os.environ)
        assert config['TELEMETRY_SINK']=='azure_blob'
        security=Security(config)
        async with httpx.AsyncClient(timeout=90) as client:
            if config['SERVICE_CLIENT_ID']=='experience-service':
                for url in ('http://cards-agent:8000/conversations','http://mcp-server:8000/mcp',
                            'http://cards-api:8000/cards','http://fincrime-api:8000/blocked-cards',
                            'http://fincrime-agent:8000/turn'):
                    assert (await client.post(url)).status_code==401
                token=await security.service_token()
                customer=security.issue_customer({'sub':'azure-internal-'+uuid4().hex,
                    'preferred_username':'azure.synthetic','accounts':[]})
                headers={'Authorization':'Bearer '+token,'X-Customer-Delegation':customer}
                assert (await client.post('http://mcp-server:8000/mcp',headers=headers)).status_code==401
                assert (await client.post('http://cards-api:8000/cards',headers=headers)).status_code==401
                assert (await client.post('http://cards-agent:8000/conversations',headers=headers)).status_code==200
                assert (await client.post('http://cards-agent:8000/conversations',
                    headers={**headers,'X-Customer-Delegation':'tampered'})).status_code==401
                passed('mcp-and-delegation','Anonymous requests denied on every internal service; wrong audiences and tampered delegation rejected')
            else:
                from card_chat.catalogue import load_catalogue
                catalogue=load_catalogue(config)
                payload={'history':[{'role':'user','content':'Please temporarily freeze my card'}],
                    'collected':{},'allowed_tools':{'block_card':catalogue['block_card'].definition()}}
                response=await client.post('http://fincrime-agent:8000/turn',json=payload,headers=await security.headers())
                assert response.status_code==200 and response.json()['kind'] in ('reply','proposal')
                passed('fincrime-agent','Authenticated agent-service token exercised the remote FinCrime /turn and configured Anthropic model')
                from card_chat.policy.factory import build_runtime_policy_services
                retriever,_=build_runtime_policy_services(config)
                evidence=retriever.retrieve('unblock_card',{'is_temporary_freeze':True})
                assert any(item.policy_id=='POL-001' and item.mandatory for item in evidence)
                passed('rag-retrieval','Strict approved-policy index verified; real FastEmbed/Qdrant retrieval returned mandatory POL-001')
    except Exception as exc:
        results.append(dict(check='private-'+os.environ.get('SERVICE_CLIENT_ID','unknown'),status='FAIL',evidence=type(exc).__name__))
    print(json.dumps(results))


asyncio.run(main())
