---
name: review-assistant
description: 综述助手。用途：①Zotero数据库查询与统计；②从Zotero论文集批量拆解PDF论文为结构化字段；③将综述段落分解为独立主张并逐一在原文中验证支持度；④给定研究问题探索论文集生成报告、文章、表格和示意图；⑤自然语言检索Semantic Scholar文献并导入Zotero。触发场景："综述""拆解论文""验证这段文字""核对文献""研究一下""探索一下""帮我找文献""搜索XX文献"。
---

# 综述助手 (Review Assistant)

## 工具清单

所有脚本位于本 skill 的 `scripts/` 目录下，运行前需先 `cd` 到 skill 根目录，确保 Python import 路径正确。

| 脚本 | 用途 | 调用方式 |
|---|---|---|
| `python -m review_assistant.auto_lit` | 自然语言→Semantic Scholar/PubMed 搜索→Zotero Web API 入库或 RIS fallback | CLI |
| `python -m review_assistant.paper_breakdown` | 批量拆解 PDF 论文到结构化字段 | CLI |
| `python -m review_assistant.claim_verify` | 段落→主张分解→Zotero 原文验证 | CLI |
| `python -m review_assistant.explore_synthesize` | 研究问题→探索总结→报告+文章+表格+示意图 | CLI |
| `python -m review_assistant.zotero_read --list` | 浏览 Zotero 论文集层级 | CLI |
| `python -c "import sys; sys.path.insert(0,'scripts'); from zotero_reader import ZoteroReader; ..."` | Zotero 数据库程序化查询 | Python |

## 前置检查

运行任何能力前，确认以下条件全部满足：

