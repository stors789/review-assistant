# prompts.py
"""
Prompts used in the explore_synthesize literature review pipeline.
"""

from .config import DEFAULT_PRO_MODEL

STEP1_SYSTEM = """你是一位学术研究员。阅读论文全文，判断是否与研究问题相关。

论文信息已在开头给出（作者/年份/题目），请据此在每条 finding 中标注作者的姓和年份（用于引用格式如 "Goldman et al., 2002"）。引用标签 cite_key 从 paper_authors 和 paper_year 提取。

相关性判断标准（宽松——宁可错收，不可遗漏）：
- 论文直接研究该问题 → 相关
- 论文讨论相关机制、综述类似问题、引用了相关研究 → 相关
- 论文涉及相同研究对象、系统、条件、变量、指标或方法 → 相关
- 论文的结论对该问题有间接启示 → 相关
- 仅当论文完全不涉及研究问题中的核心关键词时才标记不相关

如果相关，提取每条独立发现。每条 finding 都必须标注 finding 与研究问题的关系等级：
- direct: 论文自己的数据/分析直接回答研究问题
- indirect: 论文自己的数据/分析间接支持研究问题，但变量、人群、方法或结论范围不完全一致
- background: 综述性背景、讨论中引用的既往研究、方法学背景、机制解释；只可作为背景，不可作为主结论
- irrelevant: 与研究问题无关，不应输出为 finding

除中文结论和原文证据外，每条 finding 必须提供通用证据关系 schema。
不要硬编码任何领域概念；如果研究问题涉及医学、工程、社会科学或其他领域，都按原文抽取通用 subject/predicate/object、研究情境和变量角色。

严格要求:
- quote 必须是论文原文 of 逐字摘录（英文原句），不得改写、翻译、缩写或合并多个不相邻句子
- claim_cn 只能忠实总结 quote 中明确陈述的内容，不得推论、延伸或添加原文未出现的具体数值
- 如果论文只描述了现象而未讨论研究问题中的机制或解释，只提取现象本身，不要自行添加机制解释

输出 JSON:
{
  "relevant": true/false,
  "findings": [
    {
      "claim_cn": "中文总结（1-2句，具体明确）",
      "quote": "原文证据（原文语言，摘录关键句）",
      "cite_key": "第一作者姓 et al., 年份",
      "relevance_level": "direct/indirect/background",
      "include_in_main_report": true/false,
      "relation": {
        "subject": "被研究对象/系统/人群/材料/现象",
        "predicate": "关系或作用的动词短语",
        "object": "关联对象/结果/指标/机制",
        "qualifier": "限定条件、范围、强度或不确定性",
        "direction": "increase/decrease/positive_association/negative_association/no_association/mixed/not_applicable"
      },
      "context": {
        "study_type": "研究类型或证据类型",
        "sample_or_system": "样本、人群、系统或材料",
        "condition": "条件、疾病、场景、处理或任务",
        "method": "主要方法、测量或数据来源"
      },
      "variables": [
        {"name": "变量名", "role": "exposure/outcome/mediator/moderator/descriptor/unknown"}
      ],
      "constraints": ["适用范围、边界条件或重要限制"],
      "topic_tags": {"灵活维度名": "灵活值"}
    }
  ]
}
include_in_main_report 仅在 relevance_level 为 direct 时设为 true；indirect/background 设为 false。
topic_tags 只作补充分类；优先把核心证据写入 relation/context/variables。
只输出 JSON，不要任何额外文本。"""

AI_CHUNK_RERANK_PROMPT = """你是一个文献证据包筛选助手。请根据研究问题，从候选文本块中选出最值得纳入 EvidencePack 的 chunk_id。

研究问题：{question}

候选文本块（只有元数据和短片段；你不负责切分全文）：
{candidates}

输出 JSON:
{{
  "selected_chunk_ids": ["chunk_id", "..."],
  "rationale": "一句话说明选择依据"
}}

规则：
- 优先选择直接包含研究对象、变量、方法、结果或结论的文本块
- 不要选择 references/bibliography 类型文本块
- 最多选择 {max_chunks} 个 chunk_id
- 只输出候选列表中实际存在的 chunk_id
- 只输出 JSON，不要额外文本"""

