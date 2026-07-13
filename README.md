# 中華民國刑法知識圖譜 (Criminal Code Knowledge Graph)

把《中華民國刑法》全文轉成一張結構化、可查詢、可推理的**知識圖譜 (Knowledge Graph)**,作為後續 RAG(檢索增強生成)系統的知識骨架。

> **目前狀態:已改版為三層結構。** 478 個節點、625 條關係;項/款文字合併進「條」節點,作為後續 OpenIE 語意三元組抽取的基礎。

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
2. `cypher/A_nodes_oneshot.cypher` — 建立 478 個節點
3. `cypher/B_rels_oneshot.cypher` — 建立全部關係
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

三層階層,以「條」為最小結構單位:

```
編 Part → 章 Chapter → 條 Article
```

- 「節」標題不建節點(條直接掛章);「項/款」不建節點,**整條原文(含項款、以換行分隔)存於 `Article.text`**。
- 骨架關係:`(上層)-[:CONTAINS]->(下層)`。
- 橫向關係(皆為 條→條):`AGGRAVATES`(加重)、`MITIGATES`(減輕)、`CITES`(引用)、`LISTS`(中性列舉,由 cleanup 降級產生)。
- 同條內的「未遂犯罰之/預備犯…」改記為 Article 布林屬性:`punishes_attempt`、`punishes_preparation`。
- 已刪除條文(內容為「（刪除）」,共 20 條)標記 `is_deleted: true`,檢索與抽取時應排除。
- 條數說明:刑法本文條號至第 363 條,另有 59 條增訂條文(第X條之Y),故 Article 共 422 個。
- 設計理由:結構層只負責定位與脈絡;**語意內容交給第四層(規劃中)的 OpenIE 三元組**,三元組以 `EXTRACTED_FROM` 回連來源「條」。

詳細的節點屬性、`code` 命名規則、關係定義與設計理由,見 [`docs/criminal_code_kg_spec.md`](docs/criminal_code_kg_spec.md)。

詳細的節點屬性、`code` 命名規則、關係定義與設計理由,見 [`docs/criminal_code_kg_spec.md`](docs/criminal_code_kg_spec.md)。

---

## 圖譜統計 (Statistics)

**節點 478**

| Label | 數量 |
|---|---|
| Article(條) | 422 |
| Chapter(章) | 54 |
| Part(編) | 2 |

**關係 625(去重後)**

| 關係 | 中文 | 數量 |
|---|---|---|
| CONTAINS | 包含(階層) | 476 |
| CITES | 引用 | 121 |
| AGGRAVATES | 加重 | 15 |
| MITIGATES | 減輕 | 13 |

**Article 屬性(取代原本的項級關係)**

| 屬性 | 中文 | 數量 |
|---|---|---|
| punishes_attempt | 未遂處罰 | 95 |
| punishes_preparation | 預備處罰 | 7 |

> CITES 中的列舉型條文(管轄/告訴乃論等)執行 `cleanup.cypher` 後會降級為 LISTS。

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

- [ ] **第四層:OpenIE 語意三元組**——從 `Article.text` 抽取 (S, P, O),以 `EXTRACTED_FROM` 回連來源條文
- [ ] 三元組向量嵌入,作為 GraphRAG 檢索入口(口語問題 → 比對三元組 → 定位條文 → 沿關係擴展)
- [ ] 查詢理解層:LLM 把口語問題正規化為法律用語後再檢索
- [ ] 接上總則計算規則(§25 未遂定義、§64–73 加減例)

---

## 已知限制 (Known Limitations)

- 橫向關係以關鍵字啟發式抽取,列舉型條文需人工複核(已於 `cleanup.cypher` 處理主要案例)。
- 「第○條第○項」引用一律連到「條」(三層模型的設計決定,項級語意將由第四層三元組承擔)。
- 少數引用指向已刪除之條文,建立關係時會因目標不存在而自動略過。

---

## 注意:不要上傳憑證

Neo4j Aura 的連線憑證檔(`Neo4j-*.txt`,含密碼)**不可**提交到版本控制,已列入 `.gitignore`。
