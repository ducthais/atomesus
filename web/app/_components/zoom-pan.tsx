'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Maximize, Minus, Plus } from 'lucide-react';

const MIN = 0.2, MAX = 2.5, PAD = 24, DRAG = 4;
const clamp = (k: number) => Math.min(MAX, Math.max(MIN, k));

/**
 * Khung xem sơ đồ: kéo để di chuyển mọi hướng, cuộn chuột để phóng to / thu nhỏ quanh con trỏ,
 * nút điều khiển và phím tắt (+, -, mũi tên, 0). Mở ra thì tự thu vừa khung.
 * Bấm link bên trong vẫn hoạt động; vừa kéo xong thì không tính là bấm.
 */
export function ZoomPan({ width, height, label, children }: { width: number; height: number; label: string; children: React.ReactNode }) {
  const viewport = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const drag = useRef<{ id: number; px: number; py: number; x: number; y: number; moved: boolean } | null>(null);
  const dragged = useRef(false);
  const [dragging, setDragging] = useState(false);

  const fit = useCallback(() => {
    const el = viewport.current;
    if (!el) return;
    const k = clamp(Math.min((el.clientWidth - PAD * 2) / width, (el.clientHeight - PAD * 2) / height, 1));
    setView({ k, x: (el.clientWidth - width * k) / 2, y: (el.clientHeight - height * k) / 2 });
  }, [width, height]);
  useEffect(() => { fit(); }, [fit]);

  /** Phóng to / thu nhỏ quanh điểm (cx, cy) trong khung, giữ nguyên điểm đó dưới con trỏ. */
  const zoomAt = useCallback((factor: number, cx?: number, cy?: number) => setView(v => {
    const el = viewport.current;
    const px = cx ?? (el ? el.clientWidth / 2 : 0), py = cy ?? (el ? el.clientHeight / 2 : 0);
    const k = clamp(v.k * factor);
    const f = k / v.k;
    return { k, x: px - (px - v.x) * f, y: py - (py - v.y) * f };
  }), []);

  // Cuộn chuột cần listener không passive để chặn trang cuộn theo.
  useEffect(() => {
    const el = viewport.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - rect.left, e.clientY - rect.top);
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [zoomAt]);

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    drag.current = { id: e.pointerId, px: e.clientX, py: e.clientY, x: view.x, y: view.y, moved: false };
    dragged.current = false;
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.px, dy = e.clientY - d.py;
    if (!d.moved && Math.hypot(dx, dy) < DRAG) return;
    if (!d.moved) { d.moved = true; dragged.current = true; setDragging(true); try { viewport.current?.setPointerCapture(d.id); } catch { /* thiết bị trỏ không hỗ trợ capture: vẫn kéo được trong khung */ } }
    setView(v => ({ ...v, x: d.x + dx, y: d.y + dy }));
  };
  const endDrag = () => {
    drag.current = null;
    setDragging(false);
    // Trình duyệt phát click ngay sau pointerup trong cùng lượt; cờ chỉ cần sống tới lúc đó để chặn click kết thúc cú kéo.
    setTimeout(() => { dragged.current = false; }, 0);
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    const step = 60;
    const moves: Record<string, [number, number]> = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] };
    if (moves[e.key]) { e.preventDefault(); const [dx, dy] = moves[e.key]; setView(v => ({ ...v, x: v.x + dx, y: v.y + dy })); }
    else if (e.key === '+' || e.key === '=') { e.preventDefault(); zoomAt(1.2); }
    else if (e.key === '-') { e.preventDefault(); zoomAt(1 / 1.2); }
    else if (e.key === '0') { e.preventDefault(); fit(); }
  };

  return <div className="zoom-pan">
    <div ref={viewport} className={`zoom-viewport ${dragging ? 'dragging' : ''}`} style={{ height: `clamp(320px, ${height + PAD * 2}px, 72vh)` }}
      role="figure" aria-label={`${label}. Kéo để di chuyển, cuộn để phóng to thu nhỏ, phím mũi tên và + − 0.`} tabIndex={0}
      onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={endDrag} onPointerCancel={endDrag} onKeyDown={onKeyDown}
      onClickCapture={e => { if (dragged.current) { e.preventDefault(); e.stopPropagation(); dragged.current = false; } }}>
      <div className="zoom-content" style={{ width, height, transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})` }}>{children}</div>
    </div>
    <div className="zoom-controls" role="toolbar" aria-label="Điều khiển thu phóng">
      <button type="button" onClick={() => zoomAt(1.2)} aria-label="Phóng to" title="Phóng to (+)"><Plus size={15} aria-hidden /></button>
      <span className="zoom-level" aria-live="polite">{Math.round(view.k * 100)}%</span>
      <button type="button" onClick={() => zoomAt(1 / 1.2)} aria-label="Thu nhỏ" title="Thu nhỏ (−)"><Minus size={15} aria-hidden /></button>
      <button type="button" onClick={fit} aria-label="Vừa khung" title="Vừa khung (0)"><Maximize size={14} aria-hidden /></button>
    </div>
    <p className="zoom-hint">Kéo để di chuyển · cuộn chuột để phóng to / thu nhỏ · nút ⤢ để vừa khung</p>
  </div>;
}
