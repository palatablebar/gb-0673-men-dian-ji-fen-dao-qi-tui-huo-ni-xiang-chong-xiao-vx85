from fastapi import Request
from fastapi.templating import Jinja2Templates
import csv, io
from app.config import ROOT, META
from app.engine import evaluate
from app import store

templates=Jinja2Templates(directory=str(ROOT/'app/templates'))

def _backfill_text(state):
    rows=state.get('backfill_input') or []
    if not rows: return ''
    fields=['id','member','type','points','at','expires','ref','rule','version']
    buffer=io.StringIO();writer=csv.DictWriter(buffer,fieldnames=fields,extrasaction='ignore')
    writer.writeheader()
    for row in rows: writer.writerow({k:(row.get(k,'') if isinstance(row,dict) else '') for k in fields})
    return buffer.getvalue()

def _cards(state,key):
    b=state['balance']
    if key=='backfill' and 'backfill' in state:
        f=state['backfill'];tot=lambda n:sum(m[n] for m in f['members'])
        return [('发放积分',f['combined']['issued']),('账上可用',f['combined']['remaining']),
                ('冻结待核',tot('frozen')),('可发布余额',tot('publishable_remaining')),
                ('待核订单',len(f['pending']))]
    return [('发放积分',b['issued']),('可用积分',b['remaining']),('消费积分',b['spent']),
            ('退款积分',b['refunded']),('到期积分',b['expired'])]

def render(request:Request,key):
    from app.main import DB_PATH
    run_id=request.query_params.get('run_id')
    if run_id:
        state=store.get(DB_PATH,run_id)
    else:
        state=store.save(DB_PATH,evaluate({'dataset':request.query_params.get('dataset','dirty')}))
    publication=store.publication_for(DB_PATH,state['manifest']['run_id'])
    return templates.TemplateResponse(request=request,name=f'{key}.html',context={
        'meta':META,'key':key,'state':state,'view':state['views'][key],
        'cards':_cards(state,key),'publication':publication,
        'backfill_text':_backfill_text(state)})
