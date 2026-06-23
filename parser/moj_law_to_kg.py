#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
moj_law_to_kg.py
將「全國法規資料庫」結構化 JSON(例:中華民國刑法 pcode=C0000001)
轉成 6 層法典知識圖譜的 Cypher MERGE 腳本。

層級:編 Part -> 章 Chapter -> 節 Section -> 條 Article -> 項 Paragraph -> 款 Subparagraph
骨架:(parent)-[:CONTAINS]->(child)
橫向:CITES / AGGRAVATES / MITIGATES / PUNISHES_ATTEMPT / PUNISHES_PREPARATION

輸入 JSON 預期格式(MOJ open data):
{
  "法規名稱": "中華民國刑法",
  "法規內容": [
     {"編章節": "第 一 編　總則"},
     {"編章節": "第 一 章　法例"},
     {"條號": "第 1 條", "條文內容": "..."},
     ...
  ]
}

用法:
  python moj_law_to_kg.py C0000001.json -o criminal_code_full.cypher
"""
import re
import sys
import json
import argparse

# ----------------------------------------------------------------------
# 中文數字 -> int  (支援 零〇兩 與 十百千,涵蓋到法典常見的三位數條號)
# ----------------------------------------------------------------------
_CN_DIGIT = {'零': 0, '〇': 0, '一': 1, '二': 2, '兩': 2, '三': 3, '四': 4,
             '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
_CN_UNIT = {'十': 10, '百': 100, '千': 1000}


def cn2int(s: str):
    """'二百七十一' -> 271 ; '二十二' -> 22 ; '271' -> 271 ; 失敗回 None"""
    s = s.strip()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    total, section, number = 0, 0, 0
    for ch in s:
        if ch in _CN_DIGIT:
            number = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            if number == 0:
                number = 1          # 處理「十一」開頭的十
            section += number * unit
            number = 0
        else:
            return None             # 非預期字元
    return total + section + number


# ----------------------------------------------------------------------
# 解析輔助
# ----------------------------------------------------------------------
_RE_PART = re.compile(r'第\s*([\u4e00-\u9fff\d]+)\s*編')
_RE_CHAP = re.compile(r'第\s*([\u4e00-\u9fff\d]+)\s*章(?:\s*之\s*([\u4e00-\u9fff\d]+))?')
_RE_SECT = re.compile(r'第\s*([\u4e00-\u9fff\d]+)\s*節')
# 條號:第 271 條 / 第 271-1 條 / 第 271 之 1 條
_RE_ARTNO = re.compile(r'第\s*(\d+)(?:\s*[-之]\s*(\d+))?\s*條')

# 內文引用(中文數字):第二百七十一條(之一)?(第一項)?
_RE_REF_ART = re.compile(
    r'第([零〇一二三四五六七八九十百千兩\d]+)條(?:之([零一二三四五六七八九十\d]+))?'
    r'(?:第([零一二三四五六七八九十\d]+)項)?'
)


def clean_title(raw: str, num_token: str) -> str:
    """從『第 二十二 章　殺人罪』取出『殺人罪』"""
    t = raw
    t = re.split(r'[編章節]', t, maxsplit=1)
    t = t[1] if len(t) > 1 else ''
    return t.replace('\u3000', ' ').strip()


def art_code(num, sub):
    return f'刑法-{num}' + (f'-{sub}' if sub else '')


def split_paragraphs(text: str):
    """條文內容 -> [(項序, 項文字, [(款序,款文字)...]), ...]
       規則:換行分段;以中文序數『一、二、…』開頭者視為款,掛在前一項底下。"""
    lines = [ln.strip() for ln in text.replace('\r', '').split('\n') if ln.strip()]
    paras, cur_no = [], 0
    sub_re = re.compile(r'^([一二三四五六七八九十百]+)、(.*)$')
    for ln in lines:
        m = sub_re.match(ln)
        if m and paras:                         # 款:附到目前的項
            paras[-1][2].append((cn2int(m.group(1)), m.group(2).strip()))
        else:                                    # 新的項
            cur_no += 1
            paras.append((cur_no, ln, []))
    return paras


def classify(text: str) -> str:
    if '加重' in text:
        return 'AGGRAVATES'
    if '減輕' in text or '減其刑' in text:
        return 'MITIGATES'
    return 'CITES'


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def parse(law_json: dict):
    nodes = []          # (label, code, props)
    contains = []       # (parent_code, child_code)
    crossref = []       # (src_code, rel, dst_code, props)

    law_name = law_json.get('法規名稱', '未知法規')
    items = law_json.get('法規內容', [])

    cur_part = cur_chap = cur_sect = None
    last_article = None                       # (code, number_int) 供「前條」用
    order_part = order_chap = order_sect = 0

    for it in items:
        # ---- 編章節標題行 ----
        if '編章節' in it and it['編章節']:
            head = it['編章節']
            if _RE_PART.search(head):
                num = cn2int(_RE_PART.search(head).group(1))
                order_part += 1
                cur_part = f'刑法-編{num}'
                nodes.append(('Part', cur_part,
                              {'title': clean_title(head, ''), 'number': str(num),
                               'order': num, 'level': '編', 'law': law_name}))
                cur_chap = cur_sect = None
            elif _RE_CHAP.search(head):
                cm = _RE_CHAP.search(head)
                num = cn2int(cm.group(1))
                csub = cn2int(cm.group(2)) if cm.group(2) else None
                cur_chap = f'{cur_part or "刑法"}-章{num}' + (f'-{csub}' if csub else '')
                nodes.append(('Chapter', cur_chap,
                              {'title': clean_title(head, ''), 'number': str(num),
                               'order': num, 'level': '章', 'law': law_name}))
                if cur_part:
                    contains.append((cur_part, cur_chap))
                cur_sect = None
            elif _RE_SECT.search(head):
                num = cn2int(_RE_SECT.search(head).group(1))
                cur_sect = (cur_chap or '刑法') + f'-節{num}'
                nodes.append(('Section', cur_sect,
                              {'title': clean_title(head, ''), 'number': str(num),
                               'order': num, 'level': '節', 'law': law_name}))
                if cur_chap:
                    contains.append((cur_chap, cur_sect))
            continue

        # ---- 條文 ----
        if '條號' in it and it['條號']:
            m = _RE_ARTNO.search(it['條號'])
            if not m:
                continue
            num, sub = m.group(1), m.group(2)
            code = art_code(num, sub)
            num_int = int(num) + (int(sub) / 10 if sub else 0)
            content = it.get('條文內容', '') or ''

            paras = split_paragraphs(content)
            # 無項(單句)→ 文字掛在條(葉);有多項 → 條只掛標題
            article_text = paras[0][1] if len(paras) == 1 else ''
            nodes.append(('Article', code,
                          {'title': f'第{num}' + (f'-{sub}' if sub else '') + '條',
                           'number': num + (f'-{sub}' if sub else ''),
                           'order': num_int, 'level': '條', 'law': law_name,
                           'text': article_text}))
            # 掛在最近的 節 > 章 (skip-level)
            parent = cur_sect or cur_chap
            if parent:
                contains.append((parent, code))

            # 項 / 款
            para_codes = []
            if len(paras) > 1:
                for pno, ptext, subs in paras:
                    pcode = f'{code}-項{pno}'
                    para_codes.append((pno, pcode, ptext))
                    nodes.append(('Paragraph', pcode,
                                  {'number': str(pno), 'order': pno, 'level': '項',
                                   'text': ptext}))
                    contains.append((code, pcode))
                    for sno, stext in subs:
                        scode = f'{pcode}-款{sno}'
                        nodes.append(('Subparagraph', scode,
                                      {'number': str(sno), 'order': sno,
                                       'level': '款', 'text': stext}))
                        contains.append((pcode, scode))

            # ---- 交叉引用抽取 ----
            # 逐項處理,讓「前項 / 預備犯第X項之罪」能定位
            scan_units = para_codes if para_codes else [(1, code, content)]
            for pno, src_code, ptext in scan_units:
                # 相對引用
                if '前條' in ptext and last_article:
                    rel = classify(ptext)
                    crossref.append((code, rel, last_article[0],
                                     {'note': '前條', 'condition': _condition(ptext)}))
                # 未遂 / 預備:指向同條被引用的項(預設前一項或文中指明的項)
                if '未遂犯' in ptext:
                    tgt = _same_article_para(code, ptext, pno, kind='attempt')
                    if tgt:
                        crossref.append((src_code, 'PUNISHES_ATTEMPT', tgt, {}))
                if '預備犯' in ptext:
                    tgt = _same_article_para(code, ptext, pno, kind='prep')
                    if tgt:
                        crossref.append((src_code, 'PUNISHES_PREPARATION', tgt, {}))
                # 絕對引用:第○○○條(第○項)?
                for rm in _RE_REF_ART.finditer(ptext):
                    art_n = cn2int(rm.group(1))
                    if art_n is None:
                        continue
                    sub_n = cn2int(rm.group(2)) if rm.group(2) else None
                    dst = art_code(str(art_n), str(sub_n) if sub_n else None)
                    if dst == code:
                        continue
                    rel = classify(ptext)
                    crossref.append((code, rel, dst,
                                     {'condition': _condition(ptext)}))

            last_article = (code, num_int)

    return law_name, nodes, contains, crossref


def _condition(text: str):
    """粗略擷取構成要件關鍵語(供人工複核,非權威)"""
    for kw in ['直系血親尊親屬', '未滿七歲', '凌虐', '當場激於義憤']:
        if kw in text:
            return kw
    return None


def _same_article_para(code, ptext, cur_pno, kind):
    """未遂/預備 在同條內指向的項。預設:前項;若文中『第○項』則用之。"""
    m = re.search(r'第([一二三四五六七八九十\d]+)項', ptext)
    if m:
        n = cn2int(m.group(1))
        return f'{code}-項{n}'
    if cur_pno and cur_pno > 1:        # 「前項之未遂犯」
        return f'{code}-項{cur_pno - 1}'
    return None


# ----------------------------------------------------------------------
# Cypher 輸出
# ----------------------------------------------------------------------
def esc(v):
    if v is None:
        return 'null'
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace('\\', '\\\\').replace("'", "\\'") + "'"


def to_cypher(law_name, nodes, contains, crossref):
    out = [f'// 自動產生:{law_name} 知識圖譜', '']
    out.append('// --- 約束 ---')
    for lbl in ['Part', 'Chapter', 'Section', 'Article', 'Paragraph', 'Subparagraph']:
        out.append(f'CREATE CONSTRAINT {lbl.lower()}_code IF NOT EXISTS '
                   f'FOR (n:{lbl}) REQUIRE n.code IS UNIQUE;')
    out.append('')
    out.append('// --- 節點 ---')
    for lbl, code, props in nodes:
        kv = ', '.join(f'{k}: {esc(v)}' for k, v in props.items() if v not in (None, ''))
        kv = (', ' + kv) if kv else ''
        out.append(f'MERGE (n:{lbl} {{code: {esc(code)}}}) SET n += {{{kv[2:] if kv else ""}}};')
    out.append('')
    out.append('// --- CONTAINS 骨架 ---')
    for p, c in contains:
        out.append(f'MATCH (a {{code: {esc(p)}}}),(b {{code: {esc(c)}}}) MERGE (a)-[:CONTAINS]->(b);')
    out.append('')
    out.append('// --- 橫向引用 ---')
    for s, rel, d, props in crossref:
        kv = ', '.join(f'{k}: {esc(v)}' for k, v in props.items() if v not in (None, ''))
        kv = f' {{{kv}}}' if kv else ''
        out.append(f'MATCH (a {{code: {esc(s)}}}),(b {{code: {esc(d)}}}) '
                   f'MERGE (a)-[:{rel}{kv}]->(b);')
    return '\n'.join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('json_path')
    ap.add_argument('-o', '--out', default='law_kg.cypher')
    args = ap.parse_args()

    with open(args.json_path, encoding='utf-8') as f:
        data = json.load(f)
    # MOJ 下載檔有時外層為 list 或包一層,取第一個 dict
    if isinstance(data, list):
        data = data[0]

    law_name, nodes, contains, crossref = parse(data)
    cypher = to_cypher(law_name, nodes, contains, crossref)
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(cypher)

    stats = {}
    for lbl, _, _ in nodes:
        stats[lbl] = stats.get(lbl, 0) + 1
    print(f'[{law_name}] 解析完成')
    print('  節點:', stats, '共', len(nodes))
    print('  CONTAINS:', len(contains), '  橫向引用:', len(crossref))
    print('  輸出 ->', args.out)


if __name__ == '__main__':
    main()
