---
name: review-assistant
description: 综述助手。用途：①Zotero数据库查询与统计；②从Zotero论文集批量拆解PDF论文为结构化字段；③将综述段落分解为独立主张并逐一在原文中验证支持度；④给定研究问题探索论文集生成报告、文章、表格和示意图；⑤自然语言检索Semantic Scholar文献并导入Zotero。触发场景："综述""拆解论文""验证这段文字""核对文献""研究一下""探索一下""帮我找文献""搜索XX文献"。
---

# 综述助手 (Review Assistant)

## 工具清单

所有脚本位于本 skill 的 `scripts/` 目录下，运行前需先 `cd` 到 skill 根目录，确保 Python import 路径正确。

| 脚本 | 用途 | 调用方式 |
|---|---|---|
| `python scripts/auto_lit.py` | 自然语言→Semantic Scholar 搜索→RIS 导入 Zotero | CLI |
| `python scripts/paper_breakdown.py` | 批量拆解 PDF 论文到结构化字段 | CLI |
| `python scripts/claim_verify.py` | 段落→主张分解→Zotero 原文验证 | CLI |
| `python scripts/explore_synthesize.py` | 研究问题→探索总结→报告+文章+表格+示意图 | CLI |
| `python scripts/zotero_read.py --list` | 浏览 Zotero 论文集层级 | CLI |
| `python -c "import sys; sys.path.insert(0,'scripts'); from zotero_reader import ZoteroReader; ..."` | Zotero 数据库程序化查询 | Python |

## 前置检查

运行任何能力前，确认以下条件全部满足：

1. **API Key**：已配置并导出相关 API 密钥（如 `DEEPSEEK_API_KEY`、`SS_API_KEY`）
2. **Zotero 不需要关闭**：`ZoteroReader` 自动复制 SQLite 到临时文件读取，不影响 Zotero 正常运行
3. **输出目录可写**：`-o` 指定的目录有写入权限
4. **Python 依赖安装**：`python -m pip install -r requirements.txt` 或本地可编辑安装 `python -m pip install -e .`


## 能力 A：文献拆解

用户指定一个 Zotero 论文集后，运行：

```bash
source ~/Documents/api.env
python scripts/paper_breakdown.py -z "<选集路径>" -o ./output -w 5
```

- `<选集路径>` 使用层级路径，如 `"电波 > alpha"`
- `-w` 控制并发数，默认 3
- 已处理的文献自动跳过，可安全重复执行
- 输出：`./output/*.json` (每篇) + `_summary.csv` (汇总)
- 也支持直接处理文件夹中的 PDF：`python scripts/paper_breakdown.py -i /path/to/pdfs -o ./output`

**前置**：先运行 `python scripts/paper_breakdown.py --list-collections` 确认论文集路径和 PDF 覆盖情况。

## 能力 B：段落主张验证

用户提供一段文字 + 一个 Zotero 选集后，运行：

```bash
source ~/Documents/api.env
python scripts/claim_verify.py "<Zotero 选集路径>" -p "段落文本..." -o report.json
```

或从文件读取：
```bash
python scripts/claim_verify.py "<选集路径>" -f paragraph.md -o report.json
```

### 流程

1. **拆解** — DeepSeek 将段落分解为 3~8 条独立、可验证的学术主张
2. **匹配** — 每条主张自动匹配论文集中最相关的 N 篇论文（默认 3）
3. **验证** — 逐篇比对原文，输出支持度（完全支持/部分支持/弱支持/不支持/矛盾）+ 原文证据
4. **总结** — 生成 Markdown 报告：可靠性评估、修正建议、引用建议

### 参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `collection` | Zotero 论文集路径（必填） | — |
| `-p` / `--paragraph` | 待验证段落 | — |
| `-f` / `--file` | 从文件读段落 | — |
| `-m` / `--model` | DeepSeek 模型 | `deepseek-v4-flash`（拆解/验证）/ `deepseek-v4-pro`（探索总结） |
| `--top` | 每主张最多验证论文数 | `3` |
| `-o` / `--output` | JSON 报告输出路径 | 仅终端输出 |

## 能力 C：Zotero 数据库查询与统计

**这是最高频的能力，任何"调查/统计/有多少/看看Zotero"类请求都优先使用此能力**。

### C1：浏览论文集概览

```bash
python scripts/zotero_read.py --list
```

输出所有论文集的名称、条目总数、有PDF数、缺PDF数。

### C2：查看单集详情

```bash
python scripts/zotero_read.py "电波 > alpha"                   # 列出单集全部文献
python scripts/zotero_read.py --pdf-only "电波 > alpha"        # 只看有 PDF 的
```

每篇显示：标题、作者、期刊、日期、DOI、PDF路径。