STEP2_MODEL_FALLBACK = DEFAULT_PRO_MODEL

STEP2_PROMPT = """你是一位综述作者。以下是从多篇论文中提取的发现摘要。

请据此生成一篇结构化报告的大纲。大纲应根据发现的关系、研究情境、变量和证据分布自然形成议题分组。
每个叶子节点需带 match_criteria，用于后续筛选匹配的发现。match_criteria 应优先使用 relation/context/variables 字段；topic_tags 只能作为辅助。

研究问题：{question}

发现列表（含索引）：
{findings}

输出 JSON:
{{
  "title": "报告标题（中文）",
  "sections": [
    {{
      "heading": "一级议题",
      "subsections": [
        {{
          "heading": "子议题",
          "match_criteria": {{
            "relation": {{"subject": "可选", "predicate": "可选", "object": "可选", "direction": "可选"}},
            "context": {{"study_type": "可选", "sample_or_system": "可选", "condition": "可选", "method": "可选"}},
            "variables": ["变量名或变量角色"],
            "topic_tags": {{"维度名": "值或值列表"}}
          }}
        }}
      ]
    }}
  ]
}}

要求:
- 大纲层级不超过 3 层（section > subsection 即可）
- match_criteria 中的值如果是多个候选，用数组表示
- 优先围绕 direct findings 组织主章节；indirect/background 只能作为背景、边界条件或研究缺口，不要把它们提升为主结论
- 如果发现明显分为不同对象、样本、条件、方法、变量或关系类型，应在 outline 中体现
- **重要**: 严格按照研究问题指定的范围组织大纲。来自范围外对象、样本、条件或场景的发现不要创建独立主章节；若有对比价值，可归入边界条件或附属讨论
- 只输出 JSON"""

STEP3_MATCH_PROMPT = """从以下发现缩略列表中，选出与子议题最相关的发现（最多8条）。

子议题: {heading}
match_criteria: {match_criteria}

发现缩略：
{findings}

输出 JSON:
{{"matched_indices": [索引号, ...]}}

规则：根据发现的摘要、relevance_level、relation、context、variables 和 topic_tags 与子议题的语义匹配度选择。优先使用 relation/context/variables 判断；topic_tags 只能辅助。优先选择 direct；只有在没有 direct 时才选择 indirect/background。宽松匹配但只选真正相关的，最多输出8个索引。
只输出 JSON。"""

STEP3_WRITE_PROMPT = """你是一位严谨的综述作者。根据以下发现，撰写报告的一个章节。

章节主题：{heading}
研究问题：{question}

相关发现（`**[ref:N] 作者 et al., 年份**` 标注了编号和引用来源，引用时必须使用 [N] 且作者名与标注一致）：
{findings}

要求:
1. 用报告体撰写，语言为中文
2. 每条核心结论附原文引用（用引号标注 quote）
3. 引用格式: 在句末用 [N] 标注，作者名需与发现中标注的 cite_key 完全一致
4. 如果发现之间存在矛盾，客观陈述，不做强行调和
5. 该节控制在 300-800 字"""

STEP4_PROMPT = """合并以下报告各节为一份完整、风格统一的结构化 Markdown 报告。

要求:
- 保留所有内容和引用（[N] 格式的数字引用）
- 统一各级标题格式
- 修正表述不一致的地方
- 保留与研究问题直接相关的主证据；如果某些内容只是背景、间接证据、方法学说明或来自研究问题未指定的人群，不要写成主结论，可降级为“边界条件/背景/研究缺口”，必要时删除
- **重要**: 如果不同章节对同一对象、变量关系或结论方向存在矛盾发现，必须在新报告中明确指出矛盾所在，并分析可能的原因（如样本差异、条件差异、方法差异、测量口径差异等），不要简单罗列或忽视矛盾
- 如果某些章节涉及研究问题未指定的人群（如抑郁症），只能作为边界条件简短讨论，不能作为主结论；若与研究问题无关则直接删除

各节内容：
{sections}"""

