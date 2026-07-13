# RAG 串接介面文件(給檢索/LLM 端)

> KG 端狀態:四層全部上線。語意層目前僅涵蓋 **殺人罪章 §271–276 + 傷害罪章 §277–287**(pilot),
> 測試問題請限制在殺人/傷害相關情境,問其他罪名語意層會查無資料(結構層仍有全法典)。

## 圖譜結構(四層)

```
(:Part)-[:CONTAINS]->(:Chapter)-[:CONTAINS]->(:Article)   ← 結構層(全法典 422 條)
(:Fact)-[:EXTRACTED_FROM]->(:Article)                      ← 語意層(pilot 62 個)
(:Article)-[:AGGRAVATES|MITIGATES|CITES|LISTS]->(:Article) ← 條文橫向關係
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
| `predicate` 固定六種 | | 法定刑 / 刑之加重 / 刑之減免 / 未遂處罰 / 加重結果 / 訴追條件 |
| `sentence` | string | 消解後的來源句(適合做 embedding 的線性化文本) |
| `article` / `para` | string/int | 出處條、項 |
| `penalty_types` | string | 刑種:`'死刑、無期徒刑、有期徒刑'`(僅法定刑類有) |
| `penalty_min` / `penalty_max` | string | **有期徒刑**區間,如 `'3年'`;無期/死刑不在此,看 types |
| `penalty_min_months` / `penalty_max_months` | int | 上行的月數換算,供數值比較 |
| `penalty_fine_max` | int | 罰金上限(元) |
| `penalty_fine_mode` | string | 併科 / 得併科 |

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

- 語意層只有 §271–287,其他條只有結構層與橫向關係。
- Fact 尚未做向量嵌入;embedding 建議對 `sentence` 欄位做,或 S+P+O 串接後嵌入。
- 「重傷」的定義在 §10(總則),目前無概念層自動連結,需要的話沿 CITES 或直接取 §10。
- min/max 只描述有期徒刑;問「最重可判什麼」要看 `penalty_types`。

## 測試問題建議(涵蓋不同謂詞)

1. 甲持刀傷人致重傷,會怎麼判?(法定刑+加重結果:§277/§278)
2. 殺人未遂會被處罰嗎?(未遂處罰:§271)
3. 打傷父母會加重嗎?(刑之加重:§280)
4. 過失撞傷人,對方沒提告,檢察官會辦嗎?(訴追條件:§284+§287)
5. 相約自殺沒死成,倖存者有罪嗎?(刑之減免:§275)
