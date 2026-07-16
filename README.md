# 中華民國刑法知識圖譜 (Criminal Code Knowledge Graph)

把《中華民國刑法》全文轉成一張結構化、可查詢、可推理的**知識圖譜 (Knowledge Graph)**,作為後續 RAG(檢索增強生成)系統的知識骨架。

> **目前狀態:三層結構 + 第四層語意事實上線。** 結構層 478 節點、674 關係;語意層 143 個 Fact 三元組(總則 §1–99 選抽 81 + 殺人/傷害罪章 62),以法條句式規則(中文 OpenIE)抽取,查核結果 ✓134 / △8(語意備註)/ ✗1(訓練集成績,held-out 評估待做)。

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
│   └── cleanup.cypher        # 診斷查詢(修正已內建 parser,無須再跑)
├── extraction/               # 第四層:語意事實抽取(中文 OpenIE)
│   ├── extract_facts.py      # 句式規則抽取器(總則 §1–99 選抽 + §271–287)
│   ├── facts_pilot.json      # 抽取結果(338 筆:143 Fact + 195 UNMATCHED,含出處)
│   ├── facts_review.md       # 人工複核表(gold standard,手工維護;腳本不會覆寫)
│   └── C_facts_oneshot.cypher# 步驟5:Fact 節點 + EXTRACTED_FROM
├── docs/
│   └── criminal_code_kg_spec.md  # 技術文件(v1 設計紀錄,現行架構見本 README)
└── examples/
    └── sample.json           # 測試用小樣本
