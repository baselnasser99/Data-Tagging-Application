// The "Answers per week" bar chart (includes/activity_row.html). Needs vendor/chart.umd.min.js.
(function () {
  const canvas = document.getElementById('weeklyChart');
  if (!canvas || !window.Chart) return;
  const weeks = JSON.parse(canvas.dataset.chartData);

  // Colours come from the theme tokens (tokens.css), so the chart matches light and dark.
  function colours() {
    const style = getComputedStyle(document.documentElement);
    const token = name => style.getPropertyValue(name).trim();
    const bar = token('--data'), edge = token('--data-edge');
    // The rim is only for contrast on the light theme; where it matches the fill (dark), a 1 px
    // stroke just blurs at fractional display scaling and reads as a second, offset bar.
    return { bar, edge, edgeWidth: edge.toLowerCase() === bar.toLowerCase() ? 0 : 1,
             text: token('--muted'), grid: token('--chart-grid') };
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
        borderColor: c.edge,
        borderWidth: c.edgeWidth,
        borderRadius: { topLeft: 4, topRight: 4 },  // flat feet: bars stand square on the axis
        borderSkipped: 'start',
        maxBarThickness: 28
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: document.documentElement.classList.contains('is-drawing') ? undefined : false,  // grows only with the other bars, once per tab
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
    chart.data.datasets[0].borderColor = c.edge;
    chart.data.datasets[0].borderWidth = c.edgeWidth;
    chart.options.scales.x.ticks.color = chart.options.scales.y.ticks.color = c.text;
    chart.options.scales.y.grid.color = c.grid;
    chart.update();
  }
  document.addEventListener('themechange', repaint);
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', repaint);
})();
