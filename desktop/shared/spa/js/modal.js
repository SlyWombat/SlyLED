/**
 * modal.js — Modal dialog management with stack support.
 * @module modal
 */

const _stack = [];

/** Push current modal content onto stack before opening a sub-modal. */
export function pushModal() {
  const m = document.getElementById('modal');
  if (!m || m.style.display === 'none') return;
  const title = document.getElementById('modal-title').textContent;
  const body = document.getElementById('modal-body').innerHTML;
  _stack.push({ title, body });
}

/** Pop and restore the previous modal, or close if stack is empty. */
export function popModal() {
  if (_stack.length) {
    const prev = _stack.pop();
    document.getElementById('modal-title').textContent = prev.title;
    document.getElementById('modal-body').innerHTML = prev.body;
    return true;
  }
  return false;
}

/** Close the modal — pops stack first, only hides if no parent on stack. */
export function closeModal() {
  if (popModal()) return;
  document.getElementById('modal').style.display = 'none';
}

/** Open the modal with a title and HTML body. */
export function openModal(title, bodyHtml) {
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').innerHTML = bodyHtml;
  document.getElementById('modal').style.display = 'block';
}
