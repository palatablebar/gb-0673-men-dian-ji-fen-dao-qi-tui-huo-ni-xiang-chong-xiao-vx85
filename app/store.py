import sqlite3, json
from pathlib import Path
from app.errors import BusinessError

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(path,timeout=15)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '待复核')")
    # 发布记录独立于批次结果：只有事务提交成功才写入新余额，失败时旧发布仍生效。
    con.execute("CREATE TABLE IF NOT EXISTS publications (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, member TEXT NOT NULL, publishable_remaining INTEGER NOT NULL, frozen INTEGER NOT NULL, previous_remaining INTEGER, status TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS publications_run_member ON publications(run_id,member)")
    return con

def save(path, result):
    run_id=result['manifest']['run_id']
    with connect(path) as con:
        con.execute('INSERT OR IGNORE INTO runs(id,digest,result) VALUES(?,?,?)', (run_id,result['manifest']['sha256'],json.dumps(result,ensure_ascii=False)))
    return get(path,run_id)

def get(path, run_id):
    with connect(path) as con: row=con.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not row: raise BusinessError('NOT_FOUND','批次不存在',404)
    result=json.loads(row['result']);result.update(revision=row['revision'],status=row['status'])
    return result

def history(path):
    with connect(path) as con:
        return [dict(r) for r in con.execute('SELECT id,revision,status FROM runs ORDER BY rowid DESC LIMIT 100')]

def review(path,run_id,revision):
    with connect(path) as con:
        cur=con.execute('UPDATE runs SET revision=revision+1,status=? WHERE id=? AND revision=?',('已复核',run_id,revision))
        if cur.rowcount!=1:
            if not con.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():
                raise BusinessError('NOT_FOUND','批次不存在',404)
            raise BusinessError('STALE_VERSION','复核版本已更新，请刷新',409)
    return get(path,run_id)

def latest_publication(path):
    with connect(path) as con:
        rows=con.execute('SELECT * FROM publications WHERE run_id=(SELECT run_id FROM publications ORDER BY id DESC LIMIT 1) ORDER BY member').fetchall()
    return [dict(r) for r in rows]

def publication_for(path,run_id):
    with connect(path) as con:
        rows=con.execute('SELECT * FROM publications WHERE run_id=? ORDER BY member',(run_id,)).fetchall()
    return [dict(r) for r in rows]

def member_publication(path,run_id,member):
    with connect(path) as con:
        row=con.execute('SELECT * FROM publications WHERE run_id=? AND member=? ORDER BY id DESC LIMIT 1',(run_id,member)).fetchone()
    return dict(row) if row else None

def publish(path,result):
    """把一次补传的可发布余额整批落库。

    每一行是一条余额快照而非增量扣减，因此同一批次重跑发布只覆盖同一行，不新增扣减；
    整批在一个事务内提交，任何异常都回滚，上一次已发布余额继续生效。
    被待核阻断的会员不覆盖旧余额，只记录待核状态。
    """
    backfill=result.get('backfill')
    if not backfill: raise BusinessError('PUBLISH_INCOMPLETE','批次缺少补传结果')
    if not result.get('backfill_input'): raise BusinessError('PUBLISH_INCOMPLETE','该批次没有补传事件，无需发布',409)
    if not backfill['can_publish']: raise BusinessError('PUBLISH_BLOCKED','当前批次不可发布，请先处理复核项',409)
    run_id=result['manifest']['run_id'];members=backfill['members']
    with connect(path) as con:
        for m in members:
            prev=con.execute('SELECT publishable_remaining FROM publications WHERE member=? AND run_id<>? ORDER BY id DESC LIMIT 1',(m['member'],run_id)).fetchone()
            if m['blocked']:
                # 阻断会员不发布新值：有旧发布则沿用旧值并标注待核，从未发布过则保持无发布记录。
                if prev is None: continue
                con.execute('INSERT INTO publications(run_id,member,publishable_remaining,frozen,previous_remaining,status) VALUES(?,?,?,?,?,?) '
                            'ON CONFLICT(run_id,member) DO UPDATE SET publishable_remaining=excluded.publishable_remaining,frozen=excluded.frozen,previous_remaining=excluded.previous_remaining,status=excluded.status',
                    (run_id,m['member'],prev['publishable_remaining'],m['frozen'],prev['publishable_remaining'],m['status']))
                continue
            con.execute('INSERT INTO publications(run_id,member,publishable_remaining,frozen,previous_remaining,status) VALUES(?,?,?,?,?,?) '
                        'ON CONFLICT(run_id,member) DO UPDATE SET publishable_remaining=excluded.publishable_remaining,frozen=excluded.frozen,previous_remaining=excluded.previous_remaining,status=excluded.status',
                (run_id,m['member'],m['publishable_remaining'],m['frozen'],prev['publishable_remaining'] if prev else m['prior_remaining'],m['status']))
    return latest_publication(path)
