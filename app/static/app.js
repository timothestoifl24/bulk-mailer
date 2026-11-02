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

  initConfirmForms();
  initRecipientSelection();
  initRemoteModals();
  initAutosubmit();
  initCopyButtons();
});

function bootstrapLib() {
  // Tabler's bundle exposes Bootstrap's components as window.tabler.
  return window.bootstrap || window.tabler || {};
}

// Confirmation dialog.
//
// A form opts in with data-confirm-title (and -body, -action, -variant; see the
// confirm() macro in _ui.html). Its submit is held back, the shared modal in
// base.html asks, and only a yes lets the form go. data-confirm-skip-if names a
// checkbox in the form that, when ticked, makes the question unnecessary - a
// dry run sends nothing, so it needs no "are you sure".
//
// If Bootstrap's modal is not available (script blocked, failed to load), the
// browser's own confirm() asks instead, so a destructive action is never left
// unguarded.
function askConfirm(options, onYes) {
  var modalEl = document.getElementById('confirm-modal');
  var Modal = bootstrapLib().Modal;
  if (!modalEl || !Modal) {
    if (window.confirm(options.title + (options.body ? '\n\n' + options.body : ''))) onYes();
    return;
  }
  var danger = options.variant !== 'primary';
  document.getElementById('confirm-modal-title').textContent = options.title;
  document.getElementById('confirm-modal-body').textContent = options.body || '';
  document.getElementById('confirm-modal-status').className = 'modal-status ' + (danger ? 'bg-danger' : 'bg-primary');
  document.getElementById('confirm-modal-icon').className = 'hero-icon mb-2 ' + (danger ? 'text-danger' : 'text-primary');

  // Replace the button each time so a stale listener from an earlier,
  // cancelled question can never fire for this one.
  var oldOk = document.getElementById('confirm-modal-ok');
  var ok = oldOk.cloneNode(false);
  ok.textContent = options.action || 'Confirm';
  ok.className = 'btn w-100 ' + (danger ? 'btn-danger' : 'btn-primary');
  oldOk.parentNode.replaceChild(ok, oldOk);

  // Bootstrap does not stack modals: when the question comes from a form in
  // the shared modal (Delete in an edit dialog), close that one first.
  var appModal = document.getElementById('app-modal');
  if (appModal && appModal.classList.contains('show')) Modal.getOrCreateInstance(appModal).hide();

  var modal = Modal.getOrCreateInstance(modalEl);
  ok.addEventListener('click', function () {
    modal.hide();
    onYes();
  });
  modal.show();
}

function initConfirmForms() {
  document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form.hasAttribute || !form.hasAttribute('data-confirm-title')) return;
    if (form.dataset.confirmed === '1') {
      delete form.dataset.confirmed;
      return;
    }
    var skip = form.dataset.confirmSkipIf;
    if (skip && form.elements[skip] && form.elements[skip].checked) return;

    event.preventDefault();
    var submitter = event.submitter;
    askConfirm({
      title: form.dataset.confirmTitle,
      body: form.dataset.confirmBody,
      action: form.dataset.confirmAction,
      variant: form.dataset.confirmVariant
    }, function () {
      form.dataset.confirmed = '1';
      if (form.requestSubmit) {
        form.requestSubmit(submitter || undefined);
      } else {
        form.submit();
      }
    });
  });
}

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

  // The action bar only shows once something is selected, and the list
  // picker only for the actions that need one.
  var bulkBar = document.getElementById('bulk-bar');
  var bulkCount = document.getElementById('bulk-bar-count');
  var actionSelect = document.getElementById('action');
  var listWrap = document.getElementById('target-list-wrap');
  if (bulkBar) bulkBar.classList.add('bulk-bar-floating');

  function renderBar() {
    if (!bulkBar) return;
    var n = effectiveCount();
    bulkBar.classList.toggle('is-visible', n > 0);
    if (bulkCount) bulkCount.textContent = n.toLocaleString() + ' selected';
    if (listWrap && actionSelect) {
      listWrap.classList.toggle('d-none', actionSelect.value.indexOf('list') === -1);
    }
  }
  if (actionSelect) actionSelect.addEventListener('change', renderBar);

  function render() {
    var count = ticked();
    checkAll.checked = count === rows.length && count > 0;
    checkAll.indeterminate = count > 0 && count < rows.length;
    if (flag) flag.value = allMatching ? '1' : '';
    renderBar();
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
        if (bulkForm.dataset.confirmed === '1') {
          delete bulkForm.dataset.confirmed;
          return;
        }
        event.preventDefault();
        var noun = count === 1 ? 'recipient' : 'recipients';
        askConfirm({
          title: 'Delete ' + count.toLocaleString() + ' ' + noun + '?',
          body: 'They are removed for good, along with their list memberships. This cannot be undone.',
          action: 'Delete ' + noun
        }, function () {
          bulkForm.dataset.confirmed = '1';
          if (bulkForm.requestSubmit) bulkForm.requestSubmit(); else bulkForm.submit();
        });
      }
    });
  }

  render();
}