1. **API Key**：已配置并导出相关 API 密钥（`DEEPSEEK_API_KEY`、`SS_API_KEY`、`PUBMED_API_KEY`）。详见 [README §Environment Variables](./README.md#environment-variables)。
2. **Zotero 不需要关闭**：`ZoteroReader` 自动复制 SQLite 到临时文件读取，不影响 Zotero 正常运行。
3. **输出目录可写**：`-o` 指定的目录有写入权限。
4. **Python 依赖安装**：`python -m pip install -r requirements.txt` 或 `python -m pip install -e .`。

## 能力匹配规则

按用户意图匹配对应能力，并执行对应 CLI 命令。各能力的完整参数表、CLI 示例详见 [README §Core Workflows](./README.md#core-workflows)。

### 能力C：数据库查询/统计（最高频）

任何"调查/统计/有多少/看看 Zotero"类请求优先使用此能力。

| 用户意图 | 动作 |
|---|---|
| "Zotero里有多少论文集/PDF" | `python -m review_assistant.zotero_read --list` |
| "XX集有多少PDF" | `python -m review_assistant.zotero_read "父集 > 子集"` 看摘要行 |
| "哪些文献缺PDF" | `python -m review_assistant.zotero_read "父集 > 子集"` 看 [缺] 标记 |
| "统计各集PDF覆盖情况" | `python -m review_assistant.zotero_read --list` |
| "调查/看看Zotero里XX情况" | 先用 `--list` 概览，不够再用 `ZoteroReader` 写 Python 查询 |

程序化查询时注意：`list_collections()` 自动跳过 `path` 为空或被标记删除的附件记录，只统计实际存在的本地 PDF。

### 能力A：文献拆解

| 用户意图 | 动作 | 参数详见 |
|---|---|---|
| "拆解 XX 这个集" | `python -m review_assistant.paper_breakdown -z "父集 > 子集" -o ./output -w 5` | [README §2](./README.md#2-break-down-papers能力a) |
| "这篇PDF拆解一下" | `python -m review_assistant.paper_breakdown -i /path/to/folder -o ./out` | 同上 |
| "帮我写这篇论文的阅读笔记/摘要" | 同上，然后读取 JSON | 同上 |
| "整理一下 XX 论文集的核心发现" | 拆解后读取 `_summary.csv` | 同上 |

### 能力B：主张验证

| 用户意图 | 动作 | 参数详见 |
|---|---|---|
| "帮我验证这段文字" | `python -m review_assistant.claim_verify "<选集>" -p "文字..."` 或 `-f file.md` | [README §3](./README.md#3-verify-claims能力b) |
| "这段综述里有文献支撑吗" | `python -m review_assistant.claim_verify "<选集>" -p "..." -o report.json` | 同上 |
| "核对一下引用是否准确" | `python -m review_assistant.claim_verify "<选集>" -f paragraph.md` | 同上 |

### 能力D：探索总结

| 用户意图 | 动作 | 参数详见 |
|---|---|---|
| "研究一下 XX 集里关于 YY 的文献" | `python -m review_assistant.explore_synthesize "<选集>" -q "YY"` | [README §4](./README.md#4-explore-and-synthesize能力d) |
| "写一篇关于 XX 的综述" | 同上，自动生成 report.md + article.md + table.md + diagram.md | 同上 |
| "续写/修正上次的报告" | 用 `--skip-step1` 跳过提取阶段 | 同上 |

流水线详细步骤见 [README §Full Synthesis Pipeline](./README.md#full-synthesis-pipeline)。

### 能力E：文献检索入库

| 用户意图 | 动作 | 参数详见 |
|---|---|---|
| "帮我找 XX 文献，放进 XX 集" | 翻译关键词后调 `auto_lit.py --web-import -c "<目标集>"`；未配置 Web API 时退回 RIS 导入 | [README §5](./README.md#5-search-and-import-literature能力e) |
| "报告里的缺口补一下" | 识别表格中 `~无数据` 单元格，逐条搜→导入 | 同上 |

### ZoteroReader 程序化查询

当 CLI 能力不足时，直接用 `ZoteroReader` 写 Python 查询：

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

## 输出格式 Schema

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

JSON 报告包含每条主张的逐篇验证详情。终端同时输出 Markdown 格式总结（可靠性评估、支持度表格、修正建议、引用建议）。支持度分五档：完全支持 / 部分支持 / 弱支持 / 不支持 / 矛盾。

## 稳定性机制

- **temperature=0**：除 Step5（叙事文章用 0.3）外，所有 API 调用温度为 0，同一输入 → 同一输出。
- **双层缓存**：PDF 文本 → PDF 内容 SHA256 缓存；API 发现 → PDF 内容 hash + 问题 + 模型 + 缓存版本。换问题/模型或 PDF 内容变化都会重新提取。
- **多 key 轮转**：自动检测 `DEEPSEEK_API_KEY_2`、`_3`、`_4`... 突破单 key 速率限制。
- **孤儿引用自动清理**：参考文献只保留正文中实际 `[N]` 引用过的文献。

## 输出质量检查

运行结束后，报告自动通过以下检查：
- ✅ 引用年份/作者名是否与原始发现一致
- ✅ 跨章节是否存在逻辑矛盾
- ✅ 参考文献是否全部被正文引用（零孤儿）
- ✅ 是否出现 LLM 自说自话的元文本（禁止）

## 通用性与可移植性开发规范（开发必读）

后续对此项目进行维护、开发或扩展代码时，**必须**严格遵守以下规范：

1. **禁止硬编码任何特定科研课题的专有词组与筛选规则**：
   - 严禁在代码中直接写死针对特定领域的关键词列表或特定匹配逻辑。
   - 所有筛选或分析规则，必须提取为通用的数据结构（如 JSON/YAML 规则配置文件），并通过类似 `--screen-rules` 的配置参数动态加载。
   - 必须提供默认配置回退字典（如 `DEFAULT_SCREENING_RULES`），以在未提供自定义规则时充当默认行为，且确保不破坏既有单元测试。
2. **严守跨平台路径归一化与 Zotero linked-file 解析规约**：
   - 处理任何涉及本地文件路径（特别是 Zotero 附件 PDF）的逻辑时，禁止假设特定操作系统的斜线格式。
   - 必须通过 `Path` 接口进行跨平台路径包装，并优先采用 `zotero_reader.py` 中实现的盘符路径映射和根目录还原逻辑（支持 `ZOTERO_LINKED_PREFIX_MAP` 和 `ZOTERO_LINKED_BASE_DIR`），确保 Windows 和 Unix 环境下的相互迁移。
3. **保持多大模型厂商 API 的参数自适应兼容**：
   - 在向不同 LLM 模型发起 API 调用时，严禁硬编码特定厂商专有的超参数。
   - 如确实要使用，必须在 `llm_client.py` 等底层调用端做异常容错降级：当遇到 API 参数错误（400 Bad Request）时，应自动剥离这些专有参数并原地重试。
