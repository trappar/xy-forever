// Small DOM helpers: element building, the confirm/prompt dialog, and the notice line.
type Child = Node | string | null | undefined | false;

export function h<K extends keyof HTMLElementTagNameMap>(tag: K, props: Partial<Record<string, unknown>> = {}, ...children: Child[]): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v as EventListener);
    else if (k === 'class') e.className = String(v);
    else if (k in e && typeof v !== 'string') (e as unknown as Record<string, unknown>)[k] = v;
    else e.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of children) if (c !== null && c !== undefined && c !== false) e.append(c);
  return e;
}

const dialog = (): HTMLDialogElement => document.getElementById('dialog') as HTMLDialogElement;

/** Asks in plain words; resolves true only on the confirm button. */
export function confirmAction(title: string, body: string, ok: string, danger = false): Promise<boolean> {
  return ask(title, body, ok, danger, null).then((v) => v !== null);
}

/** Asks for one line of text; null when cancelled. */
export function promptText(title: string, body: string, value: string, ok = 'OK'): Promise<string | null> {
  return ask(title, body, ok, false, value);
}

function ask(title: string, body: string, ok: string, danger: boolean, value: string | null): Promise<string | null> {
  const d = dialog();
  const form = d.querySelector('form')!;
  const input = value === null ? null : h('input', { type: 'text', value, 'aria-label': title });
  form.replaceChildren(h('h2', {}, title), h('p', {}, body), input ?? '',
    h('div', { class: 'row' }, h('button', { value: 'cancel', type: 'submit' }, 'Cancel'),
      h('button', { value: 'ok', type: 'submit', class: danger ? 'danger solid' : 'primary', 'data-ok': true }, ok)));
  return new Promise((resolve) => {
    d.addEventListener('close', () => resolve(d.returnValue === 'ok' ? (input?.value ?? '') : null), { once: true });
    d.returnValue = '';
    d.showModal();
    (input ?? form.querySelector<HTMLButtonElement>('[data-ok]'))?.focus();
  });
}

export function notice(text: string, kind: 'error' | 'busy' | 'info' = 'info'): void {
  const n = document.getElementById('notice')!;
  n.hidden = text === '';
  n.className = `notice ${kind}`;
  n.textContent = text;
}

export function downloadBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  h('a', { href: url, download: name }).click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export function previewBugReport(json: string): void {
  const d = document.getElementById('bug-report-dialog') as HTMLDialogElement;
  document.getElementById('bug-report-preview')!.textContent = json;
  document.getElementById('bug-report-download')!.onclick = () => {
    downloadBlob(new Blob([json], { type: 'application/json' }), 'xy-forever-bug-report.json');
  };
  d.showModal();
}
