import proj4 from 'proj4';
import type { Terrain } from './types';

/**
 * Display-only ground texture for the AOI.
 * 1) Real: Esri World Imagery tiles (public, CORS-enabled) stitched and warped into the local UTM grid.
 * 2) Fallback: a deterministic procedural texture, labelled as such. Never used for analysis.
 */
const UTM = '+proj=utm +zone=43 +datum=WGS84 +units=m +no_defs';
const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile';
export const ESRI_LABEL = 'Real imagery: Esri World Imagery (Esri, Maxar, Earthstar Geographics) · display only, not 2023 flood-date';
export const PROCEDURAL_LABEL = 'Procedural ground texture · illustrative, not imagery';

export interface CityTexture { canvas: HTMLCanvasElement; source: 'esri' | 'procedural'; label: string }

const mercator = (lon: number, lat: number, z: number): [number, number] => [
  (lon + 180) / 360 * 2 ** z * 256,
  (1 - Math.asinh(Math.tan(lat * Math.PI / 180)) / Math.PI) / 2 * 2 ** z * 256,
];

function loadTile(z: number, x: number, y: number): Promise<HTMLImageElement | null> {
  return new Promise(resolve => {
    const image = new Image(); image.crossOrigin = 'anonymous'; image.decoding = 'async';
    const timer = window.setTimeout(() => { image.src = ''; resolve(null); }, 12000);
    image.onload = () => { window.clearTimeout(timer); resolve(image); };
    image.onerror = () => { window.clearTimeout(timer); resolve(null); };
    image.src = `${ESRI}/${z}/${y}/${x}`;
  });
}

function outputSize(terrain: Terrain, maxTexture: number): [number, number] {
  const [ex, ez] = terrain.extent_m, longest = Math.min(4096, maxTexture);
  return ex >= ez ? [longest, Math.round(longest * ez / ex)] : [Math.round(longest * ex / ez), longest];
}

async function esriTexture(terrain: Terrain, maxTexture: number, zoom: number): Promise<HTMLCanvasElement | null> {
  const toGeo = proj4(UTM, 'EPSG:4326');
  const geo = (x: number, z: number) => toGeo.forward([terrain.origin[0] + x, terrain.origin[1] - z]) as [number, number];
  const [ex, ez] = terrain.extent_m;
  const corners = [[0, 0], [ex, 0], [0, ez], [ex, ez]].map(([x, z]) => mercator(...geo(x, z), zoom));
  const tx0 = Math.floor(Math.min(...corners.map(p => p[0])) / 256), tx1 = Math.floor(Math.max(...corners.map(p => p[0])) / 256);
  const ty0 = Math.floor(Math.min(...corners.map(p => p[1])) / 256), ty1 = Math.floor(Math.max(...corners.map(p => p[1])) / 256);
  const nx = tx1 - tx0 + 1, ny = ty1 - ty0 + 1;
  if (nx * ny > 900) return null;
  const stitched = document.createElement('canvas'); stitched.width = nx * 256; stitched.height = ny * 256;
  const sctx = stitched.getContext('2d')!; sctx.fillStyle = '#8a8676'; sctx.fillRect(0, 0, stitched.width, stitched.height);
  const jobs: [number, number][] = [];
  for (let y = ty0; y <= ty1; y++) for (let x = tx0; x <= tx1; x++) jobs.push([x, y]);
  let failed = 0, cursor = 0;
  const worker = async () => {
    while (cursor < jobs.length) {
      const [x, y] = jobs[cursor++];
      const image = await loadTile(zoom, x, y);
      if (image) sctx.drawImage(image, (x - tx0) * 256, (y - ty0) * 256); else failed++;
    }
  };
  await Promise.all(Array.from({ length: 12 }, worker));
  if (failed > jobs.length * 0.3) return null;
  // Affine warp from local metres (UTM) to the stitched mercator image. Over ~16 km the
  // projection difference is effectively affine (rotation ≈ grid convergence).
  const [W, H] = outputSize(terrain, maxTexture);
  const origin = [tx0 * 256, ty0 * 256];
  const P = [[0, 0], [ex, 0], [0, ez]].map(([x, z]) => { const m = mercator(...geo(x, z), zoom); return [m[0] - origin[0], m[1] - origin[1]]; });
  const forward = new DOMMatrix([(P[1][0] - P[0][0]) / W, (P[1][1] - P[0][1]) / W, (P[2][0] - P[0][0]) / H, (P[2][1] - P[0][1]) / H, P[0][0], P[0][1]]);
  const out = document.createElement('canvas'); out.width = W; out.height = H;
  const ctx = out.getContext('2d')!; ctx.imageSmoothingQuality = 'high';
  ctx.setTransform(forward.inverse()); ctx.drawImage(stitched, 0, 0); ctx.setTransform(1, 0, 0, 1, 0, 0);
  return out;
}

