/** Lightweight event toast with optional Gemini summary; deterministic fallback. */
import type { DisasterModule, ImpactMetrics } from './types';

type ToastKind = DisasterModule | 'area' | 'info';
type ToastInput = {
  kind: ToastKind;
  title: string;
  summary?: string;
  runId?: string;
  frameIndex?: number;
  metrics?: ImpactMetrics;
  response?: { metrics?: { units_dispatched?: number; average_eta_s?: number | null; hotspots_unreached?: number; stations_cut_off?: number } };
};

const container = (() => {
  let host = document.getElementById('toast-host') as HTMLDivElement | null;
  if (!host) { host = document.createElement('div'); host.id = 'toast-host'; document.body.append(host); }
  return host;
})();

const stylesheet = (() => {
  if (document.getElementById('toast-style')) return;
  const style = document.createElement('style'); style.id = 'toast-style';
  style.textContent = `
    #toast-host{position:fixed;top:68px;right:16px;z-index:9999;display:flex;flex-direction:column;gap:8px;max-width:340px;pointer-events:none;}
    .toast{pointer-events:auto;position:relative;background:#2a2a2a;border:1px solid #ffffff14;color:#e8e8e7;padding:12px 14px;border-radius:6px;box-shadow:0 6px 20px #0007,0 1px 2px #0005;font:13px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;transform:translateX(360px);opacity:0;transition:transform .25s cubic-bezier(.22,.9,.3,1),opacity .2s;}
    .toast.show{transform:translateX(0);opacity:1;}
    .toast .eyebrow{font-size:11px;color:#a0a09e;margin-bottom:4px;}
    .toast .dot{display:none;}
    .toast h4{margin:0 0 4px;font-size:14px;font-weight:600;color:#f1f1ef;letter-spacing:-.1px;}
    .toast p{margin:0;color:#c6c6c3;font-size:13px;line-height:1.4;}
    .toast .toast-finding{margin-top:6px;color:#9b9b98;font-size:12px;}
    .toast .label{display:none;}
    .toast .close{position:absolute;top:6px;right:8px;background:none;border:0;color:#7a7a78;font-size:14px;cursor:pointer;padding:2px 5px;line-height:1;}
    .toast .close:hover{color:#e8e8e7;}
  `;
  document.head.append(style);
})();
void stylesheet;

const kindLabels: Record<ToastKind, string> = {
  flood: 'Flood', breach: 'Breach', quake: 'Earthquake',
  fire: 'Fire', chain: 'Quake → flood', area: 'Area', info: '',
};

function describe(input: ToastInput): string {
  const m = input.metrics, r = input.response?.metrics;
  if (input.kind === 'area') return input.summary ?? 'Terrain loaded.';
  const parts: string[] = [];
  if (m) {
    if (Number.isFinite(m.affected_area_km2) && m.affected_area_km2 > 0) parts.push(`${m.affected_area_km2.toFixed(2)} km²`);
    if (typeof m.roads_closed === 'number') parts.push(`${m.roads_closed.toLocaleString()} roads closed`);
    if (typeof m.people_affected === 'number') parts.push(`${m.people_affected.toLocaleString()} people`);
  }
  if (r) {
    if (typeof r.units_dispatched === 'number' && r.units_dispatched > 0) parts.push(`${r.units_dispatched} units`);
    if (typeof r.average_eta_s === 'number') parts.push(`ETA ${(r.average_eta_s / 60).toFixed(1)} min`);
  }
  return parts.join(' · ') || 'Running.';
}

export function showToast(input: ToastInput, duration_ms = 9500): HTMLDivElement {
  const toast = document.createElement('div'); toast.className = `toast ${input.kind}`;
  const eyebrow = document.createElement('div'); eyebrow.className = 'eyebrow'; eyebrow.textContent = kindLabels[input.kind];
  if (!kindLabels[input.kind]) eyebrow.hidden = true;
  const title = document.createElement('h4'); title.textContent = input.title;
  const body = document.createElement('p'); body.textContent = input.summary ?? describe(input);
  const finding = document.createElement('p'); finding.className = 'toast-finding'; finding.hidden = true;
  const label = document.createElement('div'); label.className = 'label'; label.hidden = true;
  const close = document.createElement('button'); close.className = 'close'; close.textContent = '×'; close.onclick = () => dismiss();
  toast.append(close, eyebrow, title, body, finding, label);
  container.append(toast);
  requestAnimationFrame(() => toast.classList.add('show'));
  const timer = window.setTimeout(() => dismiss(), duration_ms);
  function dismiss(): void { window.clearTimeout(timer); toast.classList.remove('show'); setTimeout(() => toast.remove(), 400); }
  // Try Gemini briefing in the background and swap text in if available.
  if (input.runId && input.frameIndex !== undefined) {
    void (async () => {
      try {
        const cached = await fetch(`/api/product/runs/${encodeURIComponent(input.runId!)}/briefing_cached?frame_index=${input.frameIndex}`).then(r => r.ok ? r.json() : null);
        if (cached?.text && typeof cached.text === 'string') {
          body.textContent = cached.text;
          label.textContent = cached.engine === 'gemini' ? 'Cached Gemini briefing · computed outputs · human review required' : 'Cached computed summary · deterministic, not AI-generated';
          const high = cached.advisory?.findings?.find((item:{priority:string}) => item.priority === 'high');
          if (high) { finding.textContent = high.detail; finding.hidden = false; }
          return;
        }
        const status = await fetch('/api/gemini/status').then(r => r.ok ? r.json() : null);
        if (!status?.ready) return;
        const briefing = await fetch(`/api/product/runs/${encodeURIComponent(input.runId!)}/advisory`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ frame_index: input.frameIndex, question: 'In 60 words, describe what is happening in this frame and the top response gaps.' }),
        }).then(r => r.ok ? r.json() : null);
        const text = briefing?.summary;
        if (text && typeof text === 'string') {
          body.textContent = text.trim();
          label.textContent = briefing.label;
          const high = briefing.findings?.find((item:{priority:string}) => item.priority === 'high');
          if (high) { finding.textContent = high.detail; finding.hidden = false; }
        }
      } catch { /* keep deterministic text */ }
    })();
  }
  return toast;
}
