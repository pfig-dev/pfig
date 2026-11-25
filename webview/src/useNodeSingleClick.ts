import { useCallback, useEffect, useRef, type MouseEvent } from "react";

export function useNodeSingleClick() {
  const pending = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (pending.current !== null) clearTimeout(pending.current);
  }, []);
  return useCallback((event: MouseEvent, action: () => void) => {
    event.stopPropagation();
    if (pending.current !== null) clearTimeout(pending.current);
    pending.current = null;
    // Keyboard activation does not generate a double-click.
    if (event.detail === 0) action();
    else if (event.detail === 1) pending.current = setTimeout(() => {
      pending.current = null;
      action();
    }, 300);
  }, []);
}
