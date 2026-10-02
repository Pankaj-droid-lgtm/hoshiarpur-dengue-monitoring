(() => {
  const root = document.querySelector('.monitoring-charts');
  if (!root) return;
  const data = JSON.parse(root.dataset.chartData || '{}');
  const palette = { navy: '#0d3b66', blue: '#176b9d', green: '#187348', amber: '#a96608', red: '#ae2f2f', muted: '#8fa5b3' };
  const empty = slot => { slot.innerHTML = '<p class="chart-empty">No data available for the selected filters.</p>'; };
  const svg = (content, viewBox = '0 0 640 250') => `<svg viewBox="${viewBox}" role="img" aria-label="Monitoring chart" preserveAspectRatio="none">${content}</svg>`;
  const escape = value => String(value).replace(/[&<>]/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[character]);

  function lineChart(id, labels, values, positives) {
    const slot = document.querySelector(id);
    if (!values?.some(Boolean)) return empty(slot);
    const max = Math.max(...values, ...(positives || [0]), 1), width = 600, height = 180, left = 28, top = 18;
    const point = (value, index, count) => `${left + index * (width - left) / Math.max(count - 1, 1)},${top + height - value / max * height}`;
    const visits = values.map((value, index) => point(value, index, values.length)).join(' ');
    const positive = positives.map((value, index) => point(value, index, positives.length)).join(' ');
    const ticks = [0, .5, 1].map(ratio => `<line x1="${left}" x2="${width}" y1="${top + height - ratio * height}" y2="${top + height - ratio * height}" class="chart-gridline"/><text x="2" y="${top + height - ratio * height + 4}" class="chart-label">${Math.round(max * ratio)}</text>`).join('');
    const labelStep = Math.max(1, Math.ceil(labels.length / 7));
    const axisLabels = labels.map((label, index) => index % labelStep === 0 ? `<text x="${left + index * (width - left) / Math.max(labels.length - 1, 1)}" y="228" class="chart-label chart-label-center">${escape(label)}</text>` : '').join('');
    slot.innerHTML = svg(`${ticks}<polyline class="chart-line chart-line-visits" points="${visits}"/><polyline class="chart-line chart-line-positive" points="${positive}"/><text x="${width - 120}" y="24" class="chart-legend visits">Visits</text><text x="${width - 120}" y="42" class="chart-legend positive">Larval positive</text>${axisLabels}`);
  }

  function horizontalBars(id, rows, color) {
    const slot = document.querySelector(id);
    if (!rows?.length) return empty(slot);
    const max = Math.max(...rows.map(row => row.value), 1), rowHeight = 28, height = Math.max(100, rows.length * rowHeight + 30);
    const bars = rows.map((row, index) => { const y = 12 + index * rowHeight; return `<text x="0" y="${y + 12}" class="chart-label">${escape(row.label)}</text><rect x="205" y="${y}" width="${(row.value / max) * 400}" height="15" fill="${color}"/><text x="${215 + (row.value / max) * 400}" y="${y + 12}" class="chart-value">${row.value}</text>`; }).join('');
    slot.innerHTML = svg(bars, `0 0 640 ${height}`);
  }

  function donut(id, values, labels, colors) {
    const slot = document.querySelector(id), total = values.reduce((sum, value) => sum + value, 0);
    if (!total) return empty(slot);
    let start = 0;
    const parts = values.map((value, index) => { const length = value / total * 100; const item = `<circle cx="125" cy="125" r="70" fill="none" stroke="${colors[index]}" stroke-width="35" pathLength="100" stroke-dasharray="${length} ${100 - length}" stroke-dashoffset="${-start}"/>`; start += length; return item; }).join('');
    const legend = labels.map((label, index) => `<rect x="250" y="${68 + index * 35}" width="12" height="12" fill="${colors[index]}"/><text x="270" y="${79 + index * 35}" class="chart-label">${escape(label)}: ${values[index]}</text>`).join('');
    slot.innerHTML = svg(`${parts}<text x="125" y="121" class="chart-total">${total}</text><text x="125" y="140" class="chart-label chart-label-center">records</text>${legend}`, '0 0 520 250');
  }

  lineChart('#chart-trend', data.trend?.labels, data.trend?.visits, data.trend?.positive);
  donut('#chart-larval', [data.larval?.positive || 0, data.larval?.negative || 0], ['Positive', 'Negative'], [palette.red, palette.green]);
  donut('#chart-reinspection', [data.reinspection?.pending || 0, data.reinspection?.completed || 0, data.reinspection?.overdue || 0], ['Pending', 'Completed', 'Overdue'], [palette.amber, palette.green, palette.red]);
  horizontalBars('#chart-blocks', data.blocks, palette.blue);
  horizontalBars('#chart-localities', data.localities, palette.navy);
  horizontalBars('#chart-mphw', data.mphw, palette.green);
  horizontalBars('#chart-checkers', data.checkers, palette.blue);
  donut('#chart-area-type', [data.area_type?.Rural || 0, data.area_type?.Urban || 0], ['Rural', 'Urban'], [palette.blue, palette.amber]);
})();
