#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_facts.py — 第四層 Fact 抽取 pilot(中文法條 OpenIE)

範圍:殺人罪章 §271–276 + 傷害罪章 §277–287(排除已刪除)

Pipeline(對應專題文件的技術路線):
  ① 切分:條 → 項 → 子句(以「;」「。」分)
  ② 前處理:
     - 條號正規化:「第二百七十七條第一項」→「第277條第1項」
     - 指代消解:「前項/前二項/前條/前二條/第一項」→ 絕對條項號
     - 省略補全:「致重傷者,處…」補回前一子句的基礎行為
  ③ 抽取規則(傳統 OpenIE:句式 pattern → SPO):
     R1 者字句:「(構成要件)者,(而為主體,)?處(效果)」→ (行為, 法定刑, 刑度)
     R2 加重減免:「…者,(依…規定)?加重其刑/得免除其刑」
     R3 未遂句:「第X條第Y項之未遂犯罰之」→ (罪, 未遂處罰, 成立)
     R4 加重結果:主詞尾為「(因而)致人於死/致重傷」→ (基礎罪, 加重結果, 結果)
     R5 訴追條件:「…之罪,須告訴乃論」+ 但書例外
  ④ 刑度正規化:「處三年以上十年以下有期徒刑」→ {min, max, types, fine}
  ⑤ 輸出:facts_pilot.json / facts_review.md(gold standard 素材)
         / C_facts_oneshot.cypher(Fact 節點 + EXTRACTED_FROM)

謂詞白名單(防止關係名增生):
  法定刑 / 刑之加重 / 刑之減免 / 未遂處罰 / 加重結果 / 訴追條件

