# Feature 22 Production Readiness Blueprint

This document turns Feature 22 into an executable design for a production-capable
Platform v2, while keeping Feature 21 compatibility as the canonical default.

## 1. 目标与边界

- 保留现有 `Feature 21` 的接口和运行语义。  
- 先做“可替换基础设施”而不是“重写引擎”。  
- 先支持生产部署需要的最小闭环：  
  - 持久化后端替换  
  - 分布式队列替换  
  - 对象存储替换  
  - 访问鉴权与权限  
  - 可观测、部署与回滚流程  

## 2. 设计原则

- 接口优先：`storage / queue / artifact / auth` 都通过明确接口解耦。  
- 幂等与可恢复：任务、任务记录、事件必须可重放且不会因重试放大副作用。  
- 保留演进兼容：保持 `docs/schema.sql` 与 `FileStore` v1 命名空间可读。  
- 明确运行档位：`local`、`staging`、`production` 档位。  

## 3. 核心接口草案

### 3.1 `StorageBackend`（存储层）

```python
class StorageBackend(Protocol):
    def save_spider(self, spider): ...
    def load_spider(self, spider_id): ...
    def list_spiders(self, limit: int = 200, cursor: str | None = None): ...

    def save_task(self, task): ...
    def load_task(self, task_id): ...
    def list_tasks(self, status: str | None = None, limit: int = 200): ...
    def append_task_event(self, task_id: str, event: dict): ...

    def claim_jobs(self, worker_id: str, limit: int = 1, lease_seconds: int = 600): ...
    def heartbeat_job(self, job_id: str, worker_id: str): ...
    def complete_job(self, job_id: str, payload: dict): ...

    def write_observability(self, kind: str, payload: dict): ...
    def read_observability(self, kind: str, target_id: str, limit: int = 200): ...

    def begin(self): ...
    def commit(self): ...
    def rollback(self): ...
```

- **默认实现**：当前 `FileStore` 改造为 `FileStoreBackend`。  
- **生产实现（首选）**：`PostgresStorageBackend`。  

### 3.2 `QueueBackend`（调度与执行队列）

```python
class QueueBackend(Protocol):
    def enqueue(self, topic: str, item: dict, dedupe_key: str | None = None): ...
    def claim(self, worker_id: str, topics: list[str] | None = None, batch_size: int = 1): ...
    def nack(self, job_id: str, reason: str, retry_at: str | None = None): ...
    def ack(self, job_id: str): ...
    def heartbeat(self, job_id: str, worker_id: str): ...
    def metrics(self): ...
```

- **默认实现**：`InProcessQueue`（沿用现有文件语义）。  
- **生产实现**：`RedisQueue`。  

### 3.3 `ArtifactStorage`（大文件与导出产物）

- `LocalArtifactStorage`：保留现有目录与 manifest。  
- `ObjectStorageBackend`：支持 S3 / MinIO 兼容对象存储。  
- manifest 结构保持固定：`artifact_id / logical_path / checksum / content_type / size`。  

### 3.4 `AuthBackend`（访问控制）

- 第一阶段：API Key 认证 + 最小 RBAC。  
- 角色：`admin`、`operator`、`viewer`。  
- 将鉴权拦截放在 API 路由层之前；CLI 可继续使用本地 `--api-key` 或环境变量。  

## 4. 兼容性与版本策略

1. 继续保留 v1 HTTP 响应风格 `{ok, data, error, meta}`。  
2. 新增 v2 配置与 profile 时默认读取 v1 字段。  
3. 提供 `--compat v1` 开关，逐步迁移时保持默认 `v1`。  
4. `Feature 21` 的命令与数据结构保持可复用，新增 backend 可按 profile 替换。  

## 5. 迁移路径（v1 -> v2）

1. `cp -r data/...` 导出快照并校验。  
2. `store export --format json` 生成中间迁移清单。  
3. `store import --source v1 --target postgres`。  
4. `migrate --dry-run --compare`：记录数、hash、checksum 对齐检查。  
5. 切换 profile 到生产后，保留 v1 回读窗口（至少一个 release）。  

## 6. 交付分期

- **阶段一（2-3 周）：基础设施层**  
  - 引入 StorageBackend + QueueBackend 接口及构建时校验。  
  - `FileStoreBackend` / `InProcessQueue` 兼容旧语义。  
  - 完成 `PostgresStorageBackend` 与 `RedisQueue` 的冒烟。  
- **阶段二（1-2 周）：安全层**  
  - API Key + RBAC；审计事件落库。  
- **阶段三（1 周）：部署工程化**  
  - `docker-compose`, `systemd`, health-check, graceful shutdown。  
- **阶段四（滚动）**  
  - 对象存储后端、异步导出管道、Playwright 扩容策略。  

## 7. 验收标准

- 关键矩阵（v2）通过：启动/运行/任务恢复/导出/回溯对齐。  
- 同一配置在 FileStore 与 Postgres 上运行结果一致（record-id 顺序可不同，schema 一致）。  
- 队列重试与 lease 恢复可观测且不会丢作业。  
- 生产档位默认开启 API Key 校验；`viewer` 无法触发执行类动作。  
- 回滚演练：切回 v1 profile 不依赖新数据库也可恢复读取。  
