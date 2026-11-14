const statusNode = document.querySelector('#status');
const filters = document.querySelector('#filters');
let current = null;
let requestGeneration = 0;

const STATUS_TEXT = {
  active: '在用',
  retired: '已报废',
  beyond_observed_age: '超龄·严格口径无点估计',
  insufficient_post_repair_evidence: '维修后·仅上界',
  estimated_within_observed_age: '观测年龄内·可估计',
};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}

function text(tag, value) {
  const node = document.createElement(tag);
  node.textContent = typeof value === 'object' ? JSON.stringify(value) : String(value ?? '未知');
  return node;
}

function table(container, rows, formatters = {}) {
  container.replaceChildren();
  if (!rows.length) {
    container.append(text('p', '当前范围没有记录'));
    return;
  }
  const columns = Array.from(new Set(rows.flatMap(row => Object.keys(row))));
  const grid = document.createElement('table');
  const head = document.createElement('tr');
  for (const column of columns) head.append(text('th', column));
  const thead = document.createElement('thead');
  thead.append(head);
  grid.append(thead);
  const body = document.createElement('tbody');
  for (const row of rows) {
    const line = document.createElement('tr');
    for (const column of columns) {
      const cell = text('td', formatters[column] ? formatters[column](row[column], row) : row[column]);
      line.append(cell);
    }
    body.append(line);
  }
  grid.append(body);
  container.append(grid);
}

function fixed(value, digits = 4) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : (value ?? '—');
}

function renderRisk(value) {
  const risk = value.result.risk;
  // 顶部状态卡片：让“无观测/超龄/维修后”状态一目了然，不藏在表格里。
  const statuses = document.querySelector('#risk-statuses');
  statuses.replaceChildren();
  const counts = risk.fleet_forecast;
  const cards = [
    ['观测年龄内可估计', counts.active_machines_estimable_within_observed_age],
    ['超龄·无点估计', counts.active_machines_beyond_observed_age],
    ['维修后·仅上界', counts.active_machines_post_repair_unidentified],
    ['模型期望故障数', fixed(counts.model_expected_failures, 3)],
    ['保守上界需求（件）', counts.conservative_upper_need ?? '—'],
  ];
  for (const [label, amount] of cards) {
    const card = document.createElement('div');
    card.className = 'card';
    card.append(text('dt', label), text('dd', amount));
    statuses.append(card);
  }

  // 借用证据
  const hom = risk.homogeneity;
  const borrowing = [
    {'项': '等率检验 p 值（条件二项）', '值': hom.conditional_binomial_p_value === null ? '无故障，无法检验' : hom.conditional_binomial_p_value.toExponential(3)},
    {'项': '是否支持跨站点借用', '值': hom.pooling_supported ? '支持' : '不支持：故障高度集中，借用仅作敏感性对照'},
    {'项': '各站点故障数', '值': JSON.stringify(hom.failures_by_site)},
    {'项': '各站点运行暴露（小时）', '值': JSON.stringify(Object.fromEntries(Object.entries(hom.exposure_by_site).map(([k, v]) => [k, Math.round(v)])))},
    {'项': '共同率下期望故障数', '值': JSON.stringify(Object.fromEntries(Object.entries(hom.expected_failures_if_common_rate).map(([k, v]) => [k, fixed(v, 2)])))},
    {'项': '批次分布', '值': '各站点均覆盖多个批次，批次不能解释站点差异'},
  ];
  table(document.querySelector('#borrowing'), borrowing);

  // 备件情景
  const sp = risk.spare_part_scenarios;
  const spareRows = [
    {'情景': sp.site_specific.label, '严格口径期望': sp.site_specific.strict_expected_failures,
     '模型期望': sp.site_specific.model_expected_failures, '上界率期望': sp.site_specific.model_upper_rate_expected,
     '维修后上界': sp.site_specific.post_repair_upper_expected, '保守合计': sp.site_specific.conservative_expected_sum},
    {'情景': sp.pooled_sensitivity.label, '严格口径期望': sp.pooled_sensitivity.strict_expected_failures,
     '模型期望': sp.pooled_sensitivity.model_expected_failures, '上界率期望': sp.pooled_sensitivity.model_upper_rate_expected,
     '维修后上界': sp.pooled_sensitivity.post_repair_upper_expected, '保守合计': sp.pooled_sensitivity.conservative_expected_sum},
  ];
  table(document.querySelector('#spares'), spareRows);
  const interval = document.createElement('p');
  interval.textContent = `模型口径 Poisson 95% 预测区间：${JSON.stringify(counts.model_poisson_prediction_interval)} 件；保守上界（含维修后零事件上界）：${counts.conservative_upper_need ?? '—'} 件。预测窗口 ${risk.horizon_hours} 运行小时。`;
  document.querySelector('#spares').append(interval);

  // 风险集表
  table(document.querySelector('#risk-table'), risk.risk_table, {
    hazard_increment: v => fixed(v, 4), nelson_aalen: v => fixed(v, 4), kaplan_meier: v => fixed(v, 4),
  });

  // 设备明细
  const machineRows = risk.machines.map(row => ({
    machine: row.machine, site: row.site, batch: row.batch, 状态: STATUS_TEXT[row.forecast.status] || row.forecast.status,
    运行小时: Math.round(row.operating_hours), 当前片段年龄: Math.round(row.current_episode_age),
    观测故障: row.failures_observed, 大修: row.overhauls, 小修: row.minor_repairs,
    严格概率: row.forecast.strict_probability === null || row.forecast.strict_probability === undefined ? '—' : fixed(row.forecast.strict_probability, 3),
    模型概率: row.forecast.extrapolation ? fixed(row.forecast.extrapolation.probability, 3) : '—',
    概率上界: row.forecast.extrapolation ? fixed(row.forecast.extrapolation.upper, 3) : (row.forecast.upper === null || row.forecast.upper === undefined ? '—' : fixed(row.forecast.upper, 3)),
    说明: row.forecast.reason || '',
  }));
  const listNode = document.querySelector('#machine-list');
  table(listNode, machineRows);
  // 首列按钮跳设备页
  listNode.querySelectorAll('tbody tr').forEach((line, index) => {
    const cell = line.firstElementChild;
    const id = risk.machines[index].machine;
    const button = text('button', id);
    button.type = 'button';
    button.addEventListener('click', () => showMachine(id));
    cell.replaceChildren(button);
  });
}

