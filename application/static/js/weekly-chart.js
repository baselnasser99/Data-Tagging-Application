// The "Answers per week" bar chart (includes/activity_row.html). Needs vendor/chart.umd.min.js.
(function () {
  const canvas = document.getElementById('weeklyChart');
  if (!canvas || !window.Chart) return;
  const weeks = JSON.parse(canvas.dataset.chartData);
  Chart.defaults.font.family = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
  Chart.defaults.color = '#56616F';
  new Chart(canvas, {
    type: 'bar',
    data: {
      labels: weeks.map(w => w.week),
      datasets: [{
        label: 'Answers submitted',
        data: weeks.map(w => w.answers),
        backgroundColor: '#3366CC',  // the accent blue (--accent in app.css)
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
        y: { beginAtZero: true, ticks: { precision: 0 }, grid: { color: '#E3E7EC' } }
      }
    }
  });
})();