### C3：程序化查询（任意SQL/统计）

当用户的请求超出了上述CLI的能力（如"alpha集有多少PDF"、"对比两个集的覆盖情况"、"统计某集近年文献数量"），直接用 `ZoteroReader` 写 Python 查询：

```python
cd <skill目录>
python -c "
import sys; sys.path.insert(0, 'scripts')
from zotero_reader import ZoteroReader

with ZoteroReader() as r:
    # r._query(sql) 直接执行任意sqlite3查询
    # r.list_collections() 返回全集概况
    # r.list_items('collection_name') 返回单集文献列表
    # r.get_papers('collection_name') 仅返回有PDF的文献
    ...
"
```

**常用查询模式：**
- **统计数量**：用 `list_collections()` 或写 SQL 统计
- **PDF 覆盖**：`list_collections()` 已包含 `has_attachment` / `missing` 字段
- **对比多个集**：分别调用 `list_collections()` 后比较
- **检查某集是否缺 PDF**：`list_items()` 返回的每条含 `pdf_available` 布尔值
- **注意**：`list_collections()` 会自动跳过 `path` 为空或被标记删除的附件记录，只统计实际存在的本地 PDF，比手写 SQL 更准确

## 能力 D：探索总结

给定一个研究问题 + Zotero 论文集，直接从论文全文提取答案，生成结构化报告和叙事综述文章。**不依赖外部段落输入**，让论文自己回答研究问题。

### 快速启动

```bash
source ~/Documents/api.env
cd ~/.agents/skills/review-assistant
python scripts/explore_synthesize.py "<选集路径>" -q "研究问题"
```

默认输出到当前目录下的 `synthesize_output/`，可用 `-o` 覆盖。

### 完整流程（6 步 + 3 项验证）

```
Step1 ──→ Ver1 ──→ Step2 ──→ Step3 ──→ Step4 ──→ Ver A/B ──→ Step6 ──→ Step5 ──→ Step7
逐篇提取   字符串验证  生成大纲   匹配+写作  整合报告   引用+逻辑    修正报告   叙事文章   示意图
 并发25篇   本地检索   1次API   14节并行   1次API    2次API      1次API    1次API   1次API
```

| 步骤 | 说明 | 关键设计 |
|---|---|---|
| **Step1** | 每篇 PDF 全文→LLM 提取发现（claim_cn + quote + cite_key + 动态 tags），不相关跳过 | temperature=0 保证确定性；PDF 文本 + API 结果双层缓存，同问题重跑不重复调用 |
| **Ver1** | quote 在原文中字符串检索（精确/模糊），找不到则重提取（最多 2 轮） | 本地匹配，不耗 API |
| **Step2** | LLM 审视所有发现→自动生成报告大纲，叶子节点带 search_tags | 严格按研究问题指定人群分组，无关人群不设独立章节 |
| **Step3** | Phase A: 逐节用 tags 语义匹配 3-8 条发现。Phase B: 逐节并行撰写正文 | 未匹配发现直接丢弃（宁漏不噪）；每节输入几百字不降智 |
| **Step4** | 拼接各节→LLM 润色统一风格，自动识别并标注跨章节矛盾 | 参考文献自动去孤儿（只保留正文引用的） |
| **Ver A** | 检查引用年份/作者名/脑区是否与发现索引一致 | 用 reasoning 模型 + 65536 tokens |
| **Ver B** | 检查跨章节矛盾、结论跳跃、无引用断言 | 同上 |
| **Step6** | 如有验证问题，LLM 根据反馈修正报告（事实修正 + 矛盾分析 + 删无关章节） | 温度=0，禁止输出元文本 |
| **Step5** | 最终报告→流畅综述文章，参考文献一字不改 | 温度=0.3（唯一例外，保证文笔流畅） |
| **Step7** | 生成总结表格（人群×指标）和 Mermaid 示意图 | 表格优先，自动着色 |

### 参数

| 参数 | 说明 | 默认值 |
|---|---|---|
| `collection` | Zotero 论文集路径（必填，可多个） | — |
| `-q` / `--question` | 研究问题（必填） | — |
| `-o` / `--output` | 输出目录 | `synthesize_output` |
| `-m` / `--model` | 模型 | `deepseek-v4-pro` |
| `-w` / `--workers` | 并发数 | `5` |
| `--skip-step1` | 跳过 Step1，从已有 findings 继续 | — |
| `--skip-verify` | 跳过所有验证 | — |
| `--max-papers` | 最大处理论文数 | `0`（无限制） |

### 输出

