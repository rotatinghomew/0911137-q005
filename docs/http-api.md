# 本地接口

- `GET /api/catalog`：资料名称、可选业务范围、业务时点范围、表计数与资料指纹。
- `GET /api/view`：当前摘要与风险估计，可选 `cutoff` 整数、`group` 业务范围与 `horizon_hours` 正整数预测窗口（运行小时，默认 720）；未知筛选项返回 400。返回的 `result.risk` 结构见 `risk-methodology.md`，包含寿命片段、风险集表、分站点故障率、维修后零事件上界、跨站点等率检验、设备级状态与备件情景。
- `GET /api/machine`：单台设备页数据，参数 `machine` 必填，可带 `cutoff`、`group`、`horizon_hours`；未知编号返回 404，未知筛选项返回 400。
- `GET /api/source?table=machines`：按实际资料表名称查询原始记录；不存在的表返回 404。
- `POST /api/analyses`：JSON 对象包含 `title` 与 `options`，重新计算并保存，成功返回 201。`options` 与当前摘要的筛选结构相同（可含 `horizon_hours`），风险结果随版本一起冻结。
- `GET /api/analyses`：只返回版本目录及资料指纹。
- `GET /api/analyses/{id}`：返回保存的完整结果，不重新计算。

API 的 `rows` 是领域行，`summary` 是当前汇总，`notes` 是业务口径提示，`options` 是实际采用的筛选值。无记录使用空列表，不能把无观测偷偷表示为有效的零值。设备级风险用显式状态（`beyond_observed_age`、`insufficient_post_repair_evidence` 等）表达不可识别，对应概率字段为 `null` 而不是外推精确值。错误 JSON 的 `error` 可直接在页面反馈。

这是本地单用户应用，不包含认证与外网部署设置。默认只监听回环地址，容器中可显式监听所有接口，但宿主机端口建议只绑定回环地址。
