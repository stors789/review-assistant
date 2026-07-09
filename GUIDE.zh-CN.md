# 综述助手 · 人话指南

## 这是什么

一个命令行工具包，帮你把 Zotero 里的 PDF 论文变成结构化的文献综述。

你不用一篇篇地读、一段段地抄、一页页地整理。工具会自动读 PDF → 提取发现 → 写大纲 → 写正文 → 验证引用 → 生成图表，最后给出一份带参考文献的 Markdown 报告和一篇可读的综述文章。

---

## 五分钟上手

```bash
# 1. 装依赖
pip install -r requirements.txt

# 2. 设 API 密钥
export DEEPSEEK_API_KEY="sk-..."
export SS_API_KEY="..."

# 3. 看看 Zotero 里有什么
python -m review_assistant.zotero_read --list

# 4. 跑第一个综述
python -m review_assistant.explore_synthesize "你的Zotero集名" -q "你的研究问题" -o ./my_review
```

跑完打开 `my_review/article.md`，就是一篇完整综述。完整参数和环境变量见 [README](./README.md)。

---

## 它怎么干活（整体流程）

```
Zotero 论文集的 PDF
        │
        ▼
  ┌─ 找论文 ──────────────────────────────────────┐
  │  能力E: 自动从 Semantic Scholar / PubMed 搜     │
  │  文献，直接写到 Zotero 某个集里              │
  └───────────────────────────────────────────────┘
        │
        ▼
  ┌─ 拆论文 ──────────────────────────────────────┐
  │  能力A: 批量把 PDF → 结构化字段              │
  │  (背景/目的/方法/结果/结论/创新/不足)        │
  └───────────────────────────────────────────────┘
        │
        ▼
  ┌─ 探索总结 ─────────────────────────────────────┐
  │  能力D: 给一个研究问题 + 一个论文集           │
  │  → 自动生成综述报告 + 表格 + 示意图          │
  │                                                │
  │  Step 1  逐篇提取相关发现 (并发, 有多轮缓存) │
  │  Ver 1   验证发现是否忠于原文                 │
  │  Step 2  根据所有发现生成大纲                 │
  │  Step 3  逐节匹配发现 + 并行写正文            │
  │  Step 4  拼接各节, 润色统稿, 生成参考文献     │
  │  Ver A/B 检查引用/逻辑正确性                   │
  │  Step 6  根据验证结果修正报告                 │
  │  Step 5  把报告改写为流畅综述文章             │
  │  Step 7  生成总结表格 + Mermaid 示意图        │
  └───────────────────────────────────────────────┘
        │
        ▼
  ┌─ 验证 ────────────────────────────────────────┐
  │  能力B: 给你一段文字 (比如你写的综述段落)     │
  │  → 拆成主张 → 逐一在论文集中找原文验证       │
  │  → 告诉你每句话有没有文献支撑                │
  └───────────────────────────────────────────────┘
        │
        ▼
  ┌─ 查库 ────────────────────────────────────────┐
  │  能力C: 随时看看 Zotero 里有什么             │
  │  → 有多少论文集 / 多少有 PDF / 缺哪些       │
  └───────────────────────────────────────────────┘
```

---

## 完整工作流示例

### 示例一：从零写一篇综述

假设你想写 "混合检索与重排如何提升 RAG 系统准确性" 的综述。

**第一步：搜文献补集**

```bash
python -m review_assistant.auto_lit "hybrid search reranking retrieval augmented generation" \
    --web-import -c "信息检索 > RAG" -t rag-review -n 20
```

如果还没配 Web API，先跑不带 `--web-import` 的版本，手动导入生成的 RIS 文件。

**第二步：看看集里有什么**

```bash
python -m review_assistant.zotero_read "信息检索 > RAG"
```

确认 PDF 覆盖情况。缺 PDF 的先把 PDF 拖进 Zotero。

**第三步：跑综述流水线**

```bash
python -m review_assistant.explore_synthesize "信息检索 > RAG" \
    -q "混合检索与重排技术如何提升检索增强生成（RAG）系统的回答准确性与忠实度" \
    -o ./rag_review
```

这一步会跑完完整的 7 步流水线（提取发现→生成大纲→写正文→验证→修正→叙事文章→表格+示意图）。30 篇论文大约需要 20-40 分钟。

**第四步：检查输出**

```bash
ls rag_review/
# article.md       - 叙事综述文章（这是你想读的）
# report.md        - 结构化报告（含参考文献）
# table.md         - 总结表格
# diagram.md       - Mermaid 示意图
# verification.md  - 验证报告
```

**第五步：补缺口**

打开 `table.md`，找标了 `~无数据` 的单元格。这些是你的论文集没覆盖到的点。比如表格里 "查询扩展" 那一行全是 `~无数据`，就搜：