```
synthesize_output/
├── cache/                  # PDF 全文缓存（SHA256 命名）
├── findings/               # Step1 单篇发现 JSON（内容 hash + 问题 + 模型 + 缓存版本）
├── outline.json            # Step2 大纲
├── outline.meta.json       # Step2 大纲缓存元数据
├── sections.json           # Step3 各章节草稿
├── sections.meta.json      # Step3 各章节缓存元数据
├── report.md               # Step4/6 结构化报告
├── report.meta.json        # Step4 报告缓存元数据
├── article.md              # Step5 叙事文章
├── table.md                # Step7 总结表格
├── diagram.md              # Step7 Mermaid 示意图
├── verification.md         # Ver A/B/B0 第一次验证报告
├── verification_after_fix.md # 修正后二次验证报告（仅在发现问题且启用修正时生成）
└── evidence_coverage.json  # EvidencePack 覆盖率报告
```

### Step7 示意图

自动生成 Mermaid 流程图，展示不同人群（正常成年人、健康衰老、MCI、神经退行性疾病）中各脑区与 alpha 功率的耦合方向（正/负/矛盾），颜色编码区分。可在 GitHub、Obsidian、Notion 等支持 Mermaid 的平台上直接渲染。

本地渲染：`mmdc --input diagram.md --output diagram.svg --backgroundColor white`

## 能力 E：自动文献检索入库

用户用自然语言描述文献需求，agent 翻译为 SS 关键词后调 `auto_lit.py` 搜索并导入 Zotero。

```bash
python scripts/auto_lit.py "<英文关键词>" -c "<目标Zotero集>" -t "<标签>" -m 5 -n 10
```

| 参数 | 说明 | 默认值 |
|---|---|---|
| `keywords` | SS 搜索关键词（英文） | — |
| `-c` / `--collection` | 目标 Zotero 集路径（提示用） | — |
| `-t` / `--tag` | Zotero 标签 | — |
| `-m` / `--min-citations` | 最低引用数（过滤水刊） | `0` |
| `-n` / `--limit` | 最大返回数 | `20` |
| `--screen` | 标题/摘要年份感知相关性筛选，先召回再筛选 | 关闭 |
| `--min-relevance` | `--screen` 模式最低相关性分数 | `4` |
| `--import-zotero` | 自动打开 Zotero 并触发 RIS 导入（仅 macOS） | 关闭 |


### 年份感知筛选

交叉主题或近年文献不宜只用硬引用阈值。需要保护新文献时优先使用：

```bash
python scripts/auto_lit.py "theta EEG FDG PET" --screen --min-relevance 4 -m 0 -n 20 -c "电波 > theta" -t theta-metabolic-coupling
```

`--screen` 会根据标题、摘要和期刊对候选文献打分：
- theta/EEG 相关信号
- FDG/PET/glucose/BOLD/fMRI/CBF/SPECT/ASL/perfusion 等代谢或血流信号
- healthy adult/aging/MCI/Alzheimer/dementia/neurodegenerative 等目标人群
- human/coupling/correlation/prediction 等研究设计信号
- animal/children/epilepsy/psychiatry/anesthesia 等偏题信号扣分

导出的 RIS 会增加 `screen:A/B` 和 `score:N` 关键词，方便在 Zotero 中复查。未开启 `--screen` 时，`-m` 仍保持原来的硬引用数过滤行为。

### 与 explore_synthesize 集成

`explore_synthesize` 跑完后，agent 读取表格识别缺口（`~无数据` 单元格），列出清单询问用户。用户确认后 agent 逐个搜→导入→提示下载 PDF→重跑 pipeline。

### 搜索质量保障

- 仅用 Semantic Scholar（带 API key），不回退 OpenAlex
- 文件锁限流 1.5 秒/次，跨进程生效
- 查询关键词不超过 5 个（过长触发 429）
- 自动查 Zotero 已存 DOI，跳过重复

### 稳定性机制

- **temperature=0**：除 Step5（叙事文章用 0.3）外，所有 API 调用温度为 0，同一输入 → 同一输出
- **双层缓存**：PDF 文本 → PDF 内容 SHA256 缓存；API 发现 → PDF 内容 hash + 问题 + 模型 + 缓存版本。**换问题/模型或 PDF 内容变化都会重新提取**
- **多 key 轮转**：自动检测 `DEEPSEEK_API_KEY_2`、`_3`、`_4`... 突破单 key 速率限制
- **孤儿引用自动清理**：参考文献只保留正文中实际 `[N]` 引用过的文献

### 输出质量检查

运行结束后，报告自动通过以下检查：
- ✅ 引用年份/作者名是否与原始发现一致
- ✅ 跨章节是否存在逻辑矛盾
- ✅ 参考文献是否全部被正文引用（零孤儿）
- ✅ 是否出现 LLM 自说自话的元文本（禁止）

## 用户典型意图 → 动作映射

