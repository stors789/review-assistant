# Zotero Web API 自动入库与本地同步

## Overview

将 `auto_lit.py` 的 RIS/GUI 导入路径升级为 Zotero Web API 写入路径：检索文献后直接创建 Zotero 条目、加入用户指定的 collection、写入 tags，然后等待 Zotero Desktop 自动同步到本地 SQLite。目标是去掉 `open -a Zotero` 和导入弹窗，同时保持后续 `ZoteroReader` 本地读取、PDF 覆盖检查、pipeline 重跑的工作流不变。

RIS 导出保留为 fallback，不作为默认高级路径的依赖。

## Goals

- 支持个人库和群组库：`library_type=user|group` + `library_id`。
- 用户通过 `-c / --collection "父集 > 子集"` 指定目标 collection。
- collection 不存在时可自动逐级创建。
- 创建条目时直接加入目标 collection 并写入 tags。
- 写入成功后可轮询本地 Zotero SQLite，等待 Desktop 同步完成。
- DOI 去重同时检查本地库和 Web API，减少重复条目。

## Non-goals

- 不自动下载 PDF。PDF 下载仍交给 Zotero Desktop、插件或用户手动处理。
- 不直接写本地 Zotero SQLite。
- 不替代 `ZoteroReader` 的本地读取能力。
- 不实现 OAuth；使用用户手动创建的 Zotero API key。

## Configuration

新增环境变量：

| 变量 | 说明 |
|---|---|
| `ZOTERO_API_KEY` | Zotero Web API key，需有 library write 权限 |
| `ZOTERO_LIBRARY_TYPE` | `user` 或 `group` |
| `ZOTERO_LIBRARY_ID` | Zotero userID 或 groupID |
| `ZOTERO_WEB_IMPORT` | 设为 true 时默认使用 Web API 入库 |
| `ZOTERO_SYNC_TIMEOUT` | 等待本地同步的秒数，默认 120 |

新增 CLI 参数：

| 参数 | 说明 |
|---|---|
| `--web-import` | 使用 Zotero Web API 写入 |
| `--zotero-api-key` | 覆盖 `ZOTERO_API_KEY` |
| `--zotero-library-type user|group` | 覆盖 `ZOTERO_LIBRARY_TYPE` |
| `--zotero-library-id` | 覆盖 `ZOTERO_LIBRARY_ID` |
| `--create-collection` | 允许自动创建缺失 collection，默认开启 |
| `--no-create-collection` | collection 不存在时直接失败 |
| `--collection-key` | 高级选项：直接指定 Zotero collection key，跳过路径解析 |
| `--wait-local-sync` | 写入云端后等待本地 SQLite 出现 DOI |
| `--sync-timeout` | 覆盖 `ZOTERO_SYNC_TIMEOUT` |

保留现有 `-c / --collection` 语义，作为 collection path：

```bash
review-assistant-autolit \
  "hybrid search reranking RAG" \
  --web-import \
  -c "信息检索 > RAG > hybrid search" \
  -t rag-hybrid-evaluation
```

## Components

### 1. `zotero_web.py`

新增 Web API 客户端模块：

```python
class ZoteroWebClient:
    def __init__(self, api_key: str, library_type: str, library_id: str):
        ...

    def list_collections(self) -> list[dict]:
        ...

    def ensure_collection_path(self, path: str, create: bool = True) -> str:
        ...

    def find_existing_dois(self, dois: set[str]) -> set[str]:
        ...

    def create_items(self, papers: list[dict], collection_key: str, tags: list[str]) -> dict:
        ...
```

API base:

```text
https://api.zotero.org/users/<library_id>
https://api.zotero.org/groups/<library_id>
```

所有写请求使用 header：

```text
Zotero-API-Key: <key>
Content-Type: application/json
Zotero-Write-Token: <uuid>
```

`Zotero-Write-Token` 用于降低重试导致重复写入的风险。

### 2. Collection Path 解析与创建

输入：

```text
电波 > theta > metabolic coupling
```

流程：

1. `GET /collections` 拉取 collection 列表。
2. 根据每个 collection 的 `key`, `data.name`, `data.parentCollection` 构建树。
3. 从根到叶逐级匹配完整路径。
4. 若某级不存在且允许创建，则 `POST /collections` 创建：

