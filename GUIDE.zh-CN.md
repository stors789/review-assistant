# 综述助手 · 人话指南

## 这是什么

一个命令行工具包，帮你把 Zotero 里的 PDF 论文变成结构化的文献综述。

你不用一篇篇地读、一段段地抄、一页页地整理。工具会自动读 PDF → 提取发现 → 写大纲 → 写正文 → 验证引用 → 生成图表，最后给出一份带参考文献的 Markdown 报告和一篇可读的综述文章。

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
  ┌─ 验证 -───────────────────────────────────────┐
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

## 五个能力，人话版

### 能力 E：自动搜文献 + 入库

**"帮我找 XX 相关的文献，放到 XX 集"**

你告诉它关键词和 Zotero 目标集，它去 Semantic Scholar（或 PubMed）搜，自动把新文献写进 Zotero。

```bash
# 搜 "hybrid search retrieval RAG"，使用默认规则初筛并导入 Zotero 集
python scripts/auto_lit.py "hybrid search retrieval RAG" --web-import -c "信息检索 > RAG" -t rag-review

# 使用自定义规则配置文件（my_rules.json）进行跨学科文献初筛并入库
python scripts/auto_lit.py "quantum computing neural networks" \
    --screen \
    --screen-rules ./my_rules.json \
    --web-import \
    -c "计算 > 量子神经网络" \
    -t quantum-nn
```

- **自定义筛选规则**：支持通过 `--screen-rules` 传入自定义的 JSON 配置文件，灵活设定不同关键词分类、加分/扣分权重（扣分用于排除无用文献），让文献检索初筛彻底告别硬编码，能够适用于任何学术课题。
- **自动去重**：已存在的 DOI 不会重复导入。
- **自动创建集**：目标 Zotero 收藏夹（如 `"信息检索 > RAG"`）如果不存在，会自动在云端创建。
- **支持 Web API 直接写入**：无需打开本地 Zotero 软件或弹窗确认即可无感后台入库。
- **RIS 备用方案**：如果未配置 Web API 密钥，会自动生成标准的 RIS 格式文件，双击即可手动拖入 Zotero 导入。

### 能力 A：批量拆解论文

**"帮我把这个集里的 PDF 拆成结构化笔记"**

逐篇读 PDF 全文，用 LLM 提取：背景、目的、方法、结果、结论、创新点、不足。

```bash
# 拆 "信息检索 > RAG" 这个集，5 个并发
python scripts/paper_breakdown.py -z "信息检索 > RAG" -o ./rag_notes -w 5
```

输出：每篇一个 JSON + 一个汇总 CSV。已处理过的自动跳过，可以重复跑。

### 能力 D：从论文集直接写综述

**"研究一下 alpha 功率和衰老的关系"**

给定一个研究问题和一个 Zotero 集，全自动跑完整 7 步流水线：

```bash
# 默认使用常规关键字块匹配与提取：
python scripts/explore_synthesize.py "电波 > alpha" -q "alpha功率如何随衰老变化"

# 开启向量语义相似度检索（Hybrid RAG 混合重排排序）：
python scripts/explore_synthesize.py "电波 > alpha" -q "alpha功率如何随衰老变化" --vector-search
```

输出目录包含：

| 文件 | 内容 |
|---|---|
| `report.md` | 结构化综述报告（含参考文献） |
| `article.md` | 叙事综述文章 |
| `table.md` | 总结表格 |
| `diagram.md` | Mermaid 示意图（可在 Obsidian/Notion 渲染） |
| `verification.md` | 引用和逻辑验证报告 |
| `findings/` | 逐篇发现缓存（下次换问题不会重复调用 API） |

**关键设计：**
- 大部分 API 调用 temperature=0，同一输入 → 同一输出，可复现
- PDF 文本和 API 结果都会缓存，换问题/换模型会重新提取
- 参考文献自动去孤儿（只保留正文中实际引用过的）
- 不会编造不存在的发现（找不到原文支撑的 quote 会标记失败并重试）

### 能力 B：验证一段话有没有文献支撑

**"帮我看看这段综述写得对不对，有没有文献依据"**

把一段文字拆成 3~8 条独立主张，逐条在论文集中找到最相关的论文，比对原文，给出支持度。

```bash
python scripts/claim_verify.py "电波 > alpha" -f my_paragraph.md -o check_report.json
```

每条主张的结论分五档：完全支持 / 部分支持 / 弱支持 / 不支持 / 矛盾。

### 能力 C：看看 Zotero 里有什么

**"Zotero 里有多少集？alpha 集有多少 PDF？缺哪些？"**

```bash
python scripts/zotero_read.py --list          # 全集概览
python scripts/zotero_read.py "电波 > alpha"  # 单集详情
```

不需要关 Zotero，工具自动复制 SQLite 到临时文件读。

---

## 典型工作流举例

