// The "Answers per week" bar chart (includes/activity_row.html). Needs vendor/chart.umd.min.js.
(function () {
  const canvas = document.getElementById('weeklyChart');
  if (!canvas || !window.Chart) return;
  const weeks = JSON.parse(canvas.dataset.chartData);

  // Colours come from the theme tokens (tokens.css), so the chart matches light and dark.
  function colours() {
    const style = getComputedStyle(document.documentElement);
    const token = name => style.getPropertyValue(name).trim();
    return { bar: token('--accent'), text: token('--muted'), grid: token('--chart-grid') };
  }

  const c = colours();
  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
  const chart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: weeks.map(w => w.week),
      datasets: [{
        label: 'Answers submitted',
        data: weeks.map(w => w.answers),
        backgroundColor: c.bar,
        borderRadius: 4,
        maxBarThickness: 28
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { color: c.text } },
        y: { beginAtZero: true, ticks: { precision: 0, color: c.text }, grid: { color: c.grid } }
      }
    }
  });

  // Repaint when the theme changes: the switch in the account menu, or the system setting.
  function repaint() {
    const c = colours();
    chart.data.datasets[0].backgroundColor = c.bar;
    chart.options.scales.x.ticks.color = chart.options.scales.y.ticks.color = c.text;
    chart.options.scales.y.grid.color = c.grid;
    chart.update();
  }
  document.addEventListener('themechange', repaint);
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', repaint);
})();
