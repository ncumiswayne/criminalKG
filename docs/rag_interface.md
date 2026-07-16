# RAG 串接介面文件(給檢索/LLM 端)

> KG 端狀態:五層全部上線(概念層為 pilot)。語意層涵蓋 **總則 §1–99(定義/責任能力/正當防衛/未遂/
> 共犯/累犯/量刑/沒收/保安處分,選抽)+ 殺人罪章 §271–276 + 傷害罪章 §277–287**,共 143 個 Fact。
> 概念層:12 個總則概念(未遂/故意/過失/重傷/教唆犯/幫助犯/公務員/性交/電磁紀錄/凌虐/累犯/自首)。
> 未入語意層:易刑、數罪併罰細節、加減例、緩刑/假釋/時效(計算與程序規則)——這些請靠全文檢索取原文。
> 分則其他罪章尚未抽取,測試問題請以殺人/傷害情境為主。

## 圖譜結構(五層)

```
(:Part)-[:CONTAINS]->(:Chapter)-[:CONTAINS]->(:Article)   ← 結構層(全法典 422 條)
(:Fact)-[:EXTRACTED_FROM]->(:Article)                      ← 語意層(143 個 Fact)
(:Article)-[:AGGRAVATES|MITIGATES|CITES|LISTS]->(:Article) ← 條文橫向關係
(:Article)-[:DEFINES {role}]->(:Concept)                   ← 概念層:總則定義條 → 概念
(:Article)-[:USES {applies}]->(:Concept)                   ← 概念層:使用條 → 概念(過濾 applies!)
```

## 節點屬性契約

**Article(條)** — 你要餵給 LLM 的原文單位(= chunk)

| 屬性 | 型別 | 說明 |
|---|---|---|
| `code` | string | 唯一鍵,如 `刑法-271`(= chunk_id) |
| `number` | string | 條號 `'271'`、`'272-1'` |
| `text` | string | **整條原文**(含所有項、款,換行分隔)→ 直接放進 prompt |
| `is_deleted` | bool | true = 已刪除條文,**檢索時排除** |
| `punishes_attempt` | bool | 該條處罰未遂 |
| `punishes_preparation` | bool | 該條處罰預備 |

**Fact(語意事實)** — 檢索入口,不要直接餵 LLM,用它定位條文

| 屬性 | 型別 | 說明 |
|---|---|---|
| `subject` / `predicate` / `object` | string | SPO 三元組,條號已正規化(第277條第1項) |
| `predicate` 白名單 | | 法定刑 / 刑之加重 / 刑之減免 / 未遂處罰 / 加重結果 / 訴追條件 / 定義 / 不罰 / 科刑限制 / 保安處分 / 沒收 / 處罰依據(+`X-例外`表但書排除) |
| `sentence` | string | 消解後的來源句(適合做 embedding 的線性化文本) |
| `article` / `para` | string/int | 出處條、項 |
| `penalty_types` | string | 刑種:`'死刑、無期徒刑、有期徒刑'`(僅法定刑類有) |
| `penalty_min` / `penalty_max` | string | **有期徒刑**區間,如 `'3年'`;無期/死刑不在此,看 types |
| `penalty_min_months` / `penalty_max_months` | int | 上行的月數換算,供數值比較 |
| `penalty_fine_max` | int | 罰金上限(元) |
| `penalty_fine_mode` | string | 併科 / 得併科 |

**Concept(總則概念)** — 查詢理解的落點、分則⇄總則的橋

| 屬性 | 型別 | 說明 |
|---|---|---|
| `cid` | string | 主鍵 `C-{正規名}`,如 `C-未遂`(12 個,全表見 `concepts/concepts.json`) |
| `name` | string | 法定正規名。**只收法條用語**;口語(「沒死成」→未遂)由查詢端 LLM 剖析後對應到 cid |
| `def_article` | string | 主定義條號,如 `'25'` |
| `uses_count` / `defines_count` | int | 度數中繼資料(概念節點天生高度數,擴展前可先看) |

邊:`(定義條)-[:DEFINES {role: 主定義|變體|減免特則}]->(:Concept)`、
`(使用條)-[:USES {trigger, basis, para, applies, review}]->(:Concept)`

