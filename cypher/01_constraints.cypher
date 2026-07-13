// 步驟1:約束 (先單獨執行這一檔)
// 三層模型:編 Part / 章 Chapter / 條 Article
CREATE CONSTRAINT part_code IF NOT EXISTS FOR (n:Part) REQUIRE n.code IS UNIQUE;
CREATE CONSTRAINT chapter_code IF NOT EXISTS FOR (n:Chapter) REQUIRE n.code IS UNIQUE;
CREATE CONSTRAINT article_code IF NOT EXISTS FOR (n:Article) REQUIRE n.code IS UNIQUE;
