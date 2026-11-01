/** First-visit guided tour. Persists dismissal in localStorage. */
const STORAGE_KEY = 'otec.tour.v1.seen';

interface Step {
  target: string;
  title: string;
  body: string;
  side?: 'top' | 'bottom' | 'left' | 'right' | 'center';
}

const steps: Step[] = [
  { target: 'body', side: 'center', title: 'Welcome', body: 'Reviewer: please don\'t skip. 11 steps, about 45 seconds.' },
  { target: '#viewport', side: 'center', title: 'Real Delhi', body: 'FABDEM elevation, 205k OSM buildings. Orbit, pan, zoom.' },
  { target: '.product-steps', side: 'bottom', title: 'Three steps', body: 'Disaster, Impact, Response.' },
  { target: '#workflow-card', side: 'left', title: 'Pick a disaster', body: 'Flood, breach, earthquake, fire, or quake → flood.' },
  { target: '[data-product-step="2"]', side: 'bottom', title: 'Earthquakes shake and collapse', body: 'Buildings sway. Collapsed bands sink and tilt.' },
  { target: '#viewport', side: 'center', title: 'Follow a responder', body: 'Click any vehicle. Camera follows; orbit stays yours.' },
  { target: '#briefing-button', side: 'left', title: 'Gemini advisories', body: 'Grounded, cited. Deterministic fallback if offline.' },
  { target: '#toast-host', side: 'left', title: 'Event toasts', body: 'Every event drops a summary. Top finding if cached.' },
  { target: '#advanced-toggle', side: 'bottom', title: 'Advanced', body: 'Layers, cameras, exaggeration.' },
  { target: '.area-launch', side: 'bottom', title: 'New area', body: 'Delhi FABDEM pack. Satellite preview for anywhere else.' },
  { target: 'body', side: 'center', title: 'Done', body: 'Press ? to replay.' },
];

const stylesheet = (() => {
  if (document.getElementById('tour-style')) return;
  const style = document.createElement('style'); style.id = 'tour-style';
  style.textContent = `
    .tour-backdrop{position:fixed;inset:0;z-index:9998;background:#00000066;opacity:0;transition:opacity .2s;pointer-events:auto;}
    .tour-backdrop.show{opacity:1;}
    .tour-spotlight{position:fixed;z-index:9999;border-radius:4px;box-shadow:0 0 0 9999px #00000066,0 0 0 1px #ffffff30;pointer-events:none;transition:all .3s cubic-bezier(.22,.9,.3,1);}
    .tour-popover{position:fixed;z-index:10000;background:#2a2a2a;border:1px solid #ffffff14;border-radius:6px;color:#e8e8e7;padding:16px 20px 14px;width:340px;box-sizing:border-box;box-shadow:0 10px 30px #0007,0 1px 2px #0005;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;opacity:0;transform:translateY(6px);transition:opacity .2s,transform .2s;}
    .tour-popover.show{opacity:1;transform:translateY(0);}
    .tour-popover .tour-step{font-size:11px;color:#a0a09e;margin-bottom:10px;letter-spacing:.2px;}
    .tour-popover h3{margin:0 0 6px;font-size:15px;font-weight:600;color:#f1f1ef;letter-spacing:-.1px;}
    .tour-popover p{margin:0 0 16px;color:#c6c6c3;font-size:13.5px;line-height:1.45;}
    .tour-popover .tour-progress{display:flex;flex:1;min-width:0;gap:3px;margin:0;}
    .tour-popover .tour-progress i{flex:1 1 0;height:2px;border-radius:1px;background:#ffffff1a;transition:background .15s;}
    .tour-popover .tour-progress i.active{background:#9b9b98;}
    .tour-popover .tour-footer{display:flex;align-items:center;justify-content:space-between;gap:16px;min-width:0;}
    .tour-popover .tour-actions{display:flex;align-items:center;flex-shrink:0;gap:4px;}
    .tour-popover button{font:500 13px/1 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:6px 12px;min-height:0;border-radius:4px;border:0;background:transparent;color:#c6c6c3;cursor:pointer;transition:background .12s,color .12s;}
    .tour-popover button:hover{background:#ffffff0f;color:#f1f1ef;}
    .tour-popover .tour-next{color:#f1f1ef;background:#ffffff10;margin-left:2px;}
    .tour-popover .tour-next:hover{background:#ffffff1c;}
    .tour-replay-fab{position:fixed;bottom:68px;right:22px;z-index:9000;width:28px;height:28px;border-radius:4px;background:#2a2a2ae8;color:#a0a09e;border:1px solid #ffffff14;cursor:pointer;font:600 13px/1 -apple-system,BlinkMacSystemFont,sans-serif;transition:background .12s,color .12s;}
    .tour-replay-fab:hover{background:#333;color:#f1f1ef;}
  `;
  document.head.append(style);
})();
void stylesheet;

let active = false;

export function hasSeenTour(): boolean {
  try { return localStorage.getItem(STORAGE_KEY) === '1'; } catch { return false; }
}

