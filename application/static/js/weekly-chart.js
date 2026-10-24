// The "Answers per week" bar chart (includes/activity_row.html). Needs vendor/chart.umd.min.js.
(function () {
  const canvas = document.getElementById('weeklyChart');
  if (!canvas || !window.Chart) return;
  const weeks = JSON.parse(canvas.dataset.chartData);

  // Colours come from the theme tokens in app.css, so the chart matches light and dark.
  function token(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
  Chart.defaults.color = token('--muted');
  const chart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels: weeks.map(w => w.week),
      datasets: [{
        label: 'Answers submitted',
        data: weeks.map(w => w.answers),
        backgroundColor: token('--accent'),
        borderRadius: 4,
        maxBarThickness: 28
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false } },
        y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: token('--chart-grid') } }
      }
    }
  });

  // Repaint when the theme changes: the switch in the account menu, or the system setting.
  function recolour() {
    const muted = token('--muted');
    chart.data.datasets[0].backgroundColor = token('--accent');
    chart.options.scales.x.ticks.color = muted;
    chart.options.scales.y.ticks.color = muted;
    chart.options.scales.y.grid.color = token('--chart-grid');
    chart.update('none');
  }
  document.addEventListener('themechange', recolour);
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', recolour);
})();
