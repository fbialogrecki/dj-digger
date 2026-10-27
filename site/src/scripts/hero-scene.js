// Hero visual: a record turning at 33 1/3 rpm inside a halo that pulses on a
// four-on-the-floor kick, like the desktop's beat light. Loaded on demand from
// index.astro, only when WebGL2 is available; the static icon stays otherwise.
import {
  AdditiveBlending,
  BufferGeometry,
  CanvasTexture,
  CircleGeometry,
  Color,
  Float32BufferAttribute,
  Group,
  Mesh,
  MeshBasicMaterial,
  PerspectiveCamera,
  Points,
  PointsMaterial,
  Scene,
  SRGBColorSpace,
  WebGLRenderer,
} from "three";

const BPM = 124;
const BEAT = 60 / BPM;
const SPIN = ((100 / 3) / 60) * Math.PI * 2; // 33 1/3 rpm in rad/s
const TAU = Math.PI * 2;

function texture(size, draw) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  const r = size / 2;
  ctx.translate(r, r);
  draw(ctx, r);
  const map = new CanvasTexture(canvas);
  map.colorSpace = SRGBColorSpace;
  return map;
}

function disc(ctx, radius, style) {
  ctx.fillStyle = style;
  ctx.beginPath();
  ctx.arc(0, 0, radius, 0, TAU);
  ctx.fill();
}

function drawRecord(ctx, r) {
  disc(ctx, r, "#0c0c0e");
  ctx.strokeStyle = "rgba(255,255,255,0.045)";
  ctx.lineWidth = 1;
  for (let g = r * 0.34; g < r * 0.98; g += 5) {
    ctx.beginPath();
    ctx.arc(0, 0, g, 0, TAU);
    ctx.stroke();
  }
  // The app's record carries a "barcode" of 18 to 27 radial marks.
  const marks = 18 + Math.floor(Math.random() * 10);
  ctx.strokeStyle = ctx.fillStyle = "rgba(255,255,255,0.78)";
  ctx.lineCap = "round";
  ctx.lineWidth = r * 0.011;
  for (let i = 0; i < marks; i++) {
    const a = (i / marks) * TAU + Math.random() * 0.2;
    const r0 = r * (0.42 + Math.random() * 0.32);
    const r1 = Math.min(r * 0.94, r0 + r * (0.08 + Math.random() * 0.28));
    ctx.beginPath();
    ctx.moveTo(Math.cos(a) * r0, Math.sin(a) * r0);
    ctx.lineTo(Math.cos(a) * r1, Math.sin(a) * r1);
    ctx.stroke();
    if (Math.random() < 0.4) {
      const d = Math.min(r * 0.95, r1 + r * 0.05);
      ctx.beginPath();
      ctx.arc(Math.cos(a) * d, Math.sin(a) * d, r * 0.012, 0, TAU);
      ctx.fill();
    }
  }
  disc(ctx, r * 0.3, "#f4f2f8");
  disc(ctx, r * 0.025, "#0c0c0e");
}

// A light reflection that stays put while the record turns under it.
function drawSheen(ctx, r) {
  const g = ctx.createConicGradient(-Math.PI / 4, 0, 0);
  for (const [stop, alpha] of [[0, 0], [0.07, 0.16], [0.14, 0], [0.5, 0], [0.57, 0.1], [0.64, 0], [1, 0]]) {
    g.addColorStop(stop, `rgba(255,255,255,${alpha})`);
  }
  disc(ctx, r, g);
  ctx.globalCompositeOperation = "destination-out";
  disc(ctx, r * 0.3, "#000");
}

function drawDot(ctx, r) {
  const g = ctx.createRadialGradient(0, 0, 0, 0, 0, r);
  g.addColorStop(0, "rgba(255,255,255,1)");
  g.addColorStop(0.35, "rgba(255,255,255,0.8)");
  g.addColorStop(1, "rgba(255,255,255,0)");
  disc(ctx, r, g);
}

function halo(count) {
  const pink = new Color("#ff4fa3");
  const blue = new Color("#4f7bff");
  const c = new Color();
  const pos = [];
  const col = [];
  for (let i = 0; i < count; i++) {
    const a = Math.random() * TAU;
    const d = 1.08 + Math.random() ** 2 * 0.6;
    pos.push(Math.cos(a) * d, Math.sin(a) * d, (Math.random() - 0.5) * 0.15);
    c.copy(pink).lerp(blue, (Math.sin(a) + 1) / 2);
    col.push(c.r, c.g, c.b);
  }
  const geo = new BufferGeometry();
  geo.setAttribute("position", new Float32BufferAttribute(pos, 3));
  geo.setAttribute("color", new Float32BufferAttribute(col, 3));
  return geo;
}

export function start(box) {
  const reduce = matchMedia("(prefers-reduced-motion: reduce)");
  const renderer = new WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.domElement.setAttribute("aria-hidden", "true");

  const scene = new Scene();
  const camera = new PerspectiveCamera(35, 1, 0.1, 50);
  camera.position.z = 5.4;
  const deck = new Group();
  deck.rotation.x = -0.55;
  scene.add(deck);

  const face = new CircleGeometry(1, 128);
  const record = new Mesh(face, new MeshBasicMaterial({ map: texture(1024, drawRecord), transparent: true }));
  const sheen = new Mesh(
    face,
    new MeshBasicMaterial({ map: texture(512, drawSheen), transparent: true, blending: AdditiveBlending, depthWrite: false }),
  );
  sheen.position.z = 0.002;
  const dots = new PointsMaterial({
    size: 0.16,
    map: texture(64, drawDot),
    vertexColors: true,
    transparent: true,
    blending: AdditiveBlending,
    depthWrite: false,
  });
  const ring = new Points(halo(900), dots);
  deck.add(ring, record, sheen);

  let raf = 0;
  let visible = true;
  let last = 0;

  function frame(ms) {
    last = ms;
    const t = ms / 1000;
    const kick = reduce.matches ? 0.25 : Math.exp(-(t % BEAT) * 9);
    record.rotation.z = -t * SPIN;
    ring.rotation.z = t * 0.12;
    ring.scale.setScalar(1 + kick * 0.05);
    dots.size = 0.16 * (1 + kick * 0.6);
    dots.opacity = 0.35 + kick * 0.65;
    box.style.setProperty("--kick", kick.toFixed(3));
    renderer.render(scene, camera);
  }

  function loop(ms) {
    frame(ms);
    raf = requestAnimationFrame(loop);
  }

  // Reduced motion freezes the scene on one still frame; off-screen it idles.
  function sync() {
    cancelAnimationFrame(raf);
    raf = 0;
    if (visible && !reduce.matches) raf = requestAnimationFrame(loop);
    else frame(last);
  }

  new ResizeObserver(([entry]) => {
    const { width, height } = entry.contentRect;
    if (!width || !height) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    frame(last);
  }).observe(box);
  new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting;
    sync();
  }).observe(box);
  reduce.addEventListener("change", sync);

  box.append(renderer.domElement);
  box.classList.add("is-live");
}
