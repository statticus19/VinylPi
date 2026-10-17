import asyncio
import importlib.util
from pathlib import Path
import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1] / 'music_bridge'

def load(name):
    spec=importlib.util.spec_from_file_location(name, ROOT / (name + '.py'))
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

matching=load('youtube_matching')
store_module=load('store')

@pytest.mark.parametrize('code,reason,expected', [
    (429,'rateLimitExceeded',True), (403,'userRateLimitExceeded',True),
    (403,'quotaExceeded',False), (403,'forbidden',False)])
def test_reason_classification(code, reason, expected):
    r=httpx.Response(code, json={'error':{'errors':[{'reason':reason}]}}, request=httpx.Request('GET','https://example.invalid'))
    async def run():
        await matching.response_json(r)
    with pytest.raises(matching.PreviewError) as exc:
        asyncio.run(run())
    assert isinstance(exc.value, matching.TemporaryRateLimit) is expected

def test_read_retry_and_bounded():
    class Client:
        def __init__(self, statuses): self.statuses=statuses; self.calls=0
        async def get(self,*args,**kwargs):
            code=self.statuses[min(self.calls,len(self.statuses)-1)];self.calls+=1
            return httpx.Response(code, json=({'ok':True} if code==200 else {'error': {'errors':[{'reason':'rateLimitExceeded'}]}}), request=httpx.Request('GET','https://example.invalid'))
    async def go(client):
        waits=[]
        async def sleep(seconds): waits.append(seconds)
        try:
            return await matching.read_json_with_backoff(client,'https://example.invalid',headers={},params={},sleep=sleep),waits
        except matching.TemporaryRateLimit:
            return None, waits
    client=Client([429,429,200]); result,waits=asyncio.run(go(client));assert result['ok'] and waits==[2,4] and client.calls==3
    client=Client([429]); result,waits=asyncio.run(go(client));assert result is None and client.calls==3

def test_requeue_waiting_rate_limit(tmp_path):
    store=store_module.JobStore(tmp_path / 'jobs.db')
    payload={'tracks':[{'title':'Song','artists':['Artist']}], 'mode':'youtube_video_playlist'}
    job,_=store.create(payload,'abcdefghijklmnop')
    store.update(job['job_id'],'waiting_for_rate_limit',0,{'retry_at':'2020-01-01T00:00:00+00:00'})
    assert store.requeue_due_quota_jobs('2026-01-01T00:00:00+00:00')==1
    assert store.get(job['job_id'])['status']=='queued'
    assert store.pending_discord_notifications()==[]
