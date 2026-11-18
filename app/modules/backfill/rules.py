"""门店断网补传的发布前核对。

原则：
- 补传的门店消费在相同时刻先于线上事件占用批次，承认顾客已在门店完成的扣点；
- 补传占用批次后，线上订单若整笔无法再落账，不静默作废，而是标记为受影响并待核；
- 受影响且待核的线上订单，其积分从可发布余额中冻结，避免同一积分被两个渠道重复使用；
- 补传订单本身余额不足时同样待核、不扣减，门店收款另行核对；
- 同一补传重复投递按编号去重，同号不同原值按冲突隔离，重跑不新增扣减。
"""
from app.errors import BusinessError
from app.reasons import reject
from app.modules.policy.rules import normalize
from app.modules.lots.rules import build_lots
from app.modules.consumption.rules import replay

FIELDS=['id','member','type','points','at','expires','ref','rule','version']
IDENT=['member','type','points','at','expires','ref','rule','version']


def _reject(state,row,code,detail):
    reject(state,row,code,detail)
    state.setdefault('backfill_rejected',[]).append(state['quarantine'][-1])


def _shape(raw):
    """补传输入统一成与采信阶段一致的字符串行，以便复用同一套校验。"""
    rows=[]
    for index,item in enumerate(raw,1):
        item=item if isinstance(item,dict) else {}
        row={k:str(item.get(k,'')).strip() for k in FIELDS}
        row['_line']=item.get('_line',index)
        row['_channel']='backfill'
        rows.append(row)
    return rows


def _dedupe(state,rows):
    """同一补传事件重复投递只保留一份；同号不同原值按冲突隔离。"""
    by_id={};order=[]
    for row in rows:
        if not row['id']:
            _reject(state,row,'MISSING_ID','补传编号不能为空');continue
        if row['id'] not in by_id: by_id[row['id']]=[];order.append(row['id'])
        by_id[row['id']].append(row)
    kept=[];duplicates=0
    for key in order:
        group=by_id[key]
        if len({tuple(r[k] for k in IDENT) for r in group})>1:
            for row in group: _reject(state,row,'DUPLICATE_CONFLICT','同一补传编号存在不同原值')
        else:
            kept.append(group[0]);duplicates+=len(group)-1
    state['backfill_duplicates']=duplicates
    return kept


def _normalize(state,rows):
    good=[]
    for row in rows:
        try:
            if row['type']=='REFUND':
                raise BusinessError('BACKFILL_TYPE_UNSUPPORTED','本次补传不处理退款事件')
            good.append(normalize(row,state['context']))
        except BusinessError as e:
            _reject(state,row,e.code,e.message)
    return good


def _identity(row):
    return tuple(row.get(k,'') for k in IDENT)


def _merge_base(state,rows):
    """补传编号与既有线上事件重合时按重复或冲突处理，绝不并入第二条扣减。"""
    base={r['id']:r for r in state['records']}
    fresh=[];duplicates=state.get('backfill_duplicates',0)
    for row in rows:
        if row['id'] in base:
            if _identity(row)==_identity(base[row['id']]): duplicates+=1
            else: _reject(state,row,'DUPLICATE_CONFLICT','补传编号与既有事件原值不同')
            continue
        fresh.append(row)
    state['backfill_duplicates']=duplicates
    return fresh


def _allocation_sig(spend):
    return [(a['lot'],a['points']) for a in spend['allocations']] if spend else None


def _caused_by(order,base_spends,comb_spends,backfill_spend_ids):
    """找出先占走该订单原批次的补传消费。"""
    lots={a['lot'] for a in base_spends[order]['allocations']}
    out=[]
    for rid in sorted(backfill_spend_ids):
        spend=comb_spends.get(rid)
        if spend and any(a['lot'] in lots for a in spend['allocations']): out.append(rid)
    return out


def _entry(order,row,channel,kind,detail,points,affected,baseline=None,current=None,caused_by=None):
    return {'order':order,'member':row['member'],'points':points,'at':row.get('at',''),
            'channel':channel,'kind':kind,'detail':detail,
            'affected':affected,'baseline_allocations':baseline,'current_allocations':current,
            'caused_by':caused_by or []}


