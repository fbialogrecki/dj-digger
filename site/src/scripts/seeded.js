// The seeded generator of the app's records (Main.qml's Cover and scripts/app_icon.py):
// the same seed always draws the same radial code. Used at build time only.
export const hash = (text) => {
  let value = 7;
  for (const char of text) value = (Math.imul(value, 31) + char.charCodeAt(0)) >>> 0;
  return Math.abs(value | 0);
};
// mulberry32
export const seeded = (state) => () => {
  state = (state + 0x6d2b79f5) >>> 0;
  let t = Math.imul(state ^ (state >>> 15), 1 | state);
  t = ((t + Math.imul(t ^ (t >>> 7), 61 | t)) >>> 0) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
};

// Radial strokes and dots from the label (inner) to the rim (outer), centred on cx, cy:
// one SVG path for the strokes and the dots' centres.
export function radialCode(random, inner, outer, cx = 0, cy = 0) {
  const patterns = [[[0, 1]], [[0, 0.5]], [[0.5, 1]], [[0.3, 0.7]], [[0, 1 / 3], [2 / 3, 1]], []];
  const at = (angle, radius) => [(cx + Math.cos(angle) * radius).toFixed(2), (cy + Math.sin(angle) * radius).toFixed(2)];
  const count = 18 + Math.floor(random() * 10);
  const turn = random() * Math.PI * 2;
  let strokes = "";
  const dots = [];
  for (let i = 0; i < count; i++) {
    const angle = turn + ((i + 0.15 + random() * 0.7) / count) * Math.PI * 2;
    const pattern = patterns[Math.floor(random() * patterns.length)];
    for (const [start, end] of pattern)
      strokes += `M${at(angle, inner + start * (outer - inner)).join(" ")}L${at(angle, inner + end * (outer - inner)).join(" ")}`;
    if (!pattern.length || random() < 0.3)
      for (let n = 1 + Math.floor(random() * 3); n > 0; n--) dots.push(at(angle, inner + random() * (outer - inner)));
  }
  return { strokes, dots };
}
