document.addEventListener("htmx:error", (event) => {
  if (!(event.detail?.error instanceof TypeError)) return;
  const main = document.getElementById("main");
  main.replaceChildren(document.getElementById("unreachable").content.cloneNode(true));
});
// Keep focus with the top dialog, including confirmation over conversation info.
let modal = null, outside = document.activeElement;
const dialogs = [];
document.addEventListener('focusin', (event) => {
  const dialog = event.target.closest('[role="dialog"]');
  if (!dialog || dialogs.some((item) => item.element === dialog)) outside = event.target;
});
document.addEventListener('pointerdown', (event) => {
  const control = event.target.closest('a, button');
  if (control) outside = control;
});
new MutationObserver(() => {
  while (dialogs.length && !dialogs.at(-1).element.checkVisibility()) {
    const closed = dialogs.pop();
    if (closed.opener?.isConnected) closed.opener.focus({preventScroll: true});
  }
  const current = [...document.querySelectorAll('.veil:not([hidden]) > [role="dialog"], .drawer:not([hidden])')]
    .filter((element) => element.checkVisibility()).at(-1) ?? null;
  if (current === modal) return;
  modal = current;
  if (current && dialogs.at(-1)?.element !== current) {
    dialogs.push({element: current, opener: outside});
    current.focus({preventScroll: true});
  }
}).observe(document.documentElement, {childList: true, subtree: true, attributes: true, attributeFilter: ['hidden']});
document.addEventListener('keydown', (event) => {
  if (!modal) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    (modal.matches('.drawer') ? modal : modal.parentElement).hidden = true;
  } else if (event.key === 'Tab') {
    const controls = [...modal.querySelectorAll('a[href], button, input, select, textarea, [tabindex="0"]')]
      .filter((element) => !element.disabled && element.tabIndex >= 0 && element.checkVisibility());
    const first = controls[0], last = controls.at(-1);
    if (!first || (event.shiftKey && (document.activeElement === first || document.activeElement === modal))) {
      event.preventDefault();
      (last ?? modal).focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
});
