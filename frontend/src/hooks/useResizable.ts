import { useState, useEffect, useCallback, useRef } from "react";

interface Size {
  width: number;
  height: number;
}

export type ResizeDirection = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
interface UseResizableOptions {
  initial: Size;
  min: Size;
  max: Size;
  storageKey?: string;
}

export function useResizable({ initial, min, max, storageKey }: UseResizableOptions) {
  const [size, setSize] = useState<Size>(() => {
    if (!storageKey || typeof window === "undefined") return initial;
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw) {
        const parsed = JSON.parse(raw) as Size;
        const vw = Math.min(max.width, window.innerWidth - 32);
        const vh = Math.min(max.height, window.innerHeight - 32);
        return {
          width: Math.max(min.width, Math.min(vw, parsed.width)),
          height: Math.max(min.height, Math.min(vh, parsed.height)),
        };
      }
    } catch {
      // ignore
    }
    return initial;
  });

  const [isResizing, setIsResizing] = useState(false);
  const startRef = useRef<{ x: number; y: number; width: number; height: number } | null>(null);
  const cleanupRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    if (storageKey && typeof window !== "undefined") {
      localStorage.setItem(storageKey, JSON.stringify(size));
    }
  }, [size, storageKey]);

  useEffect(() => {
    return () => {
      cleanupRef.current?.();
    };
  }, []);

  const startResize = useCallback((e: React.MouseEvent, direction: ResizeDirection = "se") => {
    e.preventDefault();
    e.stopPropagation();
    setIsResizing(true);
    startRef.current = {
      x: e.clientX,
      y: e.clientY,
      width: size.width,
      height: size.height,
    };

    const viewportMax = {
      width: Math.min(max.width, window.innerWidth - 32),
      height: Math.min(max.height, window.innerHeight - 32),
    };

    const handleMove = (moveEvent: MouseEvent) => {
      const start = startRef.current;
      if (!start) return;
      const dx = moveEvent.clientX - start.x;
      const dy = moveEvent.clientY - start.y;
      // 面板锚定右下：w/n 方向增长 = 向左/向上扩展
      const dw = direction.includes("e") ? dx : direction.includes("w") ? -dx : 0;
      const dh = direction.includes("s") ? dy : direction.includes("n") ? -dy : 0;
      setSize({
        width: Math.max(min.width, Math.min(viewportMax.width, start.width + dw)),
        height: Math.max(min.height, Math.min(viewportMax.height, start.height + dh)),
      });
    };

    const handleUp = () => {
      setIsResizing(false);
      startRef.current = null;
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
      cleanupRef.current = null;
    };

    cleanupRef.current = () => {
      window.removeEventListener("mousemove", handleMove);
      window.removeEventListener("mouseup", handleUp);
    };

    window.addEventListener("mousemove", handleMove);
    window.addEventListener("mouseup", handleUp);
  }, [size, min, max]);

  return { size, setSize, isResizing, startResize };
}