```json
[
  {
    "name": "metabolic coupling",
    "parentCollection": "<theta_collection_key>"
  }
]
```

5. 返回最终叶子 collection key。

日志示例：

```text
📁 目标 collection: 电波 > theta > metabolic coupling
  ✓ 已找到: 电波
  ✓ 已找到: theta
  + 已创建: metabolic coupling
```

如果传入 `--collection-key`，则跳过路径解析和创建，直接使用该 key。

### 3. Item 映射

Semantic Scholar / PubMed paper 映射为 Zotero `journalArticle`：

```json
{
  "itemType": "journalArticle",
  "title": "...",
  "creators": [
    {"creatorType": "author", "firstName": "...", "lastName": "..."}
  ],
  "date": "2024",
  "publicationTitle": "...",
  "DOI": "...",
  "url": "https://doi.org/...",
  "abstractNote": "...",
  "tags": [{"tag": "theta-metabolic-coupling"}],
  "collections": ["<target_collection_key>"]
}
```

作者名解析不可靠时退化为：

```json
{"creatorType": "author", "name": "Full Name"}
```

### 4. 去重

去重顺序：

1. 本地 SQLite：复用 `_get_existing_dois()`。
2. Web API：按 DOI 查询已存在条目。先实现简单逐 DOI 查询；后续如遇性能瓶颈再批量优化。
3. 本轮候选列表内部去重。

已存在 DOI 直接跳过并打印标题。

### 5. 批量写入与错误处理

Zotero Web API 写入按批次提交，每批最多 50 条。

响应分类：

- `successful`: 打印创建成功条目数量和 key。
- `unchanged`: 视为成功但已存在。
- `failed`: 打印每条错误，继续处理其他批次。

错误处理：

- `401/403`: API key 缺失、权限不足或库 ID 错误，直接失败。
- `404`: library 或 collection 不存在；若 collection path 模式且允许创建，重建 collection 后重试一次。
- `409/412`: 版本冲突，重新拉取 collection/item 信息后重试一次。
- `429`: 按 `Retry-After` 等待后重试。
- 网络错误：指数退避，最多 3 次。

### 6. 等待本地同步

写入 Web API 成功后，如果启用 `--wait-local-sync`：

1. 每 5 秒用 `ZoteroReader` 查询本地 DOI。
2. 找到所有新 DOI 或超时即停止。
3. 成功时提示本地已同步；超时时提示云端已写入但本地尚未同步。

超时不视为写入失败，因为 Zotero Desktop 可能未打开、未登录、未开启自动同步或网络较慢。

## `auto_lit.py` Flow

```text
搜索候选文献
  -> 规则筛选 / 引用过滤
  -> 本地 DOI 去重
  -> 如果 --web-import:
       Web DOI 去重
       ensure_collection_path()
       create_items()
       wait_for_local_sync()
     否则:
       生成 RIS
       可选 --import-zotero 触发旧 GUI 导入
```

## Compatibility

- 默认行为保持现状：未传 `--web-import` 时仍生成 RIS。
- `--import-zotero` 保留，但文档标记为 legacy fallback。
- 旧的 `-c / --collection` 继续可用；在 RIS 模式仅作为提示，在 Web 模式作为真实目标 collection path。

## Testing

新增测试：

- collection tree path resolve：已有、部分缺失、完全缺失。
- `--no-create-collection` 下缺失路径报错。
- item JSON 映射：DOI、title、authors、journal、tags、collections。
- 批量写入分批：超过 50 条拆批。
- 429 Retry-After 重试。
- 本地同步等待：mock `ZoteroReader`，验证成功和超时两种路径。
- `auto_lit.py` Web 模式不会生成 RIS/不会调用 `open -a Zotero`。

## Open Questions

- 默认是否开启 `--wait-local-sync`：建议默认开启，超时不失败。
- collection 自动创建是否默认开启：建议默认开启，同时提供 `--no-create-collection`。
- Web API 去重是否需要先做批量 search：第一版可逐 DOI 查询，避免复杂查询语法问题。

## References

- Zotero Web API v3 Basics: https://www.zotero.org/support/dev/web_api/v3/basics
- Zotero Web API v3 Write Requests: https://www.zotero.org/support/dev/web_api/v3/write_requests