用法:python extract_facts.py ../data/C0000001.json
"""
import re
import sys
import json
from collections import Counter

# ------------------------------------------------------------------
# 中文數字 → int
# ------------------------------------------------------------------
_D = {'零': 0, '〇': 0, '一': 1, '二': 2, '兩': 2, '三': 3, '四': 4,
      '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
_U = {'十': 10, '百': 100, '千': 1000}


def cn2int(s):
    s = s.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    sec = num = 0
    for ch in s:
        if ch in _D:
            num = _D[ch]
        elif ch in _U:
            sec += (num or 1) * _U[ch]
            num = 0
        else:
            return None
    return sec + num


_CN = '零〇一二三四五六七八九十百千兩'

# ==================================================================
# ② 前處理
# ==================================================================


def normalize_refs(text, art):
    """「第二百七十七條第一項」→「第277條第1項」;同條內「第X項」補上條號"""
    def _art(m):
        a = cn2int(m.group(1))
        sub = cn2int(m.group(2)) if m.group(2) else None
        p = cn2int(m.group(3)) if m.group(3) else None
        out = f'第{a}' + (f'-{sub}' if sub else '') + '條'
        if p:
            out += f'第{p}項'
        return out
    text = re.sub(
        rf'第([{_CN}\d]+)條(?:之([{_CN}\d]+))?(?:第([{_CN}\d]+)項)?',
        _art, text)
    text = re.sub(rf'(?<!條)第([{_CN}\d]+)項',
                  lambda m: f'第{art}條第{cn2int(m.group(1))}項', text)
    return text


def resolve_relative(text, art, pno, prev_arts):
    """指代消解:前條/前二條/前三條/前項/前二項/前三項 → 絕對號"""
    for n, word in [(3, '前三條'), (2, '前二條'), (1, '前條')]:
        if word in text and len(prev_arts) >= n:
            repl = '、'.join(f'第{a}條' for a in prev_arts[-n:])
            text = text.replace(word, repl)
    for n, word in [(3, '前三項'), (2, '前二項'), (1, '前項')]:
        if word in text and pno - n >= 1:
            repl = '、'.join(f'第{art}條第{i}項' for i in range(pno - n, pno))
            text = text.replace(word, repl)
    return text


def split_paras(text):
    """條文 → 項(款併回前一項)"""
    lines = [l.strip() for l in text.replace('\r', '').split('\n') if l.strip()]
    paras = []
    for ln in lines:
        if re.match(r'^[一二三四五六七八九十]+、', ln) and paras:
            paras[-1] += ln
        else:
            paras.append(ln)
    return paras

# ==================================================================
# ④ 刑度正規化
# ==================================================================


def parse_penalty(eff):
    p = {'raw': eff}
    types = [t for t in ('死刑', '無期徒刑', '有期徒刑', '拘役', '罰金')
             if t in eff]
    if types:
        p['types'] = types
    m = re.search(rf'([{_CN}\d]+)(年|月)以上', eff)
    if m:
        p['min'] = f'{cn2int(m.group(1))}{m.group(2)}'
    m = re.search(rf'([{_CN}\d]+)(年|月)以下有期徒刑', eff)
    if m:
        p['max'] = f'{cn2int(m.group(1))}{m.group(2)}'
    m = re.search(rf'([{_CN}\d]+)萬元以下罰金', eff)
    if m:
        p['fine_max'] = cn2int(m.group(1)) * 10000
    if '併科' in eff:
        p['fine_mode'] = '得併科' if '得併科' in eff else '併科'
    return p

# ==================================================================
# ③ 抽取規則
# ==================================================================

# R1/R2 者字句:者 之後可插一個主體 NP(§283「…者,在場助勢之人,處…」),
#            效果可帶「依…規定」前綴(§286III)
_EFF_HEAD = r'(?:處|科|依|加重其刑|得加重|減輕其刑|得減輕|免除其刑|得免除)'
_RE_ZHE = re.compile(
    rf'^(?P<act>.+?)者，?(?:(?P<who>[^，]{{1,15}})，)?(?P<eff>{_EFF_HEAD}.+)$')
# R3 未遂句(消解後)
_RE_ATT = re.compile(r'^(?P<refs>(?:第[\d\-]+條(?:第\d+項)?[、]?)+)之未遂犯罰之$')
# R4 加重結果:主詞尾部的結果語
_RE_RESULT = re.compile(r'^(?P<base>.+?)，?(?:因而)?(?P<res>致人於死|致重傷|致死)$')
# 引用(消解後)
_RE_REF = re.compile(r'第[\d\-]+條(?:第\d+項)?')


def classify_eff(eff):
    if eff.startswith(('處', '科')):
        return '法定刑'
    if '加重' in eff:
        return '刑之加重'
    return '刑之減免'


def extract_article(art, text, prev_arts, facts):
    for pno, ptext in enumerate(split_paras(text), 1):
        ptext = normalize_refs(ptext, art)
        ptext = resolve_relative(ptext, art, pno, prev_arts)
        last_base = None                       # 省略補全用(逐項重置)
        for clause in ptext.replace('。', '；').split('；'):
            clause = clause.strip().strip('，')
            if not clause:
                continue

            # --- R5b 但書例外(告訴乃論的除外)---
            if clause.startswith('但') and '不在此限' in clause:
                subj = clause[1:].replace('，不在此限', '').rstrip('者')
                add(facts, art, pno, clause, subj, '訴追條件', '非告訴乃論(但書)')
                continue
            if clause.startswith('但'):        # 一般但書:去「但」後照常抽
                clause = clause[1:]

            # --- 省略補全:「(因而)致…者,處…」補回基礎行為 ---
            if re.match(r'^(因而)?致', clause) and last_base:
                clause = last_base + '，' + clause
            sent = clause

            # --- R3 未遂句 ---
            m = _RE_ATT.match(clause.replace('，', ''))
            if m:
                for ref in _RE_REF.findall(m.group('refs')):
                    add(facts, art, pno, sent, f'{ref}之罪', '未遂處罰', '成立')
                continue

            # --- R5 訴追條件 ---
            if '告訴乃論' in clause:
                for ref in _RE_REF.findall(clause):
                    add(facts, art, pno, sent, f'{ref}之罪', '訴追條件', '告訴乃論')
                continue

            # --- R1/R2 者字句 ---
            m = _RE_ZHE.match(clause)
            if m:
                act, who, eff = m.group('act'), m.group('who'), m.group('eff')
                subj = act + (f'，{who}' if who else '')
                pred = classify_eff(eff)
                f = add(facts, art, pno, sent, subj, pred, eff)
                if pred == '法定刑':
                    f['penalty'] = parse_penalty(eff)
                # --- R4 加重結果 + 記住基礎行為 ---
                # 注意:「因過失致人於死」(§276) 的致死是基本構成要件,
                # 只有「犯○罪因而致…」才是加重結果犯 → 要求 base 含「犯…之罪」
                bm = _RE_RESULT.match(act)
                if bm and bm.group('base'):
                    last_base = bm.group('base')
                    if re.search(r'犯.+之罪', bm.group('base')):
                        add(facts, art, pno, sent,
                            bm.group('base'), '加重結果', bm.group('res'))
                else:
                    last_base = act
                continue

            # --- 沒中任何規則:記為 UNMATCHED 供錯誤分析 ---
            add(facts, art, pno, sent, clause, 'UNMATCHED', '')


_seen = set()


def add(facts, art, pno, sent, s, p, o):
    s, o = s.strip('，, '), o.strip('，, ')
    key = (s, p, o, art)
    if key in _seen:                # 去重(但書切句後可能重覆掃到)
        return {}
    _seen.add(key)
    f = {'fid': f'F-{art}-{pno}-{len(facts) + 1}',
         'subject': s, 'predicate': p, 'object': o,
         'article': art, 'para': pno, 'sentence': sent}
    facts.append(f)
    return f

# ==================================================================
# ⑤ 輸出
# ==================================================================


def esc(v):
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace('\\', '\\\\').replace("'", "\\'") + "'"


def to_cypher(facts):
    rows = []
    for f in facts:
        if f['predicate'] == 'UNMATCHED':
            continue
        props = {k: f[k] for k in
                 ('fid', 'subject', 'predicate', 'object', 'article', 'para', 'sentence')}
        if 'penalty' in f:
            pen = f['penalty']
            props['penalty_raw'] = pen['raw']
            for k in ('min', 'max', 'fine_max'):
                if k in pen:
                    props[f'penalty_{k}'] = pen[k]
            if 'types' in pen:                      # 刑種:死刑/無期/有期/拘役/罰金
                props['penalty_types'] = '、'.join(pen['types'])
            if 'fine_mode' in pen:                  # 併科 / 得併科
                props['penalty_fine_mode'] = pen['fine_mode']
            for k in ('min', 'max'):                # 月數換算(供數值比較)
                if k in pen:
                    n = int(pen[k][:-1])
                    props[f'penalty_{k}_months'] = n * 12 if pen[k].endswith('年') else n
        rows.append(props)
    seg = ['// === 檔C:第四層 Fact 節點(pilot,先執行下行約束,再整段執行 UNWIND)===',
           'CREATE CONSTRAINT fact_fid IF NOT EXISTS FOR (n:Fact) REQUIRE n.fid IS UNIQUE;']
    arr = ', '.join('{' + ', '.join(f'{k}: {esc(v)}' for k, v in r.items()) + '}'
                    for r in rows)
    seg.append(f'UNWIND [{arr}] AS f MERGE (n:Fact {{fid: f.fid}}) SET n += f')
    seg.append('WITH count(*) AS _')
    pairs = ', '.join(f"['{r['fid']}','{r['article']}']" for r in rows)
    seg.append(f'UNWIND [{pairs}] AS p MATCH (f:Fact {{fid:p[0]}}), '
               '(a:Article {number:p[1]}) MERGE (f)-[:EXTRACTED_FROM]->(a)')
    seg.append('WITH count(*) AS _ MATCH (f:Fact) RETURN count(f) AS Fact數;')
    return '\n'.join(seg) + '\n'


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else '../data/C0000001.json'
    with open(path, encoding='utf-8') as fp:
        data = json.load(fp)
    if isinstance(data, list):
        data = data[0]

    facts, prev_arts = [], []
    for it in data['法規內容']:
        if not it.get('條號'):
            continue
        m = re.search(r'第\s*(\d+)(?:-(\d+))?\s*條', it['條號'])
        if not m:
            continue
        base = int(m.group(1))
        art = m.group(1) + (f'-{m.group(2)}' if m.group(2) else '')
        content = (it.get('條文內容') or '').replace('\r', '').strip()
        if 271 <= base <= 287 and content != '（刪除）':
            extract_article(art, content, prev_arts, facts)
        prev_arts.append(art)

    ok = [f for f in facts if f['predicate'] != 'UNMATCHED']
    bad = [f for f in facts if f['predicate'] == 'UNMATCHED']

    with open('facts_pilot.json', 'w', encoding='utf-8') as fp:
        json.dump(facts, fp, ensure_ascii=False, indent=1)
    with open('C_facts_oneshot.cypher', 'w', encoding='utf-8') as fp:
        fp.write(to_cypher(facts))
    with open('facts_review.md', 'w', encoding='utf-8') as fp:
        fp.write('# Fact 抽取人工複核表(pilot §271–287)\n\n'
                 '打勾=正確;打叉請寫正確答案 → 這張表就是 gold standard。\n\n'
                 '| fid | 主詞 (S) | 謂詞 (P) | 受詞 (O) | 出處 | 正確? |\n'
                 '|---|---|---|---|---|---|\n')
        for f in facts:
            fp.write(f"| {f['fid']} | {f['subject']} | {f['predicate']} | "
                     f"{f['object'][:40]} | §{f['article']}({f['para']}) |  |\n")

    print(f'抽取完成:{len(ok)} 個三元組,{len(bad)} 句未匹配 (UNMATCHED)')
    print('謂詞分布:', dict(Counter(f['predicate'] for f in facts)))
    print()
    for f in facts:
        mark = ' ⚠' if f['predicate'] == 'UNMATCHED' else ''
        print(f"§{f['article']}({f['para']}) ({f['subject']} | "
              f"{f['predicate']} | {f['object'][:38]}){mark}")


if __name__ == '__main__':
    main()
