import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import type { FocusDirection } from "./graphFocus";

const DIRECTIONS = [
  { key: "dependencies", label: "Dependencies" },
  { key: "both", label: "Both directions" },
  { key: "dependents", label: "Dependents" },
] as const;

export function NodeToolbar({ label, active, onToggle, collapse }: {
  label: string;
  active: FocusDirection | null;
  onToggle: (direction: FocusDirection) => void;
  collapse?: ReactNode;
}) {
  const menuId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const activeLabel = DIRECTIONS.find(({ key }) => key === active)?.label;

  useEffect(() => {
    const menu = menuRef.current;
    if (!menu) return;
    const sync = () => {
      const showing = menu.matches(":popover-open");
      setOpen(showing);
      const trigger = triggerRef.current;
      if (!showing || !trigger) return;

      const anchor = trigger.getBoundingClientRect();
      const { offsetWidth: width, offsetHeight: height } = menu;
      const gap = 6;
      const below = anchor.bottom + gap;
      const above = anchor.top - gap - height;
      menu.style.left = `${Math.max(gap, Math.min(anchor.right - width, window.innerWidth - width - gap))}px`;
      menu.style.top = `${Math.max(gap, below + height <= window.innerHeight - gap ? below : above)}px`;
      const first = menu.querySelector<HTMLButtonElement>('[aria-checked="true"]') ?? menu.querySelector("button");
      first?.focus({ preventScroll: true });
    };
    menu.addEventListener("toggle", sync);
    return () => menu.removeEventListener("toggle", sync);
  }, []);

  return (
    <div className="pfig-node-toolbar nodrag nopan" onClick={(event) => event.stopPropagation()}>
      <button
        ref={triggerRef}
        className="pfig-overview-icon pfig-node-menu-trigger"
        type="button"
        aria-label={`Solo options for ${label}${activeLabel ? ` (${activeLabel} active)` : ""}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        data-solo-active={active ?? undefined}
        title={activeLabel ? `Solo: ${activeLabel}` : "Solo options"}
        {...{ popovertarget: menuId }}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") event.stopPropagation();
        }}
      >
        <span aria-hidden="true">⋯</span>
      </button>
      <div
        id={menuId}
        ref={menuRef}
        {...{ popover: "auto" }}
        className="pfig-node-menu nowheel"
        role="menu"
        aria-label={`Solo ${label}`}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") event.stopPropagation();
          if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            menuRef.current?.hidePopover();
            triggerRef.current?.focus({ preventScroll: true });
          }
          if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
          event.preventDefault();
          event.stopPropagation();
          const options = Array.from(event.currentTarget.querySelectorAll("button"));
          const current = options.indexOf(document.activeElement as HTMLButtonElement);
          const index = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1
            : (current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length;
          options[index]?.focus({ preventScroll: true });
        }}
      >
        {DIRECTIONS.map(({ key, label: direction }) => (
          <button
            key={key}
            type="button"
            role="menuitemradio"
            aria-checked={active === key}
            data-direction={key}
            onClick={() => {
              menuRef.current?.hidePopover();
              triggerRef.current?.focus({ preventScroll: true });
              onToggle(key);
            }}
          >
            <span aria-hidden="true">{active === key ? "✓" : ""}</span>
            {direction}
          </button>
        ))}
      </div>
      {collapse}
    </div>
  );
}
