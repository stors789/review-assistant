# TODO: review-assistant 可迁移性与健壮性优化 (已完成)

本代办清单基于 2026-06-15 对该项目的「可迁移性审查报告」建立，目前已全部开发并测试通过。

## 🎯 核心已完成任务

### 1. 支持 Zotero 自定义数据目录
* [x] **ZoteroReader 参数集成**：修改 `scripts/zotero_reader.py` 中的 `ZoteroReader` 构造函数，让其更加明确地支持在各个位置被传入 `zotero_dir` 参数。
* [x] **CLI 命令行参数暴露**：在以下主要 CLI 脚本中添加 `--zotero-dir` 命令行参数，并透传给 `ZoteroReader`：
  - [x] `scripts/zotero_read.py`
  - [x] `scripts/paper_breakdown.py`
  - [x] `scripts/claim_verify.py`
  - [x] `scripts/explore_synthesize.py`
* [x] **全局环境变量支持**：允许从环境变量 `ZOTERO_DIR` 读取自定义的数据根目录作为次优先级，减少每次命令行输入的烦恼。

### 2. 兼容 Zotero 链接文件 (Linked Files) 附件类型
* [x] **支持绝对路径解析**：更新 `scripts/zotero_reader.py` 的 PDF 匹配逻辑。若数据库中 `itemAttachments.path` 存储的是系统绝对路径（非 `storage:` 前缀），应直接识别并校验文件存在性，而不是跳过。
* [x] **支持「链接附件根目录」相对路径解析**：允许在 Zotero 使用了“链接附件根目录”相对链接附件时，通过读取 `ZOTERO_LINKED_BASE_DIR` 环境变量来自动还原并拼接 `attachments:` 相对路径附件。

### 3. 多 LLM 厂商与接口兼容性优化
* [x] **自适应请求参数与重试**：解决了在 `llm_client.py` 中强行硬编码推理参数导致非 DeepSeek 服务商（如标准 OpenAI gpt-4o、Anthropic、本地 Ollama）报错 400 的问题。目前能自适应识别模型名（OpenAI `o1`/`o3` 分支或 DeepSeek 分支），且如果调用因非法参数导致 400 失败，会立即自动剥离 `thinking` 和 `reasoning_effort` 并恢复标准配置参数发起原地重试。

### 4. 完善文献检索 (`auto_lit.py`) 密钥手动配置
* [x] **暴露检索 API 密钥**：在 `auto_lit.py` 中增加了 `--ss-api-key`、`--pubmed-api-key` 以及 `--zotero-dir` 命令行选项，使用户无需在环境中强行 `export` 即可配置与检索。

### 5. 增强外部工具依赖校验与容错
* [x] **OCR/扫描件 PDF 提醒**：在公共 PDF 解析器 `extract_pdf_text`（[scripts/utils.py](file:///Users/eros/.agents/skills/review-assistant/scripts/utils.py)）中增加文本检测，当提取字符数为 0 时，打印明显的警告，提示可能是扫描版或已加密，建议先使用 OCR 或解锁。
* [x] **Mermaid CLI (mmdc) 说明**：审查确定项目内并没有直接的 `subprocess` 渲染 Mermaid 行为，图表渲染在 `SKILL.md` 中以文档命令形式交由用户自愿本地离线渲染。

### 6. 项目打包与分发优化 (Packaging)
* [x] **构建本地包安装配置**：在根目录下提供了 [pyproject.toml](file:///Users/eros/.agents/skills/review-assistant/pyproject.toml)，项目支持在本地任意路径通过 `pip install -e .` 进行安装。已彻底支持任何目录下的命令直达调用。
* [x] **Windows 环境变量指南**：在 `SKILL.md` 中为 Windows 用户补充在 Command Prompt 和 PowerShell 下设置 `DEEPSEEK_API_KEY` 的具体命令（例如 `set` / `$env:`），代替单一的 `source ~/Documents/api.env` 说明。

## 🔎 2026-06-15 追加审查：仍需处理的迁移性风险 (已完成)

### 7. Python 入口版本不一致
* [x] **文档命令仍大量使用 `python`**：`pyproject.toml` 要求 Python `>=3.10`，已将 README/SKILL 中的命令示例统一为 `python` 和 `review-assistant-*` CLI 命令。
* [x] **增加启动时版本检查**：在所有五个公共 CLI 入口脚本开头增加了 `sys.version_info >= (3, 10)` 校验，低于该版本时直接输出清晰错误并退出。

### 8. LLM Provider 兼容性尚未贯通
* [x] **`paper_breakdown.py` 未复用 `llm_client`**：重构了 `paper_breakdown.py` 来初始化并复用 `llm_client` 线程安全池，使用其内置的 `call_json` 方法，获得了更好的降级逻辑与非 DeepSeek provider 兼容性。
* [x] **代理清理不完整**：在 `llm_client.py` 中补充了对 `http_proxy`, `HTTP_PROXY`, `https_proxy`, `HTTPS_PROXY` 的清理，解决企业内网代理/本机代理导致 client 请求行为不一致的问题。

### 9. 平台与文件系统假设
* [x] **`auto_lit.py` 默认 macOS 自动打开 Zotero**：改为由 `--import-zotero` 显式开启自动导入逻辑，默认情况下仅导出 RIS 报告并输出导入提示，提高在 CI、服务器及无 GUI 环境下的稳定性。
* [x] **Semantic Scholar 锁文件固定写入 `Path.home()`**：重构锁文件路径获取逻辑，优先检查 `AUTO_LIT_LOCK_DIR` 环境变量，然后尝试 `Path.home()`（校验写权限），最后安全回退到系统临时文件目录。
* [x] **Zotero linked-file 路径缺少跨平台测试**：在 `zotero_reader.py` 中引入了反斜线到斜线的路径分隔符归一化，并在 `test_zotero_reader.py` 中新增了 `test_resolve_pdf_path_cross_platform` 测试，全面覆盖 Unix 绝对路径、Windows 绝对路径（通过 mock 测试）、以及 `attachments:` + `ZOTERO_LINKED_BASE_DIR` 跨平台解析。

### 10. 文档可移植性
* [x] **README/SKILL 仍绑定 Unix shell 习惯**：在 README 和 SKILL 中添加了 Windows cmd/PowerShell 环境变量配置指南、控制 console scripts 运行说明，以及 Zotero 环境变量配置说明。
* [x] **测试运行说明缺失**：已在 README 中明确记录了单元测试的运行命令：`python -m unittest discover -s tests -v`，测试用例均通过。

## 🛠 建议修复顺序

1. **先处理入口和文档**：统一 README/SKILL 中的运行方式，明确需要 Python 3.10+；推荐 `python -m pip install -e .` 后使用 `review-assistant-*` console scripts，减少路径和解释器差异。
2. **补公共兼容层**：让 `paper_breakdown.py` 复用 `llm_client.call_json`，并把 `HTTP_PROXY` / `HTTPS_PROXY` 清理或代理开关集中放进 `llm_client.init_client_pool`。
3. **降低平台副作用**：把 `auto_lit.py` 的 Zotero 自动导入改为显式参数；锁文件目录支持环境变量或临时目录回退。
4. **补迁移性测试**：增加 Python 版本入口测试、linked-file 路径测试、`auto_lit` 不自动打开 Zotero 的测试、以及 provider 参数降级测试。
5. **最后更新验证说明**：文档中记录推荐测试命令：`python -m unittest discover -s tests -v`；如果以后使用 pytest，再将 pytest 放入开发依赖。
