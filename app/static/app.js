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

  initRecipientSelection();
});

// Selection in the recipients table.
//
// Two different things can be selected, and the difference matters because one
// of them reaches rows this page never rendered:
//
//   - the ticked checkboxes, which can only ever be the current page, and
//   - "everything matching the active filter", which the server resolves by
//     re-running the same query the page was built from.
//
// The second mode is expressed by a hidden flag rather than by posting every
// id, and it is deliberately fragile: any change to an individual row drops
// back to plain checkbox selection, so a stale "all matching" flag can never
// ride along with a selection the user has since narrowed.
function initRecipientSelection() {
  var checkAll = document.getElementById('check-all');
  var rows = Array.prototype.slice.call(document.querySelectorAll('.row-check'));
  if (!checkAll || !rows.length) return;

  var flag = document.getElementById('select-all-matching');
  var banner = document.getElementById('selection-banner');
  var bannerText = document.getElementById('selection-banner-text');
  var selectAllBtn = document.getElementById('select-all-matching-btn');
  var clearBtn = document.getElementById('clear-selection-btn');
  var bulkForm = document.getElementById('bulk-form');

  // No banner means every match already fits on this page, so ticked rows are
  // the whole story and there is nothing extra to offer.
  var total = banner ? parseInt(banner.dataset.total, 10) : rows.length;
  var allMatching = false;

  function ticked() {
    return rows.filter(function (box) { return box.checked; }).length;
  }

  // The count an action would actually affect - used for the confirmation, so
  // "Delete" never understates what is about to happen.
  function effectiveCount() {
    return allMatching ? total : ticked();
  }

  function render() {
    var count = ticked();
    checkAll.checked = count === rows.length && count > 0;
    checkAll.indeterminate = count > 0 && count < rows.length;
    if (flag) flag.value = allMatching ? '1' : '';
    if (!banner) return;

    if (allMatching) {
      banner.classList.remove('d-none');
      bannerText.textContent = 'All ' + total + ' recipients matching this filter are selected.';
      selectAllBtn.classList.add('d-none');
      clearBtn.classList.remove('d-none');
    } else if (count === rows.length) {
      banner.classList.remove('d-none');
      bannerText.textContent = 'All ' + count + ' recipients on this page are selected.';
      selectAllBtn.classList.remove('d-none');
      clearBtn.classList.add('d-none');
    } else {
      banner.classList.add('d-none');
    }
  }

  checkAll.addEventListener('change', function () {
    // Re-ticking the header box is a page-level action; it never implies the
    // user still wants everything across every page.
    allMatching = false;
    rows.forEach(function (box) { box.checked = checkAll.checked; });
    render();
  });

  rows.forEach(function (box) {
    box.addEventListener('change', function () {
      allMatching = false;
      render();
    });
  });

  if (selectAllBtn) {
    selectAllBtn.addEventListener('click', function () {
      allMatching = true;
      rows.forEach(function (box) { box.checked = true; });
      render();
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener('click', function () {
      allMatching = false;
      rows.forEach(function (box) { box.checked = false; });
      render();
    });
  }

  if (bulkForm) {
    bulkForm.addEventListener('submit', function (event) {
      var action = document.getElementById('action');
      var count = effectiveCount();
      if (!count) {
        event.preventDefault();
        window.alert('Select at least one recipient first.');
        return;
      }
      if (action && action.value === 'delete') {
        var noun = count === 1 ? 'recipient' : 'recipients';
        if (!window.confirm('Delete ' + count + ' ' + noun + ' permanently? This cannot be undone.')) {
          event.preventDefault();
        }
      }
    });
  }

  render();
}
