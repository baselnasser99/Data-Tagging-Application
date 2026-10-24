// The "Answers per week" bar chart (includes/activity_row.html). Needs vendor/chart.umd.min.js.
(function () {
  const canvas = document.getElementById('weeklyChart');
  if (!canvas || !window.Chart) return;
  const weeks = JSON.parse(canvas.dataset.chartData);

  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
  const chart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: weeks.map(w => w.week),
      datasets: [{ label: 'Answers submitted', data: weeks.map(w => w.answers), borderRadius: 4, maxBarThickness: 28 }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false } },
        y: { beginAtZero: true, ticks: { precision: 0 } }
      }
    }
  });

  // Colours come from the theme tokens (tokens.css), read again whenever the theme
  // changes: the switch in the account menu, or the system setting.
  function paint() {
    const style = getComputedStyle(document.documentElement);
    const token = name => style.getPropertyValue(name).trim();
    chart.data.datasets[0].backgroundColor = token('--accent');
    chart.options.scales.x.ticks.color = chart.options.scales.y.ticks.color = token('--muted');
    chart.options.scales.y.grid.color = token('--chart-grid');
    chart.update('none');
  }
  paint();
  document.addEventListener('themechange', paint);
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', paint);
})();
