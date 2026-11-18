from copy import deepcopy
from app.engine import evaluate
from app.config import ROOT
import pytest

BACKFILL=(ROOT/'sample_data/backfill.csv').read_text()

@pytest.fixture
def backfilled(clean):
    return evaluate({'dataset':'clean','backfill_csv':BACKFILL})

def member(state,mid):
    return next(m for m in state['backfill']['members'] if m['member']==mid)

def test_displaced_online_order(tmp_path):
    s=evaluate({'dataset':'clean','backfill_csv':BACKFILL})
    displaced={e['order'] for e in s['backfill']['affected'] if e['kind']=='DISPLACED'}
    assert displaced=={'S2','S7'}
    assert member(s,'00001')['affected_orders']==['S2']

def test_late_deduction_names_cause(tmp_path):
    s=evaluate({'dataset':'clean','backfill_csv':BACKFILL})
    s2=next(e for e in s['backfill']['affected'] if e['order']=='S2')
    assert s2['caused_by']==['OS1']
    # 线上 S2 原本整笔落在尚未到期的 E2 上。
    assert s2['baseline_allocations']==[('E2',50)]

def test_unfunded_backfill_pending(tmp_path):
    rows='id,member,type,points,at,expires,ref,rule,version\nX1,00001,SPEND,9999,2026-11-02T09:30:00+08:00,,,常规,1\n'
    s=evaluate({'dataset':'clean','backfill_csv':rows})
    entry=next(e for e in s['backfill']['pending'] if e['order']=='X1')
    assert entry['kind']=='BACKFILL_UNFUNDED'
    assert entry['channel']=='backfill'

def test_duplicate_replay_no_extra_deduction(tmp_path):
    rows='id,member,type,points,at,expires,ref,rule,version\nX1,00002,SPEND,10,2026-10-16T09:00:00+08:00,,,常规,1\n'*1
    once=evaluate({'dataset':'clean','backfill_csv':rows})
    doubled=evaluate({'dataset':'clean','backfill_csv':rows+rows})
    assert member(once,'00002')['publishable_remaining']==member(doubled,'00002')['publishable_remaining']
    assert doubled['backfill']['duplicates']==1

def test_conflicting_backfill_quarantined(tmp_path):
    rows=('id,member,type,points,at,expires,ref,rule,version\n'
          'X9,00002,SPEND,5,2026-10-16T09:00:00+08:00,,,常规,1\n'
          'X9,00002,SPEND,6,2026-10-16T09:00:00+08:00,,,常规,1\n')
    s=evaluate({'dataset':'clean','backfill_csv':rows})
    codes=[r['code'] for r in s['backfill']['rejected'] if r['id']=='X9']
    assert codes==['DUPLICATE_CONFLICT','DUPLICATE_CONFLICT']
    assert 'X9' not in s['backfill']['honored_backfill']

def test_refund_backfill_out_of_scope(tmp_path):
    rows='id,member,type,points,at,expires,ref,rule,version\nX1,00001,REFUND,10,2026-11-02T09:00:00+08:00,,S1,常规,1\n'
    s=evaluate({'dataset':'clean','backfill_csv':rows})
    assert next(r for r in s['backfill']['rejected'] if r['id']=='X1')['code']=='BACKFILL_TYPE_UNSUPPORTED'

def test_stable_run_id_across_rerun(tmp_path,monkeypatch):
    from app import main,store
    monkeypatch.setattr(main,'DB_PATH',str(tmp_path/'db.sqlite'))
    from fastapi.testclient import TestClient
    c=TestClient(main.app)
    a=c.post('/api/runs',json={'dataset':'clean','backfill_csv':BACKFILL}).json()
    rid=a['manifest']['run_id']
    b=c.post('/api/runs/'+rid+'/rerun',json={}).json()
    assert b['manifest']['run_id']==rid
    assert b['backfill']['members']==a['backfill']['members']

def test_balance_conserved(tmp_path):
    s=evaluate({'dataset':'clean','backfill_csv':BACKFILL})
    c=s['backfill']['combined']
    assert c['issued']==c['remaining']+c['spent']-c['refunded']+c['expired']
    assert s['backfill']['points_balanced']
