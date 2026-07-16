#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_facts.py — 第四層 Fact 抽取(中文法條 OpenIE)

範圍(RANGES):
  總則 §1–99(選擇性:定義/責任/未遂/共犯/累犯/量刑/沒收/保安處分)
  + 分則 §271–287(殺人/傷害罪章)
  刻意不抽:易刑(§41–44)、數罪併罰細節(§51)、加減例(§64–73)、
  緩刑/假釋/時效(§74–85)→ 屬計算/程序規則,日後以規則引擎處理

Pipeline:
  ① 切分:條 → 項 → 子句(「;」「。」;含「:」的列舉項不切)
  ② 前處理:條號正規化、指代消解(前項/前條/前二條…)、省略補全
  ③ 抽取規則:
     R1 者字句:「(要件)者,(主體,)?處/不罰/減免(效果)」
     R2 加重減免:「…者,(依…規定)?加重其刑/得免除其刑」
     R3 未遂句:「第X條第Y項之未遂犯罰之」
     R4 加重結果:主詞尾「(因而)致人於死/重傷」且含「犯…之罪」
     R5 訴追條件:「…之罪,須告訴乃論」;但書「不在此限」= 例外(一般化)
     R6 定義句:「稱X者,謂Y」「X者,為Y」「X者,以Y論」(總則)
     R7 效果句(無者):「X之行為,不罰/得減輕其刑」(總則)
     R8 亦同句:「X,亦同」繼承前一事實之謂詞與受詞
     R9 處罰原則句:「X之處罰,依/得按…」(§25/29/30/48)
     R10 種類/分類定義:「主刑之種類如下」「刑分為主刑及從刑」(§32/33/36)
  ④ 刑度正規化 ⑤ 輸出 json / facts_review_generated.md(底稿) / cypher
     (人工複核成果在 facts_review.md,本腳本不觸碰)

謂詞白名單:法定刑 / 刑之加重 / 刑之減免 / 未遂處罰 / 加重結果 / 訴追條件 / 定義
            / 不罰 / 科刑限制 / 保安處分 / 沒收 / 處罰依據(+「X-例外」表但書排除)

