# 中華民國刑法知識圖譜 (Criminal Code Knowledge Graph)

把《中華民國刑法》全文轉成一張結構化、可查詢、可推理的**知識圖譜 (Knowledge Graph)**,作為後續 RAG(檢索增強生成)系統的知識骨架。

> **目前狀態:KG 已建置完成。** 1,228 個節點、1,524 條關係,已載入 Neo4j Aura 並完成資料品質清理。

---

## 專案簡介

法律條文本身就是高度結構化的階層資料,非常適合用知識圖譜表達。相較於把法條切塊丟進向量資料庫的純向量 RAG,知識圖譜能額外表達**法條之間的結構關係**(哪一條加重哪一條、哪一條引用哪一條),讓系統能沿關係做多跳推理,而非只找文字相似的段落。

本 repo 包含:從官方法規 JSON 自動建圖的 parser、可直接灌入 Neo4j 的 Cypher 腳本、資料品質清理腳本,以及完整技術文件。

---

## 資料來源 (Data Source)

| 項目 | 內容 |
|---|---|
| 法規 | 中華民國刑法,pcode `C0000001` |
| 原始來源 | 全國法規資料庫 (law.moj.gov.tw) |
| 取得管道 | [`kong0107/mojLawSplitJSON`](https://github.com/kong0107/mojLawSplitJSON)(將 MOJ 開放資料拆分為單一法規 JSON) |
| 法規異動日期 | 2026-03-13 |


---

## 目錄結構

```
criminal-code-kg/
├── README.md
├── .gitignore
├── parser/
│   ├── moj_law_to_kg.py      # 主 parser:MOJ JSON → Cypher
│   └── emit_oneshot.py       # 產生「單段」Cypher(供 Aura 一次貼上執行)
├── data/
│   └── C0000001.json         # 刑法原始 JSON
├── cypher/
│   ├── 01_constraints.cypher # 步驟1:建立唯一性約束
│   ├── A_nodes_oneshot.cypher# 步驟2:建立全部節點
│   ├── B_rels_oneshot.cypher # 步驟3:建立全部關係
│   └── cleanup.cypher        # 步驟4:資料品質清理
├── docs/
│   └── criminal_code_kg_spec.md  # 完整技術文件(資料模型、方法、設計理由)
└── examples/
    └── sample.json           # 測試用小樣本
```

---

## 快速開始 (Quick Start)

### A. 直接用現成的 Cypher 灌進 Neo4j

在 Neo4j(Aura 或本機)的查詢介面,依序執行:

1. `cypher/01_constraints.cypher` — 建立約束
2. `cypher/A_nodes_oneshot.cypher` — 建立 1,228 個節點
3. `cypher/B_rels_oneshot.cypher` — 建立 1,524 條關係
4. `cypher/cleanup.cypher` — 清理啟發式誤判(分段執行,見檔內註解)

> Aura Query 編輯器一次只執行一段 statement,A/B 兩檔已各包成單段,整段貼上即可一次灌完。

驗證:

```cypher
MATCH (n) RETURN labels(n)[0] AS 類型, count(*) AS 數量 ORDER BY 數量 DESC;
```

### B. 自己從原始資料重新產生

```bash
cd parser
python moj_law_to_kg.py ../data/C0000001.json -o ../cypher/criminal_code_full.cypher
# 或產生「單段」版本:
python emit_oneshot.py ../data/C0000001.json
```

---

## 資料模型 (Data Model)

六層階層,完全對應法典本身的結構:

```
編 Part → 章 Chapter → 節 Section → 條 Article → 項 Paragraph → 款 Subparagraph
```

- 骨架關係:`(上層)-[:CONTAINS]->(下層)`,「節」與「款」為選用層(無則 skip-level)。
- 橫向關係:`AGGRAVATES`(加重)、`MITIGATES`(減輕)、`CITES`(引用)、`PUNISHES_ATTEMPT`(未遂處罰)、`PUNISHES_PREPARATION`(預備處罰)、`LISTS`(中性列舉)。

詳細的節點屬性、`code` 命名規則、關係定義與設計理由,見 [`docs/criminal_code_kg_spec.md`](docs/criminal_code_kg_spec.md)。

---

## 圖譜統計 (Statistics)

**節點 1,228**

| Label | 數量 |
|---|---|
| Paragraph(項) | 628 |
| Article(條) | 422 |
| Subparagraph(款) | 122 |
| Chapter(章) | 54 |
| Part(編) | 2 |

**關係 1,524**

| 關係 | 中文 | 數量 |
|---|---|---|
| CONTAINS | 包含(階層) | 1,226 |
| CITES | 引用 | 107 |
| PUNISHES_ATTEMPT | 未遂處罰 | 95 |
| LISTS | 列舉(中性) | 62 |
| AGGRAVATES | 加重 | 15 |
| MITIGATES | 減輕 | 12 |
| PUNISHES_PREPARATION | 預備處罰 | 7 |

---

## 範例查詢 (Example Queries)

```cypher
// 殺人罪整章 + 加重/減輕/引用關係
MATCH (c:Chapter {title:'殺人罪'})-[:CONTAINS]->(a:Article)
OPTIONAL MATCH (a)-[r:AGGRAVATES|MITIGATES|CITES]->(a2)
RETURN c, a, r, a2;

// 第271條的完整出身(編→章→條)
MATCH path = (:Part)-[:CONTAINS*]->(:Article {number:'271'})
RETURN path;
```

---

## 後續工作 (Roadmap)

- [ ] 建立全文索引 / 向量嵌入,作為 RAG 檢索入口
- [ ] 查詢理解層:LLM 把口語問題拆解、正規化成結構化意圖(含條號驗證回 KG)
- [ ] 情境檢索查詢:沿關係擴展、組成 context
- [ ] 接上總則計算規則(§25 未遂定義、§64–73 加減例)
- [ ] 引用精度提升到「項」層級

---

## 已知限制 (Known Limitations)

- 橫向關係以關鍵字啟發式抽取,列舉型條文需人工複核(已於 `cleanup.cypher` 處理主要案例)。
- 「第○條第○項」引用目前連到「條」,尚未精確到「項」。

---

## 注意:不要上傳憑證

Neo4j Aura 的連線憑證檔(`Neo4j-*.txt`,含密碼)**不可**提交到版本控制,已列入 `.gitignore`。
