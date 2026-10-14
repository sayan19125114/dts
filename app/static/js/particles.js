/* Lightweight fake-3D particle field for the login background.
   No dependencies, same-origin ES module. Self-terminates when its canvas
   leaves the DOM, pauses when the tab is hidden, and honours reduced motion. */

export function mountStarfield(container, opts = {}) {
  if (!container || typeof ResizeObserver === "undefined") return () => {};

  const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const canvas = document.createElement("canvas");
  canvas.className = "particle-canvas";
  canvas.setAttribute("aria-hidden", "true");
  container.prepend(canvas);
  const ctx = canvas.getContext("2d");
  if (!ctx) return () => {};

  const count = opts.count ?? 60;
  const color = opts.color ?? "99,102,241";
  const baseAlpha = opts.baseAlpha ?? 0.5;
  const maxSize = opts.maxSize ?? 2.2;
  const speed = opts.speed ?? 0.0009;

  let w = 0, h = 0, raf = 0, last = 0;
  const parts = [];
  for (let i = 0; i < count; i++) {
    parts.push({ x: Math.random() * 2 - 1, y: Math.random() * 2 - 1, z: Math.random() * 0.9 + 0.1, r: Math.random() });
  }

  function resize() {
    w = container.clientWidth;
    h = container.clientHeight;
    canvas.width = Math.max(1, Math.round(w * dpr));
    canvas.height = Math.max(1, Math.round(h * dpr));
    canvas.style.width = w + "px";
    canvas.style.height = h + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function draw() {
    ctx.clearRect(0, 0, w, h);
    const cx = w / 2, cy = h / 2, k = Math.min(w, h) * 0.4;
    for (const p of parts) {
      const sx = cx + (p.x / p.z) * k;
      const sy = cy + (p.y / p.z) * k;
      if (sx < -20 || sx > w + 20 || sy < -20 || sy > h + 20) continue;
      const radius = Math.max(0.4, (1 - p.z) * maxSize * (0.6 + p.r * 0.8));
      const alpha = baseAlpha * (1 - p.z) + 0.08;
      ctx.beginPath();
      ctx.arc(sx, sy, radius, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(${color},${alpha.toFixed(3)})`;
      ctx.fill();
    }
  }

  function cleanup() {
    cancelAnimationFrame(raf);
    ro.disconnect();
    document.removeEventListener("visibilitychange", onVis);
  }

  function step(now) {
    if (!canvas.isConnected) { cleanup(); return; }
    const dt = Math.min(now - last, 60);
    last = now;
    for (const p of parts) {
      p.z -= speed * dt;
      if (p.z <= 0.06) { p.x = Math.random() * 2 - 1; p.y = Math.random() * 2 - 1; p.z = 1; p.r = Math.random(); }
    }
    draw();
    raf = requestAnimationFrame(step);
  }

  function onVis() {
    if (document.hidden) { cancelAnimationFrame(raf); raf = 0; }
    else if (!raf) { last = performance.now(); raf = requestAnimationFrame(step); }
  }

  resize();
  draw();

  const ro = new ResizeObserver(() => {
    resize();
    if (reduce) draw();
  });
  ro.observe(container);

  if (!reduce) {
    document.addEventListener("visibilitychange", onVis);
    last = performance.now();
    raf = requestAnimationFrame(step);
  }

  return cleanup;
}
