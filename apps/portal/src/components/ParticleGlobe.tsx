import { useEffect, useRef } from "react";

// Local rendering of the particle globe from the colleague's landing-page design.
export function ParticleGlobe() {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const motion = matchMedia("(prefers-reduced-motion: reduce)");
    let width = 0,
      frame = 0,
      rotation = 0.3,
      visible = true,
      last = 0;
    const pointer = { x: 0, y: 0 };
    const points = Array.from({ length: 1300 }, (_, i) => {
      const y = 1 - (i / 1299) * 2;
      const radius = Math.sqrt(1 - y * y);
      const theta = i * 2.399963;
      return { x: Math.cos(theta) * radius, y, z: Math.sin(theta) * radius };
    });
    function draw() {
      if (!canvas || !ctx || !width) return;
      ctx.clearRect(0, 0, width, width);
      const center = width / 2;
      const radius = width * 0.31;
      const glow = ctx.createRadialGradient(
        center,
        center,
        0,
        center,
        center,
        width * 0.48,
      );
      glow.addColorStop(0, "rgba(77,232,176,.13)");
      glow.addColorStop(1, "rgba(77,232,176,0)");
      ctx.fillStyle = glow;
      ctx.fillRect(0, 0, width, width);
      ctx.strokeStyle = "rgba(77,232,176,.22)";
      ctx.lineWidth = 0.8;
      for (const angle of [-0.45, 0.6, 1.6]) {
        ctx.beginPath();
        ctx.ellipse(
          center,
          center,
          radius * 1.43,
          radius * 0.45,
          angle,
          0,
          Math.PI * 2,
        );
        ctx.stroke();
      }
      const a = rotation + pointer.x * 0.2;
      const tilt = -0.18 + pointer.y * 0.15;
      for (const p of points) {
        const x = p.x * Math.cos(a) + p.z * Math.sin(a);
        const z = p.z * Math.cos(a) - p.x * Math.sin(a);
        const y = p.y * Math.cos(tilt) - z * Math.sin(tilt);
        const depth = z * Math.cos(tilt) + p.y * Math.sin(tilt);
        const perspective = 3.6 / (3.6 - depth);
        ctx.fillStyle = `rgba(114,244,193,${0.16 + (depth + 1) * 0.32})`;
        ctx.beginPath();
        ctx.arc(
          center + x * radius * perspective,
          center + y * radius * perspective,
          Math.max(0.5, (width / 530) * (0.8 + (depth + 1) * 0.55)),
          0,
          Math.PI * 2,
        );
        ctx.fill();
      }
    }
    function tick(now: number) {
      if (!visible || document.hidden || motion.matches) {
        frame = 0;
        return;
      }
      rotation += Math.min(50, now - (last || now)) * 0.00012;
      last = now;
      draw();
      frame = requestAnimationFrame(tick);
    }
    function resume() {
      cancelAnimationFrame(frame);
      last = 0;
      draw();
      frame =
        visible && !document.hidden && !motion.matches
          ? requestAnimationFrame(tick)
          : 0;
    }
    const resize = new ResizeObserver(([entry]) => {
      width = entry.contentRect.width;
      const dpr = Math.min(devicePixelRatio || 1, 2);
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(width * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      resume();
    });
    const observer = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      resume();
    });
    function move(e: PointerEvent) {
      if (motion.matches) return;
      const bounds = canvas!.getBoundingClientRect();
      pointer.x = (e.clientX - bounds.left) / bounds.width - 0.5;
      pointer.y = (e.clientY - bounds.top) / bounds.height - 0.5;
    }
    resize.observe(canvas);
    observer.observe(canvas);
    canvas.addEventListener("pointermove", move);
    document.addEventListener("visibilitychange", resume);
    motion.addEventListener("change", resume);
    return () => {
      cancelAnimationFrame(frame);
      resize.disconnect();
      observer.disconnect();
      canvas.removeEventListener("pointermove", move);
      document.removeEventListener("visibilitychange", resume);
      motion.removeEventListener("change", resume);
    };
  }, []);
  return <canvas className="particle-globe" ref={ref} aria-hidden="true" />;
}