假设你想写一篇关于 "混合检索与重排技术在检索增强生成（RAG）系统中的作用" 的综述：

```bash
# 1. 先看看 Zotero 里有没有 RAG 相关的集
python scripts/zotero_read.py --list

# 2. 搜新文献补到 RAG 集
python scripts/auto_lit.py "hybrid search reranking retrieval augmented generation" \
    --web-import -c "信息检索 > RAG" -t rag-review -n 20

# 3. 拆解（可选，想做详细笔记时用）
python scripts/paper_breakdown.py -z "信息检索 > RAG" -o ./rag_notes -w 5

# 4. 跑综述流水线
python scripts/explore_synthesize.py "信息检索 > RAG" \
    -q "混合检索与重排技术如何提升检索增强生成（RAG）系统的回答准确性与忠实度" \
    -o ./rag_review

# 5. 读报告，把表里 ~无数据 的格子记下来，搜文献补缺口
# 6. 重跑（缓存会自动跳过已有结果）
```

---

## 环境要求

### 必须

```bash
# API 密钥
export DEEPSEEK_API_KEY="sk-..."
export SS_API_KEY="..."        # Semantic Scholar (搜文献用)
export PUBMED_API_KEY="..."    # PubMed (搜文献用，可选)

# 安装依赖
pip install -r requirements.txt
```

### Zotero

不需要关 Zotero。工具只读 SQLite。

### 可选配置

常用环境变量（可放在 `~/Documents/api.env` 里一次性 source）：

| 变量 | 作用 | 默认 |
|---|---|---|
| `REVIEW_ASSISTANT_MODEL` | 主模型 | `deepseek-v4-pro` |
| `REVIEW_ASSISTANT_STEP7_MODEL` | 表格/示意图模型 | 跟主模型一致 |
| `REVIEW_ASSISTANT_EMBEDDING_API_KEY` | 向量生成 API 密钥 | 默认使用 `OPENAI_API_KEY` |
| `REVIEW_ASSISTANT_EMBEDDING_BASE_URL` | 向量生成 Base URL | 默认使用 OpenAI 官方端点 |
| `REVIEW_ASSISTANT_WORKERS` | 并发数 | `5` |
| `REVIEW_ASSISTANT_USE_PROXY=true` | 走系统代理 | 不走代理 |
| `ZOTERO_DIR` | Zotero 数据目录 | `~/Zotero` |
| `ZOTERO_LINKED_BASE_DIR` | 链接附件的本地根目录 | 无 |
| `ZOTERO_LINKED_PREFIX_MAP` | Windows 盘符→本机路径映射 | 无 |
| `ZOTERO_API_KEY` | Zotero Web API 写权限密钥 | 无 |
| `ZOTERO_LIBRARY_TYPE` | `user` 或 `group` | `user` |
| `ZOTERO_LIBRARY_ID` | 用户/群组 ID | 无 |
| `ZOTERO_WEB_IMPORT=true` | 默认走 Web API 入库 | `false` |

**跨系统使用 Zotero linked-file 时：** 如果你在 Windows 上建了 linked-file（路径是 `C:\Users\...`），在 Mac 上用时要设：
```bash
export ZOTERO_LINKED_PREFIX_MAP="C:\Users\eros\= >/Users/eros/"
```

---

## 通用性与可移植性设计

综述助手秉持高通用性与跨平台移植性的设计规范，确保能无缝迁移于不同课题和运行环境：

* **文献自定义初筛（解决硬编码问题）**：检索文献初筛过滤系统不绑定任何学术方向。你只需提供一个规则 JSON 文件并通过 `--screen-rules` 参数传入，即可动态覆盖默认的 RAG/LLM 评估筛选规则。配置支持自由定制关键词分类、正负权重计分以及关键评级条件。
* **跨平台 Zotero 路径解析**：哪怕 Zotero 数据库是在 Windows 上建立的（含 Windows 绝对盘符路径 `C:\...`），在 Linux/macOS 上运行时，依然可以通过 `ZOTERO_LINKED_BASE_DIR` 和 `ZOTERO_LINKED_PREFIX_MAP` 的环境变量组合，将它们动态且完美地解耦还原为当前系统的可访问路径。
* **自适应多 LLM 服务商**：内置客户端（`llm_client`）能自动感知底层是大模型云端 API（如 DeepSeek、OpenAI）还是本地运行服务（如 Ollama、LM Studio）。在切换模型触发特定推理参数报错时，客户端会自适应剥离不兼容的 Payload 并自动重试，无需因更换模型商而修改核心代码。

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

---

## 更多

- 项目 README：[README.md](./README.md)
- 技能指令（给 AI 看的）：[SKILL.md](./SKILL.md)
- 开发备忘：[TODO.md](./TODO.md)
- 更新日志：[CHANGELOG.md](./CHANGELOG.md)
- 设计文档：`specs/` 目录
