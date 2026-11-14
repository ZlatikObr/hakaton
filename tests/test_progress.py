import json
import threading
import time
import pytest
from fastapi.testclient import TestClient
from tz_audit.api import create_app
from tz_audit.progress import RunControl, RunCancelled
from tz_audit.service import Service
from tz_audit.agent import AgentConfig


def test_cancel_prevents_publication_and_aborts_registered_socket():
    control=RunControl();control.start();aborted=[];published=[]
    control.register_abort(lambda:aborted.append(True))
    assert control.cancel() and aborted==[True]
    with pytest.raises(RunCancelled):control.commit(lambda:published.append(True))
    assert not published
    control.cancelled();assert control.snapshot()['status']=='cancelled'


def test_completion_wins_over_late_cancel():
    control=RunControl();control.start();control.commit(lambda:None)
    assert not control.cancel() and control.snapshot()['status']=='complete'


def test_cancel_after_model_response_does_not_write_or_continue(tmp_path,docx_bytes):
    control=RunControl();control.start()
    class Fake:
        def complete(self,messages,config):
            control.cancel()
            return {'unexpected':'never parsed after cancellation'}
    service=Service(tmp_path)
    job=service.prepare(docx_bytes(['Техническое задание. Мощность 500 Вт.']), 'rules')
    with pytest.raises(RunCancelled):
        service.analyze(job['job_id'],'openrouter',job['release_digest'],client=Fake(),config=AgentConfig(),control=control)
    assert not (service.path(job['job_id'])/'report.json').exists()
    assert not (service.path(job['job_id'])/'corrected.docx').exists()


def test_api_running_and_queued_cancel_and_restart(tmp_path,monkeypatch,docx_bytes):
    entered=threading.Event();release=threading.Event();calls=[]
    original=Service.analyze
    def slow(self,job_id,*args,control=None,**kwargs):
        calls.append(job_id);entered.set()
        try:
            release.wait(5)
            control.check()
            return original(self,job_id,*args,control=control,**kwargs)
        finally:entered.clear()
    monkeypatch.setattr(Service,'analyze',slow)
    client=TestClient(create_app(tmp_path));client.get('/')
    def prepare():
        r=client.post('/jobs',data={'privacy_backend':'rules'},files={'file':('test.docx',docx_bytes(['ТЗ. Поставка оборудования.']))})
        assert r.status_code==200
        return r.json()['job_id']
    first,second=prepare(),prepare()
    try:
        assert client.post(f'/jobs/{first}/run',json={'mode':'offline'}).status_code==202
        assert entered.wait(2)
        assert client.post(f'/jobs/{second}/run',json={'mode':'offline'}).status_code==202
        r=client.post(f'/jobs/{second}/cancel',json={}).json()
        assert r['status']=='cancelled'
        assert client.post(f'/jobs/{first}/cancel',json={}).json()['status']=='cancelling'
        assert client.post(f'/jobs/{first}/run',json={'mode':'offline'}).status_code==409
        release.set()
        for _ in range(100):
            state=client.get(f'/jobs/{first}/status').json()
            if state['status']=='cancelled':break
            time.sleep(.01)
        assert state['status']=='cancelled' and len(state['events'])>=3
        assert second not in calls
        assert client.get(f'/jobs/{first}/corrected.docx').status_code==404
        assert client.post(f'/jobs/{first}/run',json={'mode':'offline'}).status_code==202
        for _ in range(100):
            state=client.get(f'/jobs/{first}/status').json()
            if state['status']=='complete':break
            time.sleep(.01)
        assert state['status']=='complete'
        assert 'ТЗ. Поставка' not in json.dumps(state,ensure_ascii=False)
    finally:release.set()