```bash
python -m review_assistant.auto_lit "query expansion retrieval augmented generation" \
    --web-import -c "信息检索 > RAG" -t rag-review -n 10
```

把新文献拖进 Zotero 的 RAG 集，然后重跑流水线（缓存会自动跳过已处理的论文）：

```bash
python -m review_assistant.explore_synthesize "信息检索 > RAG" \
    -q "混合检索与重排技术如何提升检索增强生成（RAG）系统的回答准确性与忠实度" \
    -o ./rag_review_v2
```

---

### 示例二：验证已有综述段落

你写了一篇综述的某个段落，想确认每句话都有文献支撑。

**第一步：确保目标集有相关论文的 PDF**

```bash
python -m review_assistant.zotero_read "信息检索 > RAG"
```

如果论文不够，先用能力E搜一波补进去。

**第二步：把段落存成文件**

```bash
cat > my_paragraph.md << 'EOF'
混合检索将稀疏检索（如 BM25）与稠密检索（如 DPR）结合，
在 RAG 系统中显著提升了检索召回率。重排序模型（如 cross-encoder）
进一步过滤了不相关文档，使最终生成答案的事实准确率提高了 15-20%。
然而在专业领域（如医疗和法律），混合检索的优势不如开放域明显。
EOF
```

**第三步：验证**

```bash
python -m review_assistant.claim_verify "信息检索 > RAG" -f my_paragraph.md -o check_report.json
```

输出会告诉你每句话的支持度：完全支持 / 部分支持 / 弱支持 / 不支持 / 矛盾。不支持或矛盾的主张需要特别关注——可能你的理解有误，也可能是论文集缺文献。

---

### 示例三：深入阅读某一篇论文

你想系统理解一篇 PDF，提取结构化笔记。

```bash
# 单篇或几篇 PDF 放一个文件夹
python -m review_assistant.paper_breakdown -i ~/Downloads/paper_folder -o ./notes
```

输出每篇一个 JSON，包含背景、目的、方法、结果、结论、创新点、不足：

```bash
cat notes/_summary.csv
```

也可以对整个 Zotero 集拆解：

```bash
python -m review_assistant.paper_breakdown -z "信息检索 > RAG" -o ./rag_notes -w 5
```

---

## 常见坑

| 问题 | 原因 | 处理 |
|---|---|---|
| 扫出来的结果全是 `~无数据` | 该集里论文和这个问题不相关 | 换更具体的问题，或者先搜相关文献补集 |
| 某篇跑了好几遍还在调 API | 缓存版本变了或 PDF 内容变了 | 正常行为，让它跑完就缓存了 |
| API 余额跑一半没了 | DeepSeek 按量计费 | 充值后用 `--skip-step1` 从断点继续 |
| 链接附件找不到 | Zotero 里的路径和本机不一致 | 设 `ZOTERO_LINKED_BASE_DIR` 或 `ZOTERO_LINKED_PREFIX_MAP` |
| 同名 PDF 搞混引用 | 两个不同论文的 PDF 刚好都叫 `fulltext.pdf` | 已修复：现在用全路径区分 |
| 报告里参考文献编号对不上 | Step 4 自动清理了孤儿引用 | 正文里实际引用过的编号才会出现在参考文献里 |
| 提示找不到模块 | 没装依赖 | `pip install -r requirements.txt` |
| Zotero 提示 database is locked | Zotero 正在写数据库 | 等几秒再跑（读取不受影响） |
| PDF 拆出来是空的 | 扫描版或加密 PDF | 先用 OCR 工具转文字层 |
| Semantic Scholar 搜不了 | 请求太频繁 | 缩短关键词（≤5 个词），等 1-2 小时 |
| JSON 解析失败 | LLM 没输出合法 JSON | 单篇重试或换 `-m deepseek-reasoner` |

---

## 环境速查

完整环境变量列表见 [README §Environment Variables](./README.md#environment-variables)。

最常用：

```bash
export DEEPSEEK_API_KEY="sk-..."    # 必填
export SS_API_KEY="..."             # 必填（搜文献用）
export PUBMED_API_KEY="..."         # 可选（PubMed 搜索用）
```

一次性加载：把上面几行写到 `~/Documents/api.env`，每次跑之前 `source ~/Documents/api.env`。

跨系统用 Zotero linked-file 时：

```bash
# Windows 建的库在 Mac 上用：
export ZOTERO_LINKED_PREFIX_MAP="C:\Users\eros\= >/Users/eros/"
```

---

## 更多

- 完整参数和命令参考：[README.md](./README.md)
- AI 指令（给 Codex 看的）：[SKILL.md](./SKILL.md)
- 开发备忘：[TODO.md](./TODO.md)
- 更新日志：[CHANGELOG.md](./CHANGELOG.md)