> ⚠ **USES 一律過濾 `WHERE u.applies`**。`applies:false` 是「詞面出現但法律上不適用」的
> 否定判斷(§275 教唆自殺**不是** §29 教唆犯),不過濾會把錯誤條文帶進 context 誤導 LLM。

```cypher
// 問題剖析出概念後:一跳取定義條 + 全部適用條(以 C-未遂 為例)
MATCH (def:Article)-[d:DEFINES]->(c:Concept {cid:'C-未遂'})
OPTIONAL MATCH (a:Article)-[u:USES]->(c) WHERE u.applies
RETURN c.name, collect(DISTINCT {條: def.number, 角色: d.role}) AS 定義條,
       collect(DISTINCT a.number) AS 使用條;

// 反向:由命中條文擴展其概念之總則依據(殺人未遂 → §25/26/27)
MATCH (a:Article {number:'271'})-[u:USES]->(c:Concept)<-[d:DEFINES]-(def:Article)
WHERE u.applies
RETURN c.name, d.role, def.number, def.text;
```

## 建議檢索流程(甲持刀傷人致重傷)

```
1. 查詢改寫:LLM 把口語轉法律用語(持刀傷人致重傷 → 傷害人之身體致重傷)
2. 比對 Fact:先用全文比對頂著(embedding 之後補):
   MATCH (f:Fact) WHERE f.subject CONTAINS '重傷' OR f.object CONTAINS '重傷'
   RETURN f
3. 定位條文 + 擴展(一次查完):
   MATCH (f:Fact)-[:EXTRACTED_FROM]->(a:Article)
   WHERE f.subject CONTAINS $kw OR f.object CONTAINS $kw
   MATCH (c:Chapter)-[:CONTAINS]->(a)
   OPTIONAL MATCH (a)<-[r:AGGRAVATES|MITIGATES|CITES|LISTS]-(rel:Article)
   RETURN DISTINCT a.number, a.text, c.title,
          collect(DISTINCT {rel: type(r), art: rel.number, text: rel.text}) AS 相關條文
4. 組 context:命中條文原文 + 相關條文原文 + Fact 的結構化刑度 → LLM 生成
```

## 已知限制(先講清楚,免得踩雷)

- 語意層只有總則 §1–99(選抽)+ 分則 §271–287,其他條只有結構層與橫向關係。
- Fact 尚未做向量嵌入;embedding 建議對 `sentence` 欄位做,或 S+P+O 串接後嵌入。
- 「重傷」「故意」「未遂犯」等定義已入語意層(`predicate='定義'`,出自 §10/§13/§25):
  `MATCH (f:Fact {predicate:'定義'}) WHERE f.subject='重傷' RETURN f` 即可取得,
  命中重傷相關 Fact 時建議一併附帶其定義 Fact 與 §10 原文。
- min/max 只描述有期徒刑;問「最重可判什麼」要看 `penalty_types`。
- 概念層 pilot 只有 12 個總則概念——這些走 Concept 節點圖走訪(§277 的「重傷」和 §10 的「重傷」
  已是同一個 `C-重傷` 節點);12 個以外的概念(如「加重結果」「預備」)仍需字串比對。
- 概念層的詞面掃描候選 168 條尚待人工複核(`review:'pending'`);誤導風險最高的 5 條已裁決為
  `applies:false`,其餘可先信任使用,複核進度見 `concepts/uses_review.md`。

## 測試問題建議(涵蓋不同謂詞)

1. 甲持刀傷人致重傷,會怎麼判?(法定刑+加重結果:§277/§278;重傷定義:§10)
2. 殺人未遂會被處罰嗎?(未遂處罰:§271;未遂定義+得減輕:§25)
3. 打傷父母會加重嗎?(刑之加重:§280)
4. 過失撞傷人,對方沒提告,檢察官會辦嗎?(訴追條件:§284+§287)
5. 相約自殺沒死成,倖存者有罪嗎?(刑之減免:§275)
6. 教唆別人殺人和幫忙把風,罪責差在哪?(定義+處罰依據:§29/§30)
7. 十三歲小孩犯罪會被關嗎?(不罰:§18;保安處分:§86)
8. 正當防衛打傷人有罪嗎?防衛過當呢?(不罰+刑之減免:§23)
9. 出獄後五年內再犯會怎樣?(定義+刑之加重:§47 累犯)
10. 一個行為同時犯兩條罪怎麼算?(處罰依據:§55 想像競合)
