# 记录样式

以下为当前业务文件中的实际记录，字段口径见 `data-contract.md`；字段标识保持业务接口写法。

### `machines`

字段：`batch`、`installed_hour`、`machine`、`site`。

```json
{
  "machine": "m000",
  "site": "站点1",
  "batch": "b0",
  "installed_hour": 0
}
```

### `intervals`

字段：`end_hour`、`known_hour`、`load`、`machine`、`start_hour`。

```json
{
  "machine": "m000",
  "start_hour": 0,
  "end_hour": 500,
  "load": 1.05,
  "known_hour": 0
}
```

### `events`

字段：`event`、`hour`、`kind`、`known_hour`、`machine`。

```json
{
  "event": "m000-f",
  "machine": "m000",
  "hour": 1000,
  "known_hour": 1010,
  "kind": "failure"
}
```

### `repairs`

字段：`grade`、`hour`、`known_hour`、`machine`。

```json
{
  "machine": "m000",
  "hour": 1100,
  "known_hour": 1100,
  "grade": "overhaul"
}
```