function markSeen(): void {
  try { localStorage.setItem(STORAGE_KEY, '1'); } catch { /* ignore */ }
}

function placePopover(popover: HTMLElement, rect: DOMRect | null, side: Step['side']): void {
  const pad = 14;
  const vw = window.innerWidth, vh = window.innerHeight;
  const pw = popover.offsetWidth, ph = popover.offsetHeight;
  if (!rect || side === 'center') {
    popover.style.left = `${(vw - pw) / 2}px`;
    popover.style.top = `${(vh - ph) / 2}px`;
    return;
  }
  let left = 0, top = 0;
  switch (side) {
    case 'bottom': left = rect.left + rect.width / 2 - pw / 2; top = rect.bottom + pad; break;
    case 'top': left = rect.left + rect.width / 2 - pw / 2; top = rect.top - ph - pad; break;
    case 'left': left = rect.left - pw - pad; top = rect.top + rect.height / 2 - ph / 2; break;
    case 'right': left = rect.right + pad; top = rect.top + rect.height / 2 - ph / 2; break;
  }
  left = Math.max(pad, Math.min(vw - pw - pad, left));
  top = Math.max(pad, Math.min(vh - ph - pad, top));
  popover.style.left = `${left}px`;
  popover.style.top = `${top}px`;
}

export function startTour(): void {
  if (active) return;
  active = true;
  const backdrop = document.createElement('div'); backdrop.className = 'tour-backdrop';
  const spotlight = document.createElement('div'); spotlight.className = 'tour-spotlight';
  const popover = document.createElement('div'); popover.className = 'tour-popover';
  document.body.append(backdrop, spotlight, popover);
  requestAnimationFrame(() => { backdrop.classList.add('show'); popover.classList.add('show'); });

  let index = 0;

  function render(): void {
    const step = steps[index];
    const target = step.target === 'body' ? null : document.querySelector(step.target) as HTMLElement | null;
    const rect = target?.getBoundingClientRect() ?? null;

    if (rect && step.side !== 'center') {
      const pad = 6;
      spotlight.style.left = `${rect.left - pad}px`;
      spotlight.style.top = `${rect.top - pad}px`;
      spotlight.style.width = `${rect.width + pad * 2}px`;
      spotlight.style.height = `${rect.height + pad * 2}px`;
      spotlight.style.opacity = '1';
    } else {
      spotlight.style.opacity = '0';
    }

    popover.innerHTML = `
      <div class="tour-step">${index + 1} / ${steps.length}</div>
      <h3></h3>
      <p></p>
      <div class="tour-footer">
        <div class="tour-progress">${steps.map((_, i) => `<i class="${i <= index ? 'active' : ''}"></i>`).join('')}</div>
        <div class="tour-actions">
          <button class="tour-skip">Skip</button>
          ${index > 0 ? '<button class="tour-back">Back</button>' : ''}
          <button class="tour-next">${index === steps.length - 1 ? 'Done' : 'Next'}</button>
        </div>
      </div>
    `;
    popover.querySelector('h3')!.textContent = step.title;
    popover.querySelector('p')!.textContent = step.body;
    popover.querySelector('.tour-skip')!.addEventListener('click', end);
    popover.querySelector('.tour-back')?.addEventListener('click', () => { index = Math.max(0, index - 1); render(); });
    popover.querySelector('.tour-next')!.addEventListener('click', () => {
      if (index === steps.length - 1) { end(); return; }
      index++; render();
    });
    requestAnimationFrame(() => placePopover(popover, rect, step.side));
  }

  function end(): void {
    markSeen();
    backdrop.classList.remove('show'); popover.classList.remove('show'); spotlight.style.opacity = '0';
    setTimeout(() => { backdrop.remove(); spotlight.remove(); popover.remove(); addReplayButton(); active = false; }, 220);
    window.removeEventListener('keydown', keyHandler);
    window.removeEventListener('resize', resizeHandler);
  }

  const keyHandler = (event: KeyboardEvent): void => {
    if (event.key === 'Escape') end();
    else if (event.key === 'ArrowRight' || event.key === 'Enter') { if (index < steps.length - 1) { index++; render(); } else end(); }
    else if (event.key === 'ArrowLeft' && index > 0) { index--; render(); }
  };
  const resizeHandler = (): void => render();
  window.addEventListener('keydown', keyHandler);
  window.addEventListener('resize', resizeHandler);

  render();
}

function addReplayButton(): void {
  if (document.getElementById('tour-replay')) return;
  const button = document.createElement('button');
  button.id = 'tour-replay'; button.className = 'tour-replay-fab'; button.textContent = '?';
  button.title = 'Replay tour';
  button.setAttribute('aria-label', 'Replay guided tour');
  button.onclick = () => startTour();
  document.body.append(button);
}

export function maybeStartTour(): void {
  if (hasSeenTour()) { addReplayButton(); return; }
  setTimeout(() => startTour(), 600);
}