def apply(state):
    raw=state.get('backfill_input') or []
    good=_merge_base(state,_normalize(state,_dedupe(state,_shape(raw))))

    lots,bad=build_lots([r for r in good if r['type']=='EARN'])
    for row,code,detail in bad: _reject(state,row,code,detail)
    bad_ids={r['id'] for r,_,_ in bad}
    good=[r for r in good if r['id'] not in bad_ids]
    for lot in lots: lot['_channel']='backfill'

    rows_by_id={r['id']:r for r in good}
    base_rows_by_id={r['id']:r for r in state['records']}
    backfill_spend_ids={r['id'] for r in good if r['type']=='SPEND'}

    combined_rows=list(state['records'])+[r for r in good if r['type'] in {'EARN','SPEND'}]
    combined_lots=list(state['lots'])+lots
    base=replay(state['records'],state['lots'],state['context']['as_of'])
    combined=replay(combined_rows,combined_lots,state['context']['as_of'],
                    tiebreak=lambda r:0 if r.get('_channel')=='backfill' else 1)
    base_spends={s['id']:s for s in base['spends']}
    comb_spends={s['id']:s for s in combined['spends']}
    base_refunds={r['id']:r for r in base['refunds']}
    comb_refunds={r['id']:r for r in combined['refunds']}

    affected=[];pending=[];held={}
    # 1) 线上订单被迟到扣点挤出：受影响且待核，冻结其全部积分。
    for order in sorted(set(base_spends)):
        b=base_spends[order];c=comb_spends.get(order);row=base_rows_by_id[order]
        if c is None:
            ent=_entry(order,row,'online','DISPLACED',
                       '迟到补传先占用原可用批次，该线上订单整笔无法落账',
                       b['points'],True,_allocation_sig(b),None,
                       _caused_by(order,base_spends,comb_spends,backfill_spend_ids))
            affected.append(ent);pending.append(ent)
            held[row['member']]=held.get(row['member'],0)+b['points']
        elif _allocation_sig(b)!=_allocation_sig(c):
            affected.append(_entry(order,row,'online','REALLOCATED',
                                   '迟到扣点改变批次分配，订单仍足额落账',
                                   b['points'],True,_allocation_sig(b),_allocation_sig(c),
                                   _caused_by(order,base_spends,comb_spends,backfill_spend_ids)))
    # 2) 原消费被挤出后，其线上退款无法回拨：待核。
    for refund in sorted(set(base_refunds)-set(comb_refunds)):
        original=base_refunds[refund]
        pending.append(_entry(refund,{'member':original['member'],'at':original.get('at','')},'online',
                              'REFUND_ORPHANED','原消费被迟到扣点挤出，退款暂不落账',original['points'],False))
    # 3) 补传订单自身余额不足：待核，不扣减；其门店已消费的份额从可发布余额冻结，避免线上重复使用。
    for order in sorted(backfill_spend_ids-set(comb_spends)):
        row=rows_by_id[order]
        pending.append(_entry(order,row,'backfill','BACKFILL_UNFUNDED',
                              '补传订单余额不足，暂不扣减，门店收款另行核对',row['points'],False))
        held[row['member']]=held.get(row['member'],0)+row['points']
    pending.sort(key=lambda x:(x['member'],x['at'],x['order']))

    prior={};now={}
    for lot in base['lots']: prior[lot['member']]=prior.get(lot['member'],0)+lot['remaining']
    for lot in combined['lots']: now[lot['member']]=now.get(lot['member'],0)+lot['remaining']

    member_rows=[]
    for member in sorted(set(prior)|set(now)):
        before=prior.get(member,0);freeze=held.get(member,0);ledger=now.get(member,0)
        shortfall=max(0,freeze-ledger)
        aff=[e for e in affected if e['member']==member]
        pend=[e for e in pending if e['member']==member]
        # 冻结份额大于账上可用时无法确定一个非负余额：本会员保留旧余额，待人工核对。
        blocked=shortfall>0
        publishable=before if blocked else max(0,ledger-freeze)
        status='待核不发布' if blocked else ('可发布·有待核' if pend else '可发布')
        member_rows.append({'member':member,'prior_remaining':before,
            'ledger_remaining':ledger,'frozen':freeze,'publishable_remaining':publishable,
            'delta':publishable-before,'deficit':shortfall,'blocked':blocked,'status':status,
            'affected_orders':[e['order'] for e in aff],
            'pending_orders':[e['order'] for e in pend],
            'pending_reasons':sorted({f"{e['order']}:{e['kind']}" for e in pend})})

    issued=sum(lot['issued'] for lot in combined_lots)
    remaining=sum(lot['remaining'] for lot in combined['lots'])
    spent=sum(s['points'] for s in combined['spends'])
    refunded=sum(r['points'] for r in combined['refunds'])
    expired=combined['expired']+sum(r['expired_refund'] for r in combined['refunds'])
    points_balanced=issued==remaining+spent-refunded+expired
    row_balanced=state['balance']['row_balanced']
    refund_issues=list(state.get('refund_issues',[]))
    can_publish=bool(points_balanced and row_balanced and not refund_issues)

    result={'can_publish':can_publish,'row_balanced':row_balanced,'points_balanced':points_balanced,
            'refund_issues':refund_issues,'duplicates':state.get('backfill_duplicates',0),
            'has_blocked':any(m['blocked'] for m in member_rows),
            'affected':affected,'pending':pending,'members':member_rows,
            'combined':{'issued':issued,'remaining':remaining,'spent':spent,
                        'refunded':refunded,'expired':expired},
            'honored_backfill':sorted(backfill_spend_ids & set(comb_spends)),
            'rejected':state.get('backfill_rejected',[])}
    state['backfill']=result
    state['views']['backfill']={'title':'补传发布','rows':member_rows,
        'metrics':{'会员数':len(member_rows),'可发布余额':sum(m['publishable_remaining'] for m in member_rows),
                   '受影响订单':len(affected),'待核订单':len(pending)}}