### 数据库查询/统计（能力C）
1. **"Zotero里有多少论文集/PDF"** → `python scripts/zotero_read.py --list`
2. **"alpha集有多少PDF"** → `python scripts/zotero_read.py "电波 > alpha"`（看摘要行）或程序化查询
3. **"哪些文献缺PDF"** → `python scripts/zotero_read.py "电波 > alpha"` 看 [缺] 标记
4. **"统计各集PDF覆盖情况"** → `python scripts/zotero_read.py --list`
5. **"调查/看看Zotero里XX情况"** → 先用 `--list` 概览，不够再用 `ZoteroReader` 写 Python 查询

### 文献拆解（能力A）
6. **"拆解 alpha 这个集"** → `python scripts/paper_breakdown.py -z "电波 > alpha" -o ./alpha_output -w 5`
7. **"这篇PDF拆解一下"** → `python scripts/paper_breakdown.py -i /path/to/folder -o ./out`
8. **"帮我写这篇论文的阅读笔记/摘要"** → `python scripts/paper_breakdown.py -i /path/to/pdf/folder -o ./output`，然后读取 JSON
9. **"整理一下 XX 论文集的核心发现"** → 拆解后读取 `_summary.csv`

### 主张验证（能力B）
10. **"帮我验证这段文字"** → `python scripts/claim_verify.py "<选集>" -p "文字..."` 或 `-f file.md`
11. **"这段综述里有文献支撑吗"** → `python scripts/claim_verify.py "<选集>" -p "..." -o report.json`
12. **"核对一下引用是否准确"** → `python scripts/claim_verify.py "<选集>" -f paragraph.md`

### 探索总结（能力D）
13. **"研究一下 alpha 集里关于 XXX 的文献"** → `python scripts/explore_synthesize.py "<选集>" -q "XXX"`
14. **"写一篇关于 XXX 的综述"** → 同上，自动生成 report.md + article.md + table.md + diagram.md
15. **"续写/修正上次的报告"** → 用 `--skip-step1` 跳过提取阶段

### 文献检索入库（能力E）
16. **"帮我找 XX 文献，放进 XX 集"** → agent 翻译关键词后调 `auto_lit.py`，弹窗导入 Zotero
17. **"报告里的缺口补一下"** → agent 识别表格中 `~无数据` 单元格，逐条搜→导入

注意：脚本输出默认写到当前工作目录下的输出文件夹，Zotero 不需要关闭。

## Troubleshooting

| 错误现象 | 原因 | 解决 |
|---|---|---|
| `database is locked` | Zotero 正在写入 SQLite | 等待 Zotero 完成操作后重试（读取不受影响） |
| 无法提取 PDF 文本（0 字符） | 扫描版/加密 PDF | 先用 OCR 工具转文字层，或解锁 PDF |
| API 返回 429 Too Many Requests | DeepSeek 请求过多 | 降低 `-w` 并发数 |
| SS 搜索 429 | Semantic Scholar 限流 | 缩短查询关键词（≤5 词），等待 1-2 小时后重试 |
| `Insufficient Balance` | DeepSeek API 余额不足 | 充值后重跑失败的步骤 |
| `论文集没有可用的 PDF` | 该集内所有文献缺 PDF 附件 | 用 `--list-papers` 查看具体哪些文献缺失 |
| `No module named 'pymupdf'` | 缺少 Python 依赖 | `pip install -r requirements.txt` |
| 拆解结果 JSON 解析失败 | LLM 未严格输出 JSON | 单篇重试或用 `-m deepseek-reasoner` 换模型 |
| `API returned empty response` | reasoning token 挤占输出 | 已修复：验证用 65536 tokens

## 输出格式

### 文献拆解输出（JSON）
```json
{
  "file": "paper.pdf",
  "original_title": "Original Title",
  "title_cn": "中文题目",
  "authors": "Author1; Author2",
  "journal_impact": "Journal Name | IF: X.X",
  "year": "2024",
  "background": "研究背景...",
  "objective": "研究目的...",
  "methods": "研究方法...",
  "results": "研究结果...",
  "conclusion": "研究结论...",
  "limitations": "不足与展望...",
  "innovation": "创新点..."
}
```

### 主张验证输出（JSON + Markdown）
JSON 报告包含每条主张的逐篇验证详情，终端同时输出 Markdown 格式总结（可靠性评估、支持度表格、修正建议、引用建议）。

## 前置要求

所有脚本需要 `DEEPSEEK_API_KEY` 或 `OPENAI_API_KEY` 环境变量。

- **Unix Shell**: `export DEEPSEEK_API_KEY="your-key"` 或 `source ~/Documents/api.env`
- **Windows Cmd**: `set DEEPSEEK_API_KEY="your-key"`
- **Windows PowerShell**: `$env:DEEPSEEK_API_KEY="your-key"`

