# 设备使用寿命台

用于查阅设备使用寿命的业务记录、当前摘要与保存的分析版本。现有功能包括业务范围筛选、历史时点查阅、原始资料展开、不可变分析结果回看，以及竞争风险下的故障风险估计与备件需求范围。统计口径见 `docs/data-contract.md`，风险方法学见 `docs/risk-methodology.md`，HTTP 结构见 `docs/http-api.md`。

## 风险与备件

风险分析把运行区间与维修记录重组成可验证的寿命片段与风险集：故障与报废按竞争结局处理，计划检修不算故障；大修按现有维修等级解释为年龄重置、小修不重置，维修后零故障的片段只给零事件上界而不外推精确风险。跨站点借用先通过条件二项等率检验，当前资料不支持零故障站点借用站点1 的故障率。每台设备返回显式状态（观测年龄内可估计、超龄无点估计、维修后仅上界、已报废），备件需求同时给严格证据口径、恒定率模型区间与保守上界，并贯通到设备页。

## 运行

需要 Python >=3.11,<3.12，不依赖特定补丁版本。应用运行只使用标准库，浏览器 JavaScript 无构建步骤。以下命令在项目根目录执行：

```sh
python3 -m fleet_reliability
```

浏览器打开本机 8080 端口即可操作。资料默认来自 `fixtures/data.json`；分析结果默认保存在 `.state/analyses.sqlite3`。可用 `--data` 指定同口径资料，`--state` 指定状态文件，`--port` 调整监听端口。

## 测试

```sh
python3 -m unittest discover -s tests -v
```

测试涵盖领域口径、真实 HTTP 往返、错误筛选、跨进程存储及不可变保存。页面直接消费与测试相同的 API，不使用预录结果。

浏览器验证工具与产品运行依赖分开锁定；准备一次后可重复执行筛选、保存、刷新回看和资料查阅：

```sh
python3 -m pip install -r requirements-test.txt
python3 -m playwright install --with-deps chromium
python3 tests/browser_smoke.py
```

前两条是首次测试环境安装，完成后无需重复执行。基础容器只运行产品和标准库测试；浏览器测试在安装了 `requirements-test.txt` 与 Chromium 的开发环境运行。

也可使用无第三方 JavaScript 依赖的 npm 工作区入口：`npm ci --ignore-scripts` 后，`npm start -- --host 127.0.0.1 --port 8080` 启动同一个 Python HTTP 服务，`npm run test:browser` 执行上述浏览器回归。npm 仅负责命令编排，不是产品运行依赖；不要把 `web/` 单独作为静态站点运行，页面需要同源 API。

## 容器

```sh
docker build -t fleet-reliability:local .
docker run --rm -p 127.0.0.1:8080:8080 fleet-reliability:local
```

基础镜像以内容摘要固定，Python 打包依赖固定在 `requirements-build.txt`。容器构建完成后可断网运行；若要保留容器外分析版本，可将宿主目录挂载到 `/app/.state`。

## 快速业务核对

```sh
python3 -m fleet_reliability --demo
```

此入口启动真实本地 HTTP 服务，计算当前摘要，保存后重新读取再退出。输出依赖当前资料，不预先指定统计结论。初次阅读可先在页面调整时点或业务范围，再保存和回看分析；测试和该核对入口可在九十秒内完成，不包含镜像构建时间。