async function showMachine(id) {
  const generation = ++requestGeneration;
  const params = new URLSearchParams(new FormData(filters));
  params.set('machine', id);
  try {
    const detail = await api(`/api/machine?${params}`);
    if (generation !== requestGeneration) return;
    const row = detail.machine;
    const node = document.querySelector('#machine-detail');
    node.replaceChildren();
    const head = document.createElement('h3');
    head.textContent = `${row.machine}（${row.site} / ${row.batch}）`;
    node.append(head);
    const facts = [
      ['当前状态', STATUS_TEXT[row.forecast.status] || row.forecast.status],
      ['累计运行小时', Math.round(row.operating_hours)],
      ['当前寿命片段年龄', Math.round(row.current_episode_age)],
      ['是否大修重置后', row.post_overhaul ? '是' : '否'],
      ['观测故障次数', row.failures_observed],
      ['大修 / 小修次数', `${row.overhauls} / ${row.minor_repairs}`],
      ['报废业务时标', row.retired_hour ?? '—'],
    ];
    const dl = document.createElement('dl');
    for (const [key, value] of facts) dl.append(text('dt', key), text('dd', value));
    node.append(dl);
    const forecast = row.forecast;
    const model = forecast.extrapolation;
    const riskLines = [
      `严格证据口径：${forecast.strict_probability === null || forecast.strict_probability === undefined ? '不提供点估计（' + forecast.reason + '）' : fixed(forecast.strict_probability, 4)}`,
      model ? `恒定率模型（${model.model}）：概率 ${fixed(model.probability, 4)}，95% 区间 [${fixed(model.lower, 4)}, ${fixed(model.upper, 4)}]，其中超龄外推 ${Math.round(model.extrapolated_hours)} 运行小时无故障观测支持` : '',
      forecast.upper !== null && forecast.upper !== undefined && !model ? `维修后零事件 95% 上界概率：${fixed(forecast.upper, 4)}（不识别点估计）` : '',
    ].filter(Boolean);
    const ul = document.createElement('ul');
    for (const line of riskLines) ul.append(text('li', line));
    node.append(ul);
    const events = document.createElement('p');
    events.textContent = `该时点可见事件：${JSON.stringify(row.visible_events)}；可见维修：${JSON.stringify(row.visible_repairs)}`;
    node.append(events);
  } catch (error) {
    statusNode.textContent = error.message;
  }
}

