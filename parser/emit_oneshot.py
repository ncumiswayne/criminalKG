#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
emit_oneshot.py
讀取 MOJ 法規 JSON,輸出兩個「單段 statement」的 Cypher 檔:
  A_nodes_oneshot.cypher  — 全部節點(一段 UNWIND 串接)
  B_rels_oneshot.cypher   — 全部關係(一段 UNWIND 串接)

這兩個檔設計成「整段一次貼進 Neo4j Aura Query 編輯器執行」,
因為該編輯器一次只執行一段 statement;包成單段才能一次灌完。

用法:
  python emit_oneshot.py ../data/C0000001.json
"""
import os
import sys
import json
from moj_law_to_kg import parse, esc

# 輸出至 repo 的 cypher/ 目錄(以腳本位置定位,與執行時的工作目錄無關)
_OUT_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'cypher'))


def cmap(props):
    inner = ', '.join(f'{k}: {esc(v)}' for k, v in props.items() if v not in (None, ''))
    return '{' + inner + '}'


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else '../data/C0000001.json'
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, list):
        data = data[0]
    _, nodes, contains, crossref = parse(data)

    # ---------- A. 節點 ----------
    by_label = {}
    for lbl, code, props in nodes:
        row = {'code': code}
        row.update({k: v for k, v in props.items() if v not in (None, '')})
        by_label.setdefault(lbl, []).append(row)

    seg = ['// === 檔A:建立所有節點(整段一次執行)===']
    first = True
    for lbl, rows in by_label.items():
        if not first:
            seg.append('WITH count(*) AS _')
        first = False
        arr = ', '.join(cmap(r) for r in rows)
        seg.append(f'UNWIND [{arr}] AS r MERGE (n:{lbl} {{code: r.code}}) SET n += r')
    seg.append('WITH count(*) AS _ MATCH (n) RETURN count(n) AS 節點總數;')
    path_a = os.path.join(_OUT_DIR, 'A_nodes_oneshot.cypher')
    with open(path_a, 'w', encoding='utf-8') as f:
        f.write('\n'.join(seg) + '\n')

    # ---------- B. 關係 ----------
    seg = ['// === 檔B:建立所有關係(整段一次執行;先跑完檔A)===']
    pairs = ', '.join(f"['{p}','{c}']" for p, c in contains)
    seg.append(f'UNWIND [{pairs}] AS p '
               'MATCH (a {code:p[0]}),(b {code:p[1]}) MERGE (a)-[:CONTAINS]->(b)')
    by_rel = {}
    for s, rel, d2, props in crossref:
        by_rel.setdefault(rel, []).append(
            (s, d2, {k: v for k, v in props.items() if v not in (None, '')}))
    for rel, rows in by_rel.items():
        seg.append('WITH count(*) AS _')
        arr = ', '.join('{s:' + esc(s) + ', d:' + esc(d2) + ', props:' + cmap(pr) + '}'
                        for s, d2, pr in rows)
        seg.append(f'UNWIND [{arr}] AS row '
                   'MATCH (a {code:row.s}),(b {code:row.d}) '
                   f'MERGE (a)-[x:{rel}]->(b) SET x += row.props')
    seg.append('WITH count(*) AS _ MATCH ()-[r]->() RETURN count(r) AS 關係總數;')
    path_b = os.path.join(_OUT_DIR, 'B_rels_oneshot.cypher')
    with open(path_b, 'w', encoding='utf-8') as f:
        f.write('\n'.join(seg) + '\n')

    print(f'已輸出 {path_a} / {path_b}')


if __name__ == '__main__':
    main()
