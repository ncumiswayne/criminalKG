#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_concepts.py — 第五層:總則概念錨定層(Concept Anchoring Layer)

依 concepts.json 詞表建立:
  (:Concept {cid, name, def_article, notes, defines_count, uses_count})
  (總則定義條:Article)-[:DEFINES {role}]->(:Concept)
  (使用條:Article)-[:USES {trigger, basis, para, applies, review, reason?}]->(:Concept)

USES 產生方式:
  1. 播種(seed):C-未遂 直接取 parser 的 punishes_attempt 屬性(95 條,
     「未遂犯罰之」句式規則抽取、已人工複核)→ applies:true, review:'approved'
  2. 詞面掃描:其餘概念以觸發詞掃 402 條有效條文(排除各概念自己的定義條)
     → 候選 applies:true, review:'pending'(待人工複核)
  3. 裁決覆寫(overrides):詞面出現但法律上不適用者(§275/§282 教唆幫助自殺自傷、
     §107 幫助行為正犯化)→ applies:false, review:'rejected',保留邊作為否定知識

輸出(皆在本目錄):
  D_concepts_oneshot.cypher   — 檔內三步驟:約束 → 清空重建 → 整段灌入(先跑完檔A)
  uses_review_generated.md    — 複核表底稿(自動產生會覆蓋;人工複核維護 uses_review.md)