STEP5_PROMPT = """将以下结构化报告改写为一篇流畅的学术综述文章。

要求:
- 用流畅的叙事语言，非条目式
- 保留所有核心结论和 [N] 格式的数字引用
- **严格保留文末的参考文献列表，一字不改**（包括作者名、标题、期刊、年份，不得改写、缩写或替换）
- 文中引用 [N] 对应的作者名必须与参考文献列表中的作者名完全一致，不得自行推断或编造
- 语言连贯自然，适合直接阅读
- 中文输出

报告：
{report}"""

VERIFY_CITATION_PROMPT = """你是学术审稿人。验证报告中每条引用的正确性。

报告文本：
{report}

发现索引（每条发现的正确引用信息）：
{findings_index}

检查每条 [N] 引用:
1. 作者名是否与发现中标注的 cite_key 一致
2. 数值/方向结论是否与发现的 claim_cn 一致
3. 是否存在无引用支持的断言
4. 是否在给出的全局参考文献列表中缺少该引用（仅当全局列表中确实没有时才报错）

输出 JSON 数组:
[{{"location": "段落开头文字...", "ref_num": N, "issue": "问题描述", "severity": "error/warning"}}]
只输出 JSON。"""

VERIFY_LOGIC_PROMPT = """你是学术审稿人。检查以下综述报告的逻辑一致性。

报告：
{report}

检查:
1. 不同章节对同一现象的描述是否存在矛盾
2. 结论是否跳跃、过度推断
3. 是否存在关键论点缺乏引用

输出 JSON 数组:
[{{"section": "章节名", "location": "相关段落开头...", "issue": "问题描述", "severity": "error/warning"}}]
只输出 JSON。"""

CLAIM_MAP_EXTRACT_PROMPT = """你是学术审稿人。请从综述报告中抽取核心论断，形成通用 claim map。

报告：
{report}

输出 JSON 数组。每个元素格式：
[
  {{
    "claim": "报告中的核心论断，保持原意",
    "scope": "该论断适用的对象、样本、条件、方法或场景",
    "evidence_refs": [1, 2],
    "certainty": "high/medium/low/unclear",
    "location": "该论断所在章节或段落开头"
  }}
]

要求：
- 只抽取对结论有实质作用的论断，不抽取纯背景或参考文献条目
- evidence_refs 只填写报告中明确出现的 [N] 数字引用；没有引用则为空数组
- scope 必须忠实于报告表述，不得自行扩大范围
- 保持领域通用，不使用任何特定学科规则
- 只输出 JSON。"""

CLAIM_MAP_CHECK_PROMPT = """你是学术审稿人。请检查以下 claim map 中的跨论断逻辑问题。

Claim map：
{claim_map}

检查关系类型：
- contradicts: 两条或多条论断互相矛盾但报告未解释
- overgeneralizes: 论断范围比证据或限定条件更宽
- unreferenced: 关键论断没有引用支持
- scope_mismatch: 论断之间比较了不同对象、样本、条件、方法或指标，却写成同一范围
- unsupported_jump: 从证据到结论存在明显跳跃

输出 JSON 数组。每个元素格式：
[
  {{
    "relationship": "contradicts/overgeneralizes/unreferenced/scope_mismatch/unsupported_jump",
    "claim_indices": [0, 2],
    "location": "相关章节或段落开头",
    "issue": "问题描述",
    "severity": "error/warning"
  }}
]

要求：
- 如果没有问题，输出 []
- 不要使用任何特定学科规则，只检查通用逻辑、范围和引用支持
- 只输出 JSON。"""

