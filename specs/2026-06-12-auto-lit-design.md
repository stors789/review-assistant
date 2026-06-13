# auto_lit: 自然语言驱动的文献自动检索入库

## Overview

用户用自然语言描述文献需求，脚本自动搜索 Semantic Scholar → 去重 → 入库 Zotero。PDF 下载由 Zotero 内置插件（Sci-Hub）完成，不在此脚本范围内。

## Trigger

通过 opencode 聊天触发：用户说"帮我找 XX 文献，放进 电波 > alpha"，agent 调用脚本完成。

## Architecture

```
用户自然语言 ──→ LLM翻译关键词 ──→ Semantic Scholar API ──→ 去重检查 ──→ Zotero本地API写入
                     ↑                        ↓
                  (agent)              返回DOI+元数据
```

## Components

### 1. 自然语言 → 关键词（agent 侧完成，不进入脚本）

Agent 收到用户自然语言后，先翻译为 SS 关键词再调用脚本。脚本只收关键词。

```
用户: "找 alpha 和 FDG-PET 在健康老年人里的文献"
agent: "alpha EEG FDG-PET glucose metabolism healthy elderly" → 调脚本
```

### 2. 获取 Zotero 集 key

复用 `zotero_reader.py`。`ZoteroReader` 已有集合查询能力，通过 collection name 查找对应的 key。如果不存在则报错退出。

### 2. Semantic Scholar 搜索

端点：`GET https://api.semanticscholar.org/graph/v1/paper/search`
参数：`query`, `limit=20`, `fields=title,authors,year,externalIds,journal`

返回每条包含 DOI（`externalIds.DOI`）、标题、作者、年份、期刊。

### RIS 文件导入方式（采用）

Zotero 7 移除了完整 HTTP API，改用 RIS 文件导入：

1. 搜索 SS → 拿到 DOI + 元数据
2. 生成 RIS 文件（标准引文格式）
3. 查 Zotero SQLite 中已有 DOI，跳过重复
4. 用户双击 RIS 或用 Zotero → File → Import 导入

优点：简单可靠，不需要 API 配置。缺点：需手动导入一步。

### 5. 输出

终端打印：
```
🔍 搜索: "alpha EEG FDG-PET glucose metabolism healthy elderly"
📥 找到 20 篇，已入库 17 篇，跳过 3 篇（重复）
💡 打开 Zotero，选中新条目，右键 → Find Available PDF 下载全文
```

## CLI

```bash
python3 scripts/auto_lit.py "<英文关键词>" [-o output.ris] [-n 20]
```

Agent 负责把用户自然语言翻译为英文关键词后传入。

| 参数 | 说明 | 默认值 |
|---|---|---|
| `keywords` | SS 搜索关键词（空格分隔，英文） | — |
| `-o` / `--output` | RIS 输出路径 | `ss_<uuid>.ris` |
| `-n` / `--limit` | 最大返回数 | `20` |

## Integration with explore_synthesize

`explore_synthesize` 跑完后，agent 读取报告和表格，识别缺口模式（`~ 无直接数据` 等），列出清单询问用户。用户确认后 agent 逐条翻译检索词并调用 `auto_lit.py` 生成 RIS 文件。用户导入 Zotero 后补 PDF，重跑 explore 即可补全。

```
explore 跑完
→ agent: "发现 3 个缺口: ①健康衰老+CBF ②AD+FDG-PET ③VaD+ASL。要搜吗？"
→ 用户: "搜 ① 和 ②"
→ agent: 翻译关键词 → auto_lit.py → 生成 gap_1.ris, gap_2.ris
→ 用户: 双击 RIS 导入 Zotero → Find Available PDF → 重跑 explore
```

## Dependencies

- `requests`（HTTP 调用）
- `zotero_reader.py`（复用，查 collection 结构）
- 无 OpenAI 依赖（LLM 翻译在 agent 侧完成）

## Error Handling

| 场景 | 处理 |
|---|---|
| Zotero 未运行 | 提示 "请先打开 Zotero" |
| SS API 无结果 | 提示 "未找到匹配文献" |
| SS API 超时 | 重试 1 次，仍失败则退出 |
| Zotero API 写入失败 | 打印失败的条目信息，继续处理下一条 |

## Limitations

- 不下载 PDF
- 不支持创建新 Zotero 集（仅写入已有集）
- 单次最多 20 篇
- 依赖 Zotero 本地运行