用法:python extract_facts.py ../data/C0000001.json
"""
import re
import sys
import json
from collections import Counter

RANGES = [(1, 99), (271, 287)]

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

# ------------------------------ ② 前處理 ------------------------------


def normalize_refs(text, art):
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
    lines = [l.strip() for l in text.replace('\r', '').split('\n') if l.strip()]
    paras = []
    for ln in lines:
        if re.match(r'^[一二三四五六七八九十]+、', ln) and paras:
            paras[-1] += ln
        else:
            paras.append(ln)
    return paras

# ---------------------------- ④ 刑度正規化 ----------------------------


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
    # 罰金:支援「五十萬元」「三千元」「一萬五千元」三種格式
    m = re.search(rf'(?:([{_CN}\d]+)萬)?([{_CN}\d]+)?元以下罰金', eff)
    if m and (m.group(1) or m.group(2)):
        wan = cn2int(m.group(1)) if m.group(1) else 0
        rest = cn2int(m.group(2)) if m.group(2) else 0
        if wan is not None and rest is not None:
            p['fine_max'] = wan * 10000 + rest
    if '併科' in eff:
        p['fine_mode'] = '得併科' if '得併科' in eff else '併科'
    return p

# ----------------------------- ③ 抽取規則 -----------------------------

_EFF_HEAD = (r'(?:處|科|依|不罰|不得|加重其刑|加重本刑|得加重|減輕其刑|得減輕|'
             r'減輕或免除其刑|免除其刑|得免除|得免|免其|得酌量|仍得|'
             r'得令入|令入|於刑之執行前|得於|應於|從一重|併合處罰|沒收之|得沒收)')
_RE_ZHE = re.compile(
    rf'^(?P<act>.+?)者，?(?:(?P<who>[^，]{{1,22}})，)?(?P<eff>{_EFF_HEAD}.*)$')
_RE_ATT = re.compile(r'^(?P<refs>(?:第[\d\-]+條(?:第\d+項)?[、]?)+)之未遂犯罰之$')
_RE_RESULT = re.compile(r'^(?P<base>.+?)，?(?:因而)?(?P<res>致人於死|致重傷|致死)$')
_RE_REF = re.compile(r'第[\d\-]+條(?:第\d+項)?')
# R6 定義句
_RE_DEF1 = re.compile(r'^稱(?P<term>.+?)者，(?:謂)?(?P<def>.+)$')
_RE_DEF2 = re.compile(r'^(?P<def>.+?)者，(?:皆)?為(?P<term>.{2,12})$')
_RE_DEF3 = re.compile(r'^(?P<def>.+?)者，(?:仍)?以(?P<term>.{2,12})論$')
# R7 效果句(無「者」)
_RE_EFFONLY = re.compile(
    r'^(?P<act>.+?)，(?P<eff>不罰|得減輕其刑|得減輕或免除其刑|'
    r'減輕或免除其刑|免除其刑|得免除其刑)$')
# R8 亦同
_RE_SAME = re.compile(r'^(?P<act>.+?)(?:者)?，亦同$')
# R9 處罰原則:「X之處罰,(以有特別規定者為限,並)?依/得按…」(§25/29/30/48)
_RE_PUNISH = re.compile(
    r'^(?P<subj>[^，]{2,10})之處罰，(?:以有特別規定者為限，並)?(?P<eff>(?:依|得按).+)$')
# R10 種類/分類定義(§32/33/36)
_RE_KIND = re.compile(r'^(?P<term>.+?)之種類如下：(?P<def>.+)$')
_RE_DEF4 = re.compile(r'^(?P<term>.{1,4})分為(?P<def>.+)$')
_RE_DEF5 = re.compile(r'^(?P<term>.{1,4})為(?P<def>[^，]{2,10})$')
# 但書孤懸效果句(§31「但得減輕其刑」)
_BARE_EFF = {'得減輕其刑', '減輕其刑', '得減輕或免除其刑',
             '減輕或免除其刑', '得免除其刑', '免除其刑', '得酌量減輕其刑'}
_PENALTY_WORDS = re.compile(r'死刑|徒刑|拘役|罰金|^[一二三四五六七八九十]+(日|月|年)$')


def classify_eff(eff):
    if eff.startswith('不罰'):
        return '不罰'
    if eff.startswith('不得'):
        return '科刑限制'
    if ('令入' in eff or '施以' in eff or '驅逐出境' in eff
            or '保護管束' in eff):
        return '保安處分'
    if '沒收' in eff:
        return '沒收'
    if eff.startswith(('處', '科')):
        return '法定刑'
    if '加重' in eff:
        return '刑之加重'
    if eff.startswith(('依', '從一重', '併合處罰')):
        return '處罰依據'
    return '刑之減免'


def extract_article(art, text, prev_arts, facts):
    last_fact = [None]                       # 條內錨點:亦同/但書 用

    def emit(pno, sent, s, p, o):
        f = add(facts, art, pno, sent, s, p, o)
        if f and p != 'UNMATCHED':
            last_fact[0] = f
        return f

    for pno, ptext in enumerate(split_paras(text), 1):
        ptext = normalize_refs(ptext, art).rstrip('。')
        ptext = resolve_relative(ptext, art, pno, prev_arts)
        last_base = None
        # 含「:」= 定義/列舉塊,整項處理不切句
        if '：' in ptext:
            m = _RE_DEF1.match(ptext)
            if m:
                emit(pno, ptext, m.group('term'), '定義', m.group('def'))
                continue
            m = _RE_KIND.match(ptext)
            if m:
                emit(pno, ptext, m.group('term'), '定義', m.group('def'))
                continue
            m = _RE_ZHE.match(ptext)     # §91-1 §50:者字句帶款列舉
            if m and '。' in m.group('act'):
                m = None                 # 跨句誤黏(§35),放棄
            if m:
                subj = m.group('act') + \
                    (f"，{m.group('who')}" if m.group('who') else '')
                emit(pno, ptext, subj, classify_eff(m.group('eff')),
                     m.group('eff'))
            else:
                emit(pno, ptext, ptext, 'UNMATCHED', '')
            continue
        for clause in ptext.replace('。', '；').split('；'):
            clause = clause.strip().strip('，')
            if not clause:
                continue
            # R5b 但書「不在此限」= 前一事實之例外(一般化)
            if clause.startswith('但') and '不在此限' in clause:
                subj = clause[1:].replace('，不在此限', '').rstrip('者')
                if last_fact[0] and last_fact[0]['predicate'] == '訴追條件':
                    emit(pno, clause, subj, '訴追條件', '非告訴乃論(但書)')
                elif last_fact[0]:
                    emit(pno, clause, subj,
                         last_fact[0]['predicate'] + '-例外', '不在此限')
                else:
                    emit(pno, clause, clause, 'UNMATCHED', '')
                continue
            if clause.startswith('但'):
                clause = clause[1:]
            if clause in _BARE_EFF:          # 「但得減輕其刑」承前主詞
                if last_fact[0]:
                    emit(pno, clause, last_fact[0]['subject'],
                         '刑之減免', clause)
                else:
                    emit(pno, clause, clause, 'UNMATCHED', '')
                continue
            if re.search(r'為之$', clause):   # 「得於執行前為之」等時點細則,略
                emit(pno, clause, clause, 'UNMATCHED', '')
                continue
            if clause.endswith('依其規定'):   # 「有特別規定者,依其規定」略
                emit(pno, clause, clause, 'UNMATCHED', '')
                continue
            if re.match(r'^(因而)?致', clause) and last_base:
                clause = last_base + '，' + clause
            sent = clause
            # R6a 稱…者(無冒號的單句定義)
            m = _RE_DEF1.match(clause)
            if m:
                emit(pno, sent, m.group('term'), '定義', m.group('def'))
                continue
            # R3 未遂句
            m = _RE_ATT.match(clause.replace('，', ''))
            if m:
                for ref in _RE_REF.findall(m.group('refs')):
                    emit(pno, sent, f'{ref}之罪', '未遂處罰', '成立')
                continue
            # R5 訴追條件
            if '告訴乃論' in clause:
                for ref in _RE_REF.findall(clause):
                    emit(pno, sent, f'{ref}之罪', '訴追條件', '告訴乃論')
                continue
            # R9 處罰原則(§25/29/30/48)
            m = _RE_PUNISH.match(clause)
            if m:
                emit(pno, sent, m.group('subj'),
                     classify_eff(m.group('eff')), m.group('eff'))
                continue
            # R8 亦同:繼承前一事實
            m = _RE_SAME.match(clause)
            if m and last_fact[0]:
                emit(pno, sent, m.group('act'),
                     last_fact[0]['predicate'], last_fact[0]['object'])
                continue
            # R1/R2 者字句
            m = _RE_ZHE.match(clause)
            if m:
                act, who, eff = m.group('act'), m.group('who'), m.group('eff')
                subj = act + (f'，{who}' if who else '')
                pred = classify_eff(eff)
                obj = '成立' if pred == '不罰' else eff
                f = emit(pno, sent, subj, pred, obj)
                if pred == '法定刑' and f:
                    f['penalty'] = parse_penalty(eff)
                bm = _RE_RESULT.match(act)
                if bm and bm.group('base'):
                    last_base = bm.group('base')
                    if re.search(r'犯.+之罪', bm.group('base')):
                        emit(pno, sent, bm.group('base'), '加重結果',
                             bm.group('res'))
                else:
                    last_base = act
                continue
            # R6b/R6c 定義句(為X / 以X論);刑名 guard 防「死刑減輕者,為無期徒刑」
            m = _RE_DEF3.match(clause) or _RE_DEF2.match(clause)
            if m and not _PENALTY_WORDS.search(m.group('term')):
                emit(pno, sent, m.group('term'), '定義', m.group('def'))
                continue
            # R7 效果句(無「者」)
            m = _RE_EFFONLY.match(clause)
            if m:
                pred = classify_eff(m.group('eff'))
                obj = '成立' if pred == '不罰' else m.group('eff')
                emit(pno, sent, m.group('act'), pred, obj)
                continue
            # R10 分類定義(§32 刑分為主刑及從刑 / §36 從刑為褫奪公權)
            m = _RE_DEF4.match(clause) or _RE_DEF5.match(clause)
            if m and not _PENALTY_WORDS.search(m.group('term')):
                emit(pno, sent, m.group('term'), '定義', m.group('def'))
                continue
            emit(pno, sent, clause, 'UNMATCHED', '')


_seen = set()
_art_seq = {}


def add(facts, art, pno, sent, s, p, o):
    s, o = s.strip('，, '), o.strip('，, ')
    key = (s, p, o, art)
    if key in _seen:
        return {}
    _seen.add(key)
    _art_seq[art] = _art_seq.get(art, 0) + 1
    f = {'fid': f'F-{art}-{pno}-{_art_seq[art]}',
         'subject': s, 'predicate': p, 'object': o,
         'article': art, 'para': pno, 'sentence': sent}
    facts.append(f)
    return f

# ------------------------------- ⑤ 輸出 -------------------------------


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
                 ('fid', 'subject', 'predicate', 'object', 'article',
                  'para', 'sentence')}
        if 'penalty' in f:
            pen = f['penalty']
            props['penalty_raw'] = pen['raw']
            for k in ('min', 'max', 'fine_max'):
                if k in pen:
                    props[f'penalty_{k}'] = pen[k]
            if 'types' in pen:
                props['penalty_types'] = '、'.join(pen['types'])
            if 'fine_mode' in pen:
                props['penalty_fine_mode'] = pen['fine_mode']
            for k in ('min', 'max'):
                if k in pen:
                    n = int(pen[k][:-1])
                    props[f'penalty_{k}_months'] = \
                        n * 12 if pen[k].endswith('年') else n
        rows.append(props)
    seg = ['// === 檔C:第四層 Fact 節點 ===',
           '// 步驟1(單獨執行):約束',
           'CREATE CONSTRAINT fact_fid IF NOT EXISTS FOR (n:Fact) REQUIRE n.fid IS UNIQUE;',
           '// 步驟2(單獨執行):清空舊 Fact 整批重建,避免 fid 漂移殘留',
           'MATCH (f:Fact) DETACH DELETE f;',
           '// 步驟3:以下整段一次執行']
    arr = ', '.join('{' + ', '.join(f'{k}: {esc(v)}' for k, v in r.items()) + '}'
                    for r in rows)
    seg.append(f'UNWIND [{arr}] AS f MERGE (n:Fact {{fid: f.fid}}) SET n += f')
    seg.append('WITH count(*) AS _')
    # 以 code(刑法-271)配對而非 number,避免日後多法典時條號互撞
    pairs = ', '.join(f"['{r['fid']}','刑法-{r['article']}']" for r in rows)
    seg.append(f'UNWIND [{pairs}] AS p MATCH (f:Fact {{fid:p[0]}}), '
               '(a:Article {code:p[1]}) MERGE (f)-[:EXTRACTED_FROM]->(a)')
    seg.append('WITH count(*) AS _ MATCH (f:Fact) RETURN count(f) AS Fact數;')
    return '\n'.join(seg) + '\n'


def in_ranges(n):
    return any(lo <= n <= hi for lo, hi in RANGES)


def main():
    # Windows 主控台預設 cp950,印「⚠」等字元會 UnicodeEncodeError
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
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
        art = m.group(1) + (f'-{m.group(2)}' if m.group(2) else '')
        content = (it.get('條文內容') or '').replace('\r', '').strip()
        if in_ranges(int(m.group(1))) and content != '（刪除）':
            extract_article(art, content, prev_arts, facts)
        prev_arts.append(art)

    ok = [f for f in facts if f['predicate'] != 'UNMATCHED']
    bad = [f for f in facts if f['predicate'] == 'UNMATCHED']

    with open('facts_pilot.json', 'w', encoding='utf-8') as fp:
        json.dump(facts, fp, ensure_ascii=False, indent=1)
    with open('C_facts_oneshot.cypher', 'w', encoding='utf-8') as fp:
        fp.write(to_cypher(facts))
    # 產生「空白」複核表底稿;人工複核成果維護於 facts_review.md(gold standard),
    # 本腳本絕不覆寫該檔,避免重跑時洗掉查核紀錄
    with open('facts_review_generated.md', 'w', encoding='utf-8') as fp:
        fp.write('# Fact 抽取複核表底稿(總則 §1–99 選抽 + 分則 §271–287)\n\n'
                 '> 本檔為 extract_facts.py 自動產生,重跑會覆蓋。\n'
                 '> 人工複核請維護 facts_review.md(gold standard),勿直接改本檔。\n\n'
                 '| fid | 主詞 (S) | 謂詞 (P) | 受詞 (O) | 出處 | 正確? |\n'
                 '|---|---|---|---|---|---|\n')
        for f in facts:
            fp.write(f"| {f['fid']} | {f['subject'][:40]} | {f['predicate']} | "
                     f"{f['object'][:40]} | §{f['article']}({f['para']}) |  |\n")

    print(f'抽取完成:{len(ok)} 個三元組,{len(bad)} 句未匹配 (UNMATCHED)')
    print('謂詞分布:', dict(Counter(f['predicate'] for f in facts)))
    print()
    for f in facts:
        mark = ' ⚠' if f['predicate'] == 'UNMATCHED' else ''
        print(f"§{f['article']}({f['para']}) ({f['subject'][:45]} | "
              f"{f['predicate']} | {f['object'][:40]}){mark}")


if __name__ == '__main__':
    main()