```

---

## 快速開始 (Quick Start)

### A. 直接用現成的 Cypher 灌進 Neo4j

在 Neo4j(Aura 或本機)的查詢介面,依序執行:

1. `cypher/01_constraints.cypher` — 建立約束(逐行執行)
2. `cypher/A_nodes_oneshot.cypher` — 建立 478 個節點
3. `cypher/B_rels_oneshot.cypher` — 建立 674 條關係
4. `extraction/C_facts_oneshot.cypher` — 建立第四層 143 個 Fact(檔內三步驟:約束 → 清空舊 Fact → 整段重建;fid 為條內流水號,重貼冪等)

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
- 設計理由:結構層只負責定位與脈絡;**語意內容交給第四層的 Fact 三元組**。

**第四層:語意事實 (Fact)** — pilot 已上線(殺人罪章 §271–276 + 傷害罪章 §277–287):

- `(:Fact {subject, predicate, object, sentence})-[:EXTRACTED_FROM]->(:Article)`
- 抽取方法:法條句式規則(中文 OpenIE)——者字句、未遂句、加重結果、告訴乃論、但書;
  前處理含條號正規化、指代消解(前項/前條/前二條)、省略補全(「致重傷者」補回基礎行為)
- 謂詞白名單:`法定刑` / `刑之加重` / `刑之減免` / `未遂處罰` / `加重結果` / `訴追條件`
- 刑度正規化:`penalty_types`(刑種)、`penalty_min/max`(有期徒刑區間)、
  `penalty_min/max_months`(月數,供數值比較)、`penalty_fine_max`(罰金上限)、`penalty_fine_mode`(併科)
  > 語意約定:min/max 只描述**有期徒刑**區間;死刑/無期徒刑/拘役/罰金為類別,記於 types;罰金金額在 fine_max。

詳細的節點屬性、`code` 命名規則、關係定義與設計理由,見 [`docs/criminal_code_kg_spec.md`](docs/criminal_code_kg_spec.md)。

---

## 圖譜統計 (Statistics)

**節點 478**

| Label | 數量 |
|---|---|
| Article(條) | 422 |
| Chapter(章) | 54 |
| Part(編) | 2 |

**關係 674(去重後)**

| 關係 | 中文 | 數量 |
|---|---|---|
| CONTAINS | 包含(階層) | 476 |
| CITES | 引用 | 92 |
| LISTS | 列舉(中性) | 69 |
| AGGRAVATES | 加重 | 25 |
| MITIGATES | 減輕 | 12 |

**Article 屬性(取代原本的項級關係)**

| 屬性 | 中文 | 數量 |
|---|---|---|
| punishes_attempt | 未遂處罰 | 95 |
| punishes_preparation | 預備處罰 | 7 |

**第四層 Fact(總則 §1–99 選抽 + 分則 §271–287)共 143**

| 謂詞 | 數量 | 說明 |
|---|---|---|
| 法定刑 | 35 | 分則 + §31 II |
| 定義 | 23 | 重傷/故意/過失/正犯/教唆犯/幫助犯/累犯/主刑…(§10/13/14/25/28–31/32/33/47) |
| 刑之減免 | 19 | §25 未遂得減、§59 酌減、§62 自首、正當防衛過當… |
| 加重結果 | 12 | 分則 |
| 處罰依據 | 10 | §29 教唆犯、§55 想像競合從一重、§50 併合處罰… |
| 不罰 | 9 | 責任能力(§18/19)、正當防衛(§23)、緊急避難(§24)… |
| 保安處分 | 9 | 感化教育/監護/禁戒/強制治療/驅逐出境(§86–95) |
| 未遂處罰 | 7 | 分則 |
| 科刑限制 | 5 | §63 少年老人不得死刑、沒收/保安處分執行時效 |
| 刑之加重 | 5 | 分則 + §47 累犯 |
| 訴追條件 | 4 | 含 §287 但書例外 |
| 沒收 | 3 | §38/§38-1 |
| X-例外 | 2 | 但書排除(§21/§48) |

> 另有 195 句 UNMATCHED = **刻意不抽**的類別(適用範圍 §1–9、易刑 §41–44、
> 數罪併罰細節 §51、加減例 §64–73、緩刑/假釋/時效 §74–85 等計算與程序規則),
> 保留於 `facts_pilot.json` 供錯誤分析;此類規則日後以規則引擎處理,原文仍可經全文檢索取得。

> 人工複核後的關係修正已內建於 parser(`_ENUM_LISTS`:列舉型條文一律 LISTS;`_FORCE_AGG`:§226/§226-1 加重結果犯強制 AGGRAVATES),`cleanup.cypher` 僅保留 A 段診斷查詢供複核用,無須再手動清理。

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

- [x] **第四層:OpenIE 語意三元組**(pilot:殺人+傷害罪章 62 個,查核 62/62)
- [x] **總則擴抽 §1–99**(選抽 81 個:定義/責任/未遂/共犯/累犯/量刑/沒收/保安處分;查核 ✓134/△8/✗1)
- [ ] Held-out 評估:規則凍結,直接跑竊盜/詐欺罪章,計算 precision / recall(誠實的成績)
- [ ] 抽取範圍擴至整部分則,錯誤分析 → 規則迭代
- [ ] 概念層 (Concept):建法律概念詞表,把散落各條的同一概念(故意/重傷/幫助犯)合併為共享節點
- [ ] 三元組向量嵌入(Neo4j vector index),作為 GraphRAG 檢索入口
- [ ] 查詢理解層:LLM 把口語問題正規化為法律用語後再檢索
- [ ] 規則引擎:易刑/加減例/緩刑假釋時效等計算與程序規則(§41–44、§51、§64–73、§74–85,可用 penalty_*_months 屬性計算)

---

## 已知限制 (Known Limitations)

- 橫向關係以關鍵字啟發式抽取,列舉型條文需人工複核(修正已內建 parser)。
- 「第○條第○項」引用一律連到「條」(三層模型的設計決定,項級語意由第四層三元組承擔)。
- 跨法典引用不建邊:§98(刑事訴訟法 §121-1)、§185-3(陸海空軍刑法 §54)、§294-1(人口販運防制法 §32/§33)之條號屬其他法規,parser 會辨識法規名稱前綴並略過,避免誤指到刑法同號條文。
- 「第X條**至**第Y條」的範圍引用只抓到頭尾兩條,中間條號未展開。
- Fact 抽取規則以已抽章節迭代調校,**查核成績(✓134/△8/✗1)為訓練集成績**;泛化能力需 held-out 章節評估。
- 但書若無「不在此限」字樣,省略主語無法補全(§16「按其情節,得減輕其刑」失去前文脈絡,已標 ✗)。
- 「擬制」條款(§3 II、§31、§47 II「以…論」)以「定義」謂詞近似表達,嚴格語意應為「視為」。

---

## 注意:不要上傳憑證

Neo4j Aura 的連線憑證檔(`Neo4j-*.txt`,含密碼)**不可**提交到版本控制,已列入 `.gitignore`。
