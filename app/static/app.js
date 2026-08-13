// Theme toggle. The initial value is applied in <head> to avoid a flash;
// this only handles the click and remembers the choice.
document.addEventListener('DOMContentLoaded', function () {
  var toggle = document.getElementById('theme-toggle');
  if (toggle) {
    toggle.addEventListener('click', function () {
      var root = document.documentElement;
      var next = root.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-bs-theme', next);
      try { localStorage.setItem('theme', next); } catch (e) { /* private mode */ }
    });
  }

  // "Select all" checkbox in recipient tables.
  var all = document.getElementById('check-all');
  if (all) {
    all.addEventListener('change', function () {
      document.querySelectorAll('.row-check').forEach(function (box) {
        box.checked = all.checked;
      });
    });
  }
});
