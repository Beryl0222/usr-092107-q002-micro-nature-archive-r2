# 微观自然影像档案

为美术馆 300 余幅岭南微距作品提供**长期影像档案服务**：同一份事件流既支撑
公众展览，也作为可信的自然观察档案。摄影师后期、物种鉴定、栖息地位置、
作者授权各自独立演进，由仓库已有的事件身份约定（`event_id` /
`aggregate_id` / `version`）维持关系。

## 核心原则

- **只追加、不改写**：记录一经接收，标识、发生时间与版本不得原地修改；
  改名、撤权、更正署名一律追加新的后继事件，历史完整保留。
- **一次野外观察 = 一个事实**：观察登记保存观察时间、模糊位置、环境条件、
  原始文件 sha256 校验值与拍摄参数；重复投稿（同一 `submission_id`）
  指向同一次观察，不会形成两条记录。
- **派生版本是独立事件**：裁切、调色、放大等以 `ASSET_DERIVED` 记录
  可复现变换参数与新校验值，形成 母版 → 派生 → 再派生 的谱系链。
- **鉴定与授权各自演进**：专家鉴定（`IDENTIFICATION_REVIEWED`）与许可
  （`USE_LICENSED` / `USE_LICENSE_WITHDRAWN`）是独立聚合流。
- **位置分级开放**：精确栖息地仅对具备资质的研究者开放；濒危/易受干扰
  物种另需专项授权。公众与策展侧始终得到模糊位置。
- **出版时点冻结**：`PUBLICATION_FROZEN` 快照保存图录当时依据的名称、
  署名、资产校验值与许可状态；公众名称更新后，已出版图录仍显示当时名称。
- **撤权不毁档案**：撤回商业/展示许可后，依法保存的 `research_citation`
  科研引用继续有效。
- **错误署名可更正、去向可追查**：`CREDIT_CORRECTED` 留痕；摄影师可查询
  名下作品的每一次展览、出版、商业与科研使用。

## 资料结构

| 路径 | 说明 |
| --- | --- |
| `contracts/domain.schema.json` | 事件信封、8 类事件与各类 payload 的 JSON Schema（2020-12） |
| `src/identity.py` | 观察、母版、派生资产、评审、许可、出版的 ID 约定 |
| `src/validator.py` | 信封与按事件类型的载荷校验（纯标准库） |
| `src/errors.py` | 领域错误（重复投稿、版本冲突、许可状态等） |
| `src/store.py` | 只追加事件存储（内存 + JSONL，幂等、版本递增、投稿去重） |
| `src/projections.py` | 从事件流重建的读模型：观察、资产谱系、许可、出版、使用记录 |
| `src/policy.py` | 调用方资质、位置开放判定与脱敏 |
| `src/services.py` | 应用服务：登记、派生、鉴定、授权/撤回、冻结、策展说明、使用闸门、署名更正与追查 |
| `src/exporting.py` | 确定性、脱敏、带指纹的研究导出与复核 |
| `src/demo.py` | 岭南场景端到端演示（5 次观察，覆盖全部验收点） |
| `src/cli.py` | 命令行入口 |
| `data/sample.json` | 一条符合完整契约的中文样例 |
| `data/demo/` | 演示产物（事件流、展项说明、图录页、三种研究导出、摄影师追查、验收摘要） |
| `tests/` | 契约测试与场景验收测试（21 项） |

## 事件一览

| 事件 | 聚合 | 作用 |
| --- | --- | --- |
| `OBSERVATION_REGISTERED` | `field_observation` | 登记观察：时间、位置、环境、母版校验值、拍摄参数、投稿去重键 |
| `ASSET_DERIVED` | `media_asset` | 裁切/调色/放大/修图等派生版本及可复现参数 |
| `IDENTIFICATION_REVIEWED` | `taxon_review` (`taxon:<obs>`) | 鉴定演进，新评审取代旧评审，旧名保留 |
| `USE_LICENSED` | `exhibition_use` (`usage:<asset>`) | 作者授权（展览/图录/科研/商业/教育/网络） |
| `USE_LICENSE_WITHDRAWN` | 同上 | 撤权，默认保留 `research_citation` |
| `CREDIT_CORRECTED` | `media_asset` | 更正错误署名，强制留痕（原因必填） |
| `PUBLICATION_FROZEN` | `exhibition_use` (`pub:<id>`) | 出版时点依据冻结，不可二次写入 |
| `USE_LOGGED` | `exhibition_use` | 实际使用留痕；展示/商业用途先过许可闸门 |

## 使用方式

```bash
# 运行全部测试
python3 -m unittest discover -s tests

# 构建端到端演示并写出 data/demo/ 全部产物
python3 -m src.cli demo

# 为展项输出来源与许可说明（不合规时退出码 2）
python3 -m src.cli label data/demo/events.jsonl \
  deriv-obs-lingnan-2026-0001-color_grade-0001 \
  --at 2026-10-06T10:00:00+08:00 --role public --exhibit EX-2026-018

# 脱敏研究导出（--sensitive 表示持有敏感物种精确位置专项授权）
python3 -m src.cli export data/demo/events.jsonl out/ \
  --role qualified_researcher --caller r-zhaomin --sensitive

# 复算导出指纹，验证可复现（退出码 0/3）
python3 -m src.cli verify out/research_export.json

# 摄影师追查使用去向与署名更正
python3 -m src.cli trace data/demo/events.jsonl p-heqi
```

## 可验收结论（演示场景）

`data/demo/acceptance_summary.json` 由 `python3 -m src.cli demo` 生成：

1. **策展人**：`exhibit_label()` 为任一展项给出名称（当前鉴定）、署名、
   母版校验值、完整派生谱系与许可状态；撤权展项 `compliant=false` 且
   布展使用被闸门拒绝。
2. **图录稳定性**：大巨腿螳 10-01 以 *Hestiasula sp.* 冻结付印，
   10-04 专家定种为 *Hestiasula major* 后，`catalog_page()` 仍显示旧名，
   展项标签显示新名。
3. **位置保护**：金斑喙凤蝶（国家一级保护）精确坐标对公众、策展人、
   普通研究者均模糊，仅持专项授权的研究者可见。
4. **撤权边界**：龙眼鸡商业与展示许可撤回后，商业使用被拦截，
   科研引用照常留痕。
5. **重复投稿**：同一投稿键再次提交（即使换新观察编号）返回
   `obs-lingnan-2026-0001`，观察总数不增加。
6. **署名更正**：阳彩臂金龟由误署"林伟"更正为"何琦"；何琦可追查更正前
   展览去向，林伟的追查中单列 `works_corrected_away`。
7. **研究导出**：确定性 JSON（键序固定）+ 事件流 sha256 + 导出包
   sha256；从 JSONL 重建存储后重导字节一致，`verify` 复核通过。

## 设计说明

- 投影可随时从事件流丢弃重建，因此不持有独立事实；当前状态是事件序列的
  折叠结果。
- 许可时点语义：`has_scope(asset, scope, at=...)` 同时考虑撤回、到期与
  依法保留的科研 scope。
- 演示中的文件校验值为对逻辑标签计算的确定性 sha256；接入真实文件时使用
  `src.services.sha256_file()` 计算母版与派生文件。