function render(value, savedTitle = '') {
  current = value;
  const summary = document.querySelector('#summary');
  summary.replaceChildren();
  for (const [key, amount] of Object.entries(value.result.summary)) {
    summary.append(text('dt', key), text('dd', amount));
  }
  const notes = [...value.result.notes, ...value.result.risk.notes];
  document.querySelector('#notes').replaceChildren(...notes.map(note => text('li', note)));
  document.querySelector('#digest').textContent = `资料指纹：${value.digest}`;
  table(document.querySelector('#results'), value.result.rows);
  renderRisk(value);
  statusNode.textContent = savedTitle ? `正在查看保存的分析：${savedTitle}` : '分析已更新';
  filters.elements.cutoff.value = value.options.cutoff;
  filters.elements.group.value = value.options.group;
  filters.elements.horizon_hours.value = value.options.horizon_hours;
}

async function refresh() {
  const generation = ++requestGeneration;
  const query = new URLSearchParams(new FormData(filters));
  statusNode.textContent = '正在分析';
  try {
    const result = await api(`/api/view?${query}`);
    if (generation === requestGeneration) render(result);
  } catch (error) {
    if (generation === requestGeneration) {
      current = null;
      statusNode.textContent = error.message;
    }
  }
}

async function history() {
  const entries = await api('/api/analyses');
  const list = document.querySelector('#history');
  list.replaceChildren();
  for (const entry of entries) {
    const item = document.createElement('li');
    const button = text('button', entry.title);
    button.type = 'button';
    button.addEventListener('click', async () => {
      const generation = ++requestGeneration;
      try {
        const saved = await api(`/api/analyses/${entry.id}`);
        if (generation === requestGeneration) render(saved, saved.title);
      } catch (error) {
        statusNode.textContent = error.message;
      }
    });
    item.append(button, text('small', entry.created_at));
    list.append(item);
  }
}

filters.addEventListener('submit', event => {
  event.preventDefault();
  refresh();
});

document.querySelector('#save').addEventListener('submit', async event => {
  event.preventDefault();
  if (!current) return;
  try {
    const saved = await api('/api/analyses', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: event.target.elements.title.value, options: current.options})});
    render(saved, saved.title);
    await history();
  } catch (error) {
    statusNode.textContent = error.message;
  }
});

document.querySelector('#source').addEventListener('click', async () => {
  try {
    const data = await api(`/api/source?table=${encodeURIComponent(document.querySelector('#tables').value)}`);
    table(document.querySelector('#raw'), data.rows);
  } catch (error) {
    statusNode.textContent = error.message;
  }
});

async function start() {
  const catalog = await api('/api/catalog');
  document.querySelector('#description').textContent = catalog.description;
  filters.elements.cutoff.min = catalog.min_cutoff;
  filters.elements.cutoff.max = catalog.max_cutoff;
  filters.elements.cutoff.value = catalog.default_cutoff;
  const groups = [['', '全部'], ...catalog.groups.map(group => [group, group])];
  for (const [key, label] of groups) {
    const option = text('option', label);
    option.value = key;
    filters.elements.group.append(option);
  }
  for (const name of Object.keys(catalog.tables)) {
    const option = text('option', `${name}（${catalog.tables[name]}）`);
    option.value = name;
    document.querySelector('#tables').append(option);
  }
  await refresh();
  await history();
}

start().catch(error => { statusNode.textContent = error.message; });