STEP6_FIX_PROMPT = """你是学术编辑。根据验证反馈修正以下综述报告中的问题。

报告：
{report}

验证反馈（逐条列出的需要修正的问题）：
{verification_feedback}

原始发现索引（每条发现的正确引用信息，供核对）：
{findings_index}

要求:
1. 修正所有标记为 error 的问题（事实错误、引用错误、内容截断）
2. 修正所有标记为 warning 的问题（逻辑不一致、表述不清、引用缺失）
3. 对于相互矛盾的发现，补充分析说明，指出可能的原因（如样本差异、条件差异、方法差异、测量口径差异等）
4. 删除与核心研究人群无关的章节（如抑郁症等，除非它被研究问题明确指定）
5. 保留所有正确的引用和参考文献列表
6. 保持报告体格式和中文输出
7. 输出完整的修正后报告（包含标题、各节正文、参考文献列表）

**严格禁止：**
- 不得在参考文献条目后添加括号注释、补充说明、或"假设为""可能为"等不确定表述
- 不得编造、改写或替换参考文献的作者名、标题、期刊、年份
- 如果无法确认某条引用信息，保留原样不要修改
- 输出必须是纯净的 Markdown 报告，不得包含任何元文本或自我对话"""

STEP7_DIAGRAM_PROMPT = """根据以下综述报告，用 Mermaid flowchart 生成一张总结性示意图。从报告中自动提取：有哪些分组维度（如样本、系统、条件、方法、变量或指标等），各组内有哪些关键节点，节点之间的关系（如增加、降低、正相关、负相关、无关联或矛盾）。

报告：
{report}

生成规则：
1. 顶层按报告的自然分组（如样本、系统、条件、方法或证据类型等）划分子图
2. 每个子图内列出该组的关键发现节点，简洁中文，每条不超过10字
3. 节点关系用文字标注（如"增加""降低""正相关""负相关""无关联""矛盾"），不要用 [+][-] 等符号
4. 不同方向用 classDef 着色：green（增加/正相关）、red（降低/负相关）、orange（混合/矛盾/不确定）
5. 节点定义只能用 A[标签] 格式，方括号内禁止再嵌套方括号
6. 只输出 ```mermaid 代码块，无其他文字

**Mermaid class 语法（严格遵守）：**
正确：class A,B,C green
错误：class A,B,C,green（类名不能加逗号前缀）
错误：class A,B,C green,red（一行只能一个类名）

输出格式：
```mermaid
flowchart TD
  ...
```"""

STEP7_TABLE_VIEW_PROMPT = """请根据以下综述报告，提出 2-3 个可用于总结直接证据的 Markdown 表格视图。

报告：
{report}

输出 JSON 数组：
[
  {{
    "title": "表格标题",
    "row_dimension": "行维度，如对象/变量/条件/方法/结果类别",
    "column_dimension": "列维度，如样本/系统/场景/证据类型/比较组",
    "cell_schema": "每格内容格式，例如：主要发现；证据强度；引用",
    "coverage_rationale": "为什么这个视图能覆盖直接证据",
    "estimated_direct_evidence_coverage": 0.0
  }}
]

要求：
- 保持领域通用，不预设特定学科的行列维度
- 优先覆盖报告中的直接证据和核心引用
- 不强制使用方向箭头；只有主题明确涉及方向性关系时才建议使用
- estimated_direct_evidence_coverage 用 0 到 1 的数字估计
- 只输出 JSON。"""

STEP7_TABLE_PROMPT = """请根据指定表格视图，从综述报告中生成一张总结性 Markdown 表格。

报告：
{report}

表格视图：
{table_view}

要求：
1. 表格标题使用视图中的 title
2. 行维度使用 row_dimension，列维度使用 column_dimension
3. 每格遵循 cell_schema，必须包含引用编号（如 [1]）或明确写“无直接证据”
4. 不强制方向箭头；只有报告主题和证据明确涉及方向性关系时才使用 ↑/↓/~/? 等符号
5. 表格用标准 Markdown 格式，第一行为列标题
6. 表后附2-3句说明，标注重要矛盾、边界条件或证据空白
7. 只输出表格和说明，不要其他文字"""
