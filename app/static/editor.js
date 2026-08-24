// Rich editor for HTML message bodies, layered over the existing textarea.
//
// The textarea remains the single source of truth: it keeps its name, it is
// what the form posts, and it is what the server stores. The visual editor is
// a view onto it that has to be opted into, and every switch back writes
// straight into it. Nothing is saved that you did not see in one pane or the
// other.
//
// Why opt-in rather than always-on: Quill models a document as its own set of
// registered formats, so anything it does not know about is dropped on the way
// in - tables, inline styles, <center>, conditional comments. That is most of
// what a designed HTML email is made of. Silently normalising such a template
// the moment someone opened it to fix a typo would be destructive, so switching
// into visual mode on content that would not survive asks first.

(function () {
  'use strict';

  // Markup that does not survive a round trip through Quill's document model
  // intact. Note "does not survive intact" rather than "is removed": Quill 2
  // keeps a basic table, but drops the attributes that made it a layout - a
  // width:100% becomes an ordinary bordered table. Either way the stored HTML
  // is no longer the HTML the designer wrote, which is what the warning is for.
  var LOSSY_PATTERNS = [
    { re: /<table[\s>]/i, what: 'tables (kept, but their sizing and styling are not)' },
    { re: /<(style|head|meta|link)[\s>]/i, what: 'a <style>/<head> block' },
    { re: /\sstyle\s*=\s*["']/i, what: 'inline style attributes' },
    { re: /<(center|font)[\s>]/i, what: 'legacy layout tags' },
    { re: /<!--\s*\[if/i, what: 'Outlook conditional comments' }
  ];

  var PLACEHOLDERS = [
    'first_name', 'last_name', 'name', 'display_name', 'email',
    'company', 'department', 'title', 'unsubscribe_url'
  ];

  function lossyReasons(html) {
    return LOSSY_PATTERNS.filter(function (p) { return p.re.test(html); })
                         .map(function (p) { return p.what; });
  }

  function build(textarea) {
    var wrapper = document.createElement('div');
    wrapper.className = 'rich-editor mb-1';

    var bar = document.createElement('div');
    bar.className = 'btn-list mb-2';

    var visualBtn = document.createElement('button');
    visualBtn.type = 'button';
    visualBtn.className = 'btn btn-sm';
    visualBtn.textContent = 'Visual';

    var htmlBtn = document.createElement('button');
    htmlBtn.type = 'button';
    htmlBtn.className = 'btn btn-sm active';
    htmlBtn.textContent = 'HTML';

    // Inserting a placeholder is the one thing that has to work identically in
    // both panes, because it is the reason most people open this at all.
    var insert = document.createElement('select');
    insert.className = 'form-select form-select-sm w-auto ms-auto';
    insert.innerHTML = '<option value="">Insert placeholder...</option>' +
      PLACEHOLDERS.map(function (name) {
        return '<option value="' + name + '">{{ ' + name + ' }}</option>';
      }).join('');

    bar.appendChild(visualBtn);
    bar.appendChild(htmlBtn);
    bar.appendChild(insert);

    var host = document.createElement('div');
    host.className = 'rich-editor-surface d-none';

    textarea.parentNode.insertBefore(wrapper, textarea);
    wrapper.appendChild(bar);
    wrapper.appendChild(host);
    wrapper.appendChild(textarea);

    var quill = null;
    var visual = false;

    function ensureQuill() {
      if (quill) return quill;
      quill = new Quill(host, {
        theme: 'snow',
        placeholder: 'Write the message body...',
        modules: {
          toolbar: [
            [{ header: [1, 2, 3, false] }],
            ['bold', 'italic', 'underline', 'strike'],
            [{ list: 'ordered' }, { list: 'bullet' }],
            [{ align: [] }],
            ['link', 'blockquote'],
            ['clean']
          ]
        }
      });

      // Mirror every edit straight into the textarea instead of only syncing
      // on submit. The submit event is not the only way a form gets sent -
      // form.submit() called from script skips listeners entirely - and a
      // silently stale textarea means the save appears to work while storing
      // the previous body. Cheap: message bodies are a few kilobytes.
      quill.on('text-change', syncToTextarea);
      return quill;
    }

    function toVisual() {
      if (visual) return;
      var html = textarea.value;
      var reasons = lossyReasons(html);
      if (reasons.length) {
        var message =
          'This template contains ' + reasons.join(', ') + '.\n\n' +
          'The visual editor rewrites the body into the markup it understands, ' +
          'so those will be simplified or lost as soon as you switch back and ' +
          'save. The HTML view leaves them exactly as they are.\n\n' +
          'Switch to visual editing anyway?';
        if (!window.confirm(message)) return;
      }
      var editor = ensureQuill();
      // clipboard.convert rather than innerHTML: it runs the same sanitiser
      // Quill uses for pasted content, so what lands is what Quill can
      // actually round-trip, with no half-supported markup left behind.
      editor.setContents(editor.clipboard.convert({ html: html }), 'silent');
      visual = true;
      host.classList.remove('d-none');
      textarea.classList.add('d-none');
      visualBtn.classList.add('active');
      htmlBtn.classList.remove('active');
    }

    // Quill's own bookkeeping, which must not end up in a sent message.
    var QUILL_ARTEFACTS = [
      [/\sdata-row="[^"]*"/g, ''],
      [/\sclass="ql-[^"]*"/g, ''],
      [/\sclass=""/g, '']
    ];

    function syncToTextarea() {
      if (!visual || !quill) return;

      // root.innerHTML, deliberately not getSemanticHTML(): the latter encodes
      // every space as &nbsp;, which turns {{ first_name }} into
      // {{&nbsp;first_name&nbsp;}} and stops Jinja recognising it as a
      // placeholder at all. That failure is invisible until a campaign goes
      // out with literal braces in it.
      var html = quill.root.innerHTML.trim();

      QUILL_ARTEFACTS.forEach(function (pair) {
        html = html.replace(pair[0], pair[1]);
      });

      // Belt and braces: whatever the serialiser does, a placeholder must come
      // back out with plain spaces inside its braces.
      html = html.replace(/\{\{[^{}]*\}\}/g, function (token) {
        return token.replace(/&nbsp;/g, ' ');
      });

      // A Quill document that is visually empty still serialises as
      // "<p><br></p>"; storing that as a body would be a lie.
      textarea.value = (html === '<p></p>' || html === '<p><br></p>') ? '' : html;
    }

    function toHtml() {
      if (!visual) return;
      syncToTextarea();
      visual = false;
      host.classList.add('d-none');
      textarea.classList.remove('d-none');
      htmlBtn.classList.add('active');
      visualBtn.classList.remove('active');
    }

    visualBtn.addEventListener('click', toVisual);
    htmlBtn.addEventListener('click', toHtml);

    insert.addEventListener('change', function () {
      var name = insert.value;
      insert.selectedIndex = 0;
      if (!name) return;
      var token = '{{ ' + name + ' }}';
      if (visual && quill) {
        var range = quill.getSelection(true);
        quill.insertText(range ? range.index : quill.getLength(), token, 'user');
      } else {
        var start = textarea.selectionStart || 0;
        var end = textarea.selectionEnd || 0;
        textarea.value = textarea.value.slice(0, start) + token + textarea.value.slice(end);
        textarea.selectionStart = textarea.selectionEnd = start + token.length;
        textarea.focus();
      }
    });

    // Belt and braces alongside the text-change mirror above: a submit that
    // does fire its event syncs once more, so the posted value is current even
    // if a change landed without a text-change notification.
    var form = textarea.form;
    if (form) form.addEventListener('submit', syncToTextarea);
  }

  document.addEventListener('DOMContentLoaded', function () {
    if (typeof Quill === 'undefined') return; // page did not load the library
    document.querySelectorAll('textarea[data-rich-editor]').forEach(build);
  });
})();