/** Deterministic (seeded) urban-looking ground texture used only when imagery is unreachable. */
export function proceduralTexture(terrain: Terrain, maxTexture = 4096): HTMLCanvasElement {
  const [W, H] = outputSize(terrain, Math.min(2048, maxTexture));
  const canvas = document.createElement('canvas'); canvas.width = W; canvas.height = H;
  const ctx = canvas.getContext('2d')!;
  let seed = 1337; const rand = () => { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
  ctx.fillStyle = '#9b9584'; ctx.fillRect(0, 0, W, H);
  const palette = ['#a39c88', '#8f8a7a', '#b0a58c', '#857f70', '#9a927b', '#7d8a6c', '#a8a08a'];
  for (let i = 0; i < 9000; i++) {
    const w = 4 + rand() * 26, h = 4 + rand() * 26;
    ctx.fillStyle = palette[Math.floor(rand() * palette.length)]; ctx.globalAlpha = .35 + rand() * .5;
    ctx.fillRect(rand() * W, rand() * H, w, h);
  }
  ctx.globalAlpha = .55; ctx.strokeStyle = '#6f6c64';
  for (let i = 0; i < 260; i++) { ctx.lineWidth = .6 + rand() * 1.6; ctx.beginPath(); const x = rand() * W, y = rand() * H; ctx.moveTo(x, y); ctx.lineTo(x + (rand() - .5) * 600, y + (rand() - .5) * 600); ctx.stroke(); }
  const image = ctx.getImageData(0, 0, W, H), data = image.data;
  for (let i = 0; i < data.length; i += 4) { const n = (rand() - .5) * 18; data[i] += n; data[i + 1] += n; data[i + 2] += n; }
  ctx.putImageData(image, 0, 0); ctx.globalAlpha = 1;
  return canvas;
}

export async function loadCityTexture(terrain: Terrain, maxTexture: number, zoom = 15): Promise<CityTexture> {
  try {
    const canvas = await esriTexture(terrain, maxTexture, zoom);
    if (canvas) return { canvas, source: 'esri', label: ESRI_LABEL };
  } catch { /* fall through to the labelled procedural texture */ }
  return { canvas: proceduralTexture(terrain, maxTexture), source: 'procedural', label: PROCEDURAL_LABEL };
}

/** Fetches Esri World Imagery for an arbitrary WGS84 bbox and returns a stitched canvas (no warping). */
export async function loadBboxImagery(bbox: [number, number, number, number], zoom = 16, maxTiles = 400): Promise<HTMLCanvasElement | null> {
  const [west, south, east, north] = bbox;
  if (!Number.isFinite(west + south + east + north) || west >= east || south >= north) return null;
  const topLeft = mercator(west, north, zoom);
  const bottomRight = mercator(east, south, zoom);
  const tx0 = Math.floor(topLeft[0] / 256), tx1 = Math.floor(bottomRight[0] / 256);
  const ty0 = Math.floor(topLeft[1] / 256), ty1 = Math.floor(bottomRight[1] / 256);
  const nx = tx1 - tx0 + 1, ny = ty1 - ty0 + 1;
  if (nx <= 0 || ny <= 0 || nx * ny > maxTiles) return null;
  const stitched = document.createElement('canvas'); stitched.width = nx * 256; stitched.height = ny * 256;
  const sctx = stitched.getContext('2d')!; sctx.fillStyle = '#8a8676'; sctx.fillRect(0, 0, stitched.width, stitched.height);
  const jobs: [number, number][] = [];
  for (let y = ty0; y <= ty1; y++) for (let x = tx0; x <= tx1; x++) jobs.push([x, y]);
  let failed = 0, cursor = 0;
  const worker = async () => {
    while (cursor < jobs.length) {
      const [x, y] = jobs[cursor++];
      const image = await loadTile(zoom, x, y);
      if (image) sctx.drawImage(image, (x - tx0) * 256, (y - ty0) * 256); else failed++;
    }
  };
  await Promise.all(Array.from({ length: 8 }, worker));
  if (failed > jobs.length * 0.5) return null;
  // Crop to the exact bbox pixel window so the plane maps 1:1 to lat/lon.
  const originX = tx0 * 256, originY = ty0 * 256;
  const left = topLeft[0] - originX, top = topLeft[1] - originY;
  const width = bottomRight[0] - topLeft[0], height = bottomRight[1] - topLeft[1];
  const out = document.createElement('canvas');
  out.width = Math.max(16, Math.round(width)); out.height = Math.max(16, Math.round(height));
  const ctx = out.getContext('2d')!; ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(stitched, left, top, width, height, 0, 0, out.width, out.height);
  return out;
}
