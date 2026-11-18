from fastapi.testclient import TestClient
from app import main,store
from app.config import ROOT
import pytest

BACKFILL=(ROOT/'sample_data/backfill.csv').read_text()

def client(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DB_PATH',str(tmp_path/'test.sqlite'));return TestClient(main.app)

def publish_ready(tmp_path,monkeypatch):
    """不带阻断的补传：00001 无线上冲突，只验证发布机制本身。"""
    c=client(tmp_path,monkeypatch)
    rows='id,member,type,points,at,expires,ref,rule,version\nP1,00002,SPEND,10,2026-10-16T09:00:00+08:00,,,常规,1\n'
    return c,c.post('/api/runs',json={'dataset':'clean','backfill_csv':rows}).json()

def test_publish_returns_member_balances(tmp_path,monkeypatch):
    c,run=publish_ready(tmp_path,monkeypatch)
    response=c.post('/api/runs/'+run['manifest']['run_id']+'/publish',json={})
    assert response.status_code==200
    body=response.json()
    assert any(row['member']=='00002' and row['publishable_remaining']==60 for row in body)

def test_republish_no_extra_deduction(tmp_path,monkeypatch):
    c,run=publish_ready(tmp_path,monkeypatch)
    rid=run['manifest']['run_id']
    first=c.post('/api/runs/'+rid+'/publish',json={}).json()
    second=c.post('/api/runs/'+rid+'/publish',json={}).json()
    assert [r['publishable_remaining'] for r in first]==[r['publishable_remaining'] for r in second]
    assert len(first)==len(second)

def test_blocked_member_keeps_old_balance(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    rows='id,member,type,points,at,expires,ref,rule,version\nP1,00002,SPEND,10,2026-10-16T09:00:00+08:00,,,常规,1\n'
    baseline=c.post('/api/runs',json={'dataset':'clean','backfill_csv':rows}).json()
    c.post('/api/runs/'+baseline['manifest']['run_id']+'/publish',json={})
    run=c.post('/api/runs',json={'dataset':'clean','backfill_csv':BACKFILL}).json()
    body=c.post('/api/runs/'+run['manifest']['run_id']+'/publish',json={}).json()
    row=next(r for r in body if r['member']=='00001')
    # 冻结超出账上余额：不发布新值，沿用上一次已发布余额并在状态中标注待核。
    assert row['status']=='待核不发布'
    assert row['publishable_remaining']==row['previous_remaining']==30

def test_publish_blocked_when_unbalanced(tmp_path,monkeypatch):
    c,run=publish_ready(tmp_path,monkeypatch)
    scoped=run['backfill']['points_balanced']
    assert scoped
    bad=dict(run);bad['backfill']=dict(run['backfill'],can_publish=False)
    with pytest.raises(Exception):
        store.publish(str(tmp_path/'test.sqlite'),bad)

def test_failed_publish_keeps_previous(tmp_path,monkeypatch):
    c,run=publish_ready(tmp_path,monkeypatch)
    rid=run['manifest']['run_id']
    first=c.post('/api/runs/'+rid+'/publish',json={}).json()
    broken=dict(run);broken['backfill']=dict(run['backfill'],can_publish=False)
    with pytest.raises(Exception):
        store.publish(str(tmp_path/'test.sqlite'),broken)
    after=c.post('/api/runs/'+rid+'/publish',json={}).json()
    assert [r['publishable_remaining'] for r in after]==[r['publishable_remaining'] for r in first]

def test_publish_refused_without_backfill(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    run=c.post('/api/runs',json={'dataset':'clean'}).json()
    assert c.post('/api/runs/'+run['manifest']['run_id']+'/publish',json={}).status_code==409

def test_stale_publish_rejected(tmp_path,monkeypatch):
    c,run=publish_ready(tmp_path,monkeypatch)
    assert c.post('/api/runs/'+run['manifest']['run_id']+'/publish',json={'revision':99}).status_code==409

def test_backfill_page_lists_members_and_reasons(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch)
    run=c.post('/api/runs',json={'dataset':'clean','backfill_csv':BACKFILL}).json()
    rid=run['manifest']['run_id']
    html=c.get('/backfill?run_id='+rid).text
    assert '会员可发布余额' in html
    assert 'S2' in html and 'OS1' in html
    assert 'DISPLACED' in html or '迟到扣点' in html