// Forms in the shared modal.
//
// A link with data-modal opens its URL in #app-modal instead of navigating:
// the page is fetched with an X-Partial header, and the server answers with
// just the modal content (_modal_partial.html). Submitting that form posts it
// the same way. A validation error comes back as the form again, shown in
// place; success comes back as an X-Redirect header naming where to go, so the
// flash message survives for the page the browser lands on (web.done()).
//
// Without fetch or Bootstrap the link simply opens the same URL as a page.
function initRemoteModals() {
  var modalEl = document.getElementById('app-modal');
  var Modal = bootstrapLib().Modal;
  if (!modalEl || !Modal || !window.fetch || !window.FormData) return;
  var dialog = modalEl.querySelector('.modal-dialog');
  var modal = Modal.getOrCreateInstance(modalEl);

  function show(html) {
    dialog.innerHTML = html;
    var Tooltip = bootstrapLib().Tooltip;
    if (Tooltip) {
      Array.prototype.forEach.call(dialog.querySelectorAll('[data-bs-toggle="tooltip"]'), function (el) {
        Tooltip.getOrCreateInstance(el);
      });
    }
    modal.show();
    var first = dialog.querySelector('[autofocus], input:not([type=hidden]):not([disabled])');
    if (first) setTimeout(function () { first.focus(); }, 150);
  }

  function load(url, options, fallbackUrl) {
    options = options || {};
    options.headers = { 'X-Partial': '1' };
    options.credentials = 'same-origin';
    return fetch(url, options).then(function (response) {
      var target = response.headers.get('X-Redirect');
      if (target) {
        window.location.href = target;
        return;
      }
      if (!response.ok && response.status !== 422) {
        window.location.href = fallbackUrl || url;
        return;
      }
      return response.text().then(show);
    }).catch(function () {
      window.location.href = fallbackUrl || url;
    });
  }

  document.addEventListener('click', function (event) {
    var link = event.target.closest ? event.target.closest('a[data-modal]') : null;
    if (!link || event.ctrlKey || event.metaKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    dialog.className = 'modal-dialog modal-dialog-centered' + (link.dataset.modalSize ? ' modal-' + link.dataset.modalSize : '');
    load(link.href);
  });

  modalEl.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form.hasAttribute('data-modal-form')) return;
    event.preventDefault();
    var button = form.querySelector('button[type=submit]:not([form])');
    if (button) button.disabled = true;
    load(form.action, { method: 'POST', body: new FormData(form) }, form.action);
  });
}

// A control with data-autosubmit submits its form as soon as it changes - for
// filter selects and settings switches that need no separate Save button.
function initAutosubmit() {
  document.addEventListener('change', function (event) {
    var control = event.target;
    if (!control.hasAttribute || !control.hasAttribute('data-autosubmit') || !control.form) return;
    if (control.form.requestSubmit) {
      control.form.requestSubmit();
    } else {
      control.form.submit();
    }
  });
}

// <button data-copy="#some-input"> copies that field's value.
function initCopyButtons() {
  document.addEventListener('click', function (event) {
    var button = event.target.closest ? event.target.closest('[data-copy]') : null;
    if (!button) return;
    var field = document.querySelector(button.dataset.copy);
    if (!field) return;
    var done = function () {
      button.classList.add('btn-success');
      setTimeout(function () { button.classList.remove('btn-success'); }, 1200);
    };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(field.value).then(done);
    } else {
      field.select();
      document.execCommand('copy');
      done();
    }
  });
}