用法:python build_concepts.py
"""
import os
import re
import sys
import json
from collections import Counter

_DIR = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(_DIR, '..', 'data', 'C0000001.json')
sys.path.insert(0, os.path.join(_DIR, '..', 'parser'))
from moj_law_to_kg import parse, esc  # noqa: E402


def load_articles(data):
    """[(條號 '271' / '272-1', 條文全文)],排除已刪除條文"""
    arts = []
    for it in data.get('法規內容', []):
        no = it.get('條號')
        if not no:
            continue
        m = re.search(r'第\s*(\d+)(?:-(\d+))?\s*條', no)
        if not m:
            continue
        art = m.group(1) + (f'-{m.group(2)}' if m.group(2) else '')
        txt = (it.get('條文內容') or '').replace('\r', '').strip()
        if txt != '（刪除）':
            arts.append((art, txt))
    return arts


def first_hit(text, trigger):
    """回傳 (項次, 含觸發詞之該行(截80字)) — 取第一個命中行作 basis"""
    for pno, line in enumerate(text.split('\n'), 1):
        if trigger in line:
            return pno, line.strip()[:80]
    return None, None


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')

    with open(os.path.join(_DIR, 'concepts.json'), encoding='utf-8') as f:
        vocab = json.load(f)
    with open(_DATA, encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, list):
        data = data[0]

    arts = load_articles(data)
    _, nodes, _, _ = parse(data)
    attempt_arts = [p['number'] for lbl, c, p in nodes
                    if lbl == 'Article' and p.get('punishes_attempt')]

    concepts, defines, uses = [], [], []
    for c in vocab['concepts']:
        cid = c['cid']
        def_arts = {d['article'] for d in c['defines']}
        for d in c['defines']:
            defines.append({'acode': f"刑法-{d['article']}",
                            'cid': cid, 'role': d['role']})
        overrides = {o['article']: o for o in c.get('overrides', [])}

        # 播種:punishes_attempt → USES(approved)
        if c.get('seed') == 'punishes_attempt':
            for art in attempt_arts:
                if art in def_arts:
                    continue
                uses.append({'acode': f'刑法-{art}', 'art': art, 'cid': cid,
                             'trigger': '未遂犯罰之',
                             'basis': '未遂犯罰之(punishes_attempt 屬性播種)',
                             'para': 0, 'applies': True, 'review': 'approved',
                             'reason': None})
        # 詞面掃描 → 候選 USES
        for trig in c.get('triggers', []):
            for art, txt in arts:
                if art in def_arts or trig not in txt:
                    continue
                if any(u['art'] == art and u['cid'] == cid for u in uses):
                    continue                      # 同條同概念只建一條邊
                pno, basis = first_hit(txt, trig)
                ov = overrides.get(art)
                uses.append({'acode': f'刑法-{art}', 'art': art, 'cid': cid,
                             'trigger': trig, 'basis': basis, 'para': pno,
                             'applies': ov['applies'] if ov else True,
                             'review': 'rejected' if ov and not ov['applies']
                                       else 'pending',
                             'reason': ov.get('reason') if ov else None})

        n_uses = sum(1 for u in uses if u['cid'] == cid)
        concepts.append({'cid': cid, 'name': c['name'],
                         'def_article': c['def_article'], 'notes': c['notes'],
                         'defines_count': len(c['defines']),
                         'uses_count': n_uses})

    # ---------------- D_concepts_oneshot.cypher ----------------
    def cmap(d):
        return '{' + ', '.join(f'{k}: {esc(v)}' for k, v in d.items()
                               if v is not None) + '}'

    seg = ['// === 檔D:第五層 概念錨定層(Concept + DEFINES/USES)===',
           '// 步驟1(單獨執行):約束',
           'CREATE CONSTRAINT concept_cid IF NOT EXISTS '
           'FOR (n:Concept) REQUIRE n.cid IS UNIQUE;',
           '// 步驟2(單獨執行):清空舊概念層整批重建,重貼冪等',
           'MATCH (c:Concept) DETACH DELETE c;',
           '// 步驟3:以下整段一次執行(需先跑完檔A建立 Article)']
    seg.append('UNWIND [' + ', '.join(cmap(c) for c in concepts) + '] AS c '
               'MERGE (n:Concept {cid: c.cid}) SET n += c')
    seg.append('WITH count(*) AS _')
    seg.append('UNWIND [' + ', '.join(cmap(d) for d in defines) + '] AS d '
               'MATCH (a:Article {code: d.acode}), (c:Concept {cid: d.cid}) '
               'MERGE (a)-[r:DEFINES]->(c) SET r.role = d.role')
    seg.append('WITH count(*) AS _')
    rows = []
    for u in uses:
        props = {k: u[k] for k in
                 ('trigger', 'basis', 'para', 'applies', 'review', 'reason')
                 if u[k] is not None}
        rows.append('{acode: ' + esc(u['acode']) + ', cid: ' + esc(u['cid'])
                    + ', props: ' + cmap(props) + '}')
    seg.append('UNWIND [' + ', '.join(rows) + '] AS u '
               'MATCH (a:Article {code: u.acode}), (c:Concept {cid: u.cid}) '
               'MERGE (a)-[r:USES]->(c) SET r += u.props')
    seg.append('WITH count(*) AS _ MATCH (c:Concept) '
               'RETURN count(c) AS Concept數;')
    with open(os.path.join(_DIR, 'D_concepts_oneshot.cypher'), 'w',
              encoding='utf-8') as f:
        f.write('\n'.join(seg) + '\n')

    # ---------------- 複核表底稿 ----------------
    with open(os.path.join(_DIR, 'uses_review_generated.md'), 'w',
              encoding='utf-8') as f:
        f.write('# USES 邊複核表底稿(概念錨定層 pilot)\n\n'
                '> 本檔為 build_concepts.py 自動產生,重跑會覆蓋。\n'
                '> 人工複核請維護 uses_review.md,勿直接改本檔。\n'
                '> 複核優先序:applies:false 全查 > pending 抽查(誤導 > 缺失)。\n'
                '> seed 列(approved)為 punishes_attempt 屬性播種,已經人工複核。\n\n'
                '| 條 | 概念 | 觸發詞 | 觸發句 | applies | review | 理由 | 查核 |\n'
                '|---|---|---|---|---|---|---|---|\n')
        order = {'rejected': 0, 'pending': 1, 'approved': 2}
        for u in sorted(uses, key=lambda u: (order[u['review']], u['cid'],
                                             int(u['art'].split('-')[0]))):
            f.write(f"| §{u['art']} | {u['cid']} | {u['trigger']} | "
                    f"{(u['basis'] or '')[:45]} | {u['applies']} | "
                    f"{u['review']} | {(u['reason'] or '')[:60]} |  |\n")

    # ---------------- 統計 ----------------
    n_false = sum(1 for u in uses if not u['applies'])
    print(f'概念 {len(concepts)} 個;DEFINES {len(defines)} 條;'
          f'USES {len(uses)} 條(applies:false {n_false} 條)')
    print('各概念 USES:', dict(Counter(u['cid'] for u in uses)))
    print('review 分布:', dict(Counter(u['review'] for u in uses)))


if __name__ == '__main__':
    main()
