import { useMemo, useState, type ReactNode } from "react";

import { searchBindings } from "./graphFocus";
import type { GraphNodeVM } from "./types";

export function GraphFocusBar({
  nodes,
  anchorLabel,
  onFocus,
  viewControls,
}: {
  nodes: readonly GraphNodeVM[];
  anchorLabel: string | null;
  onFocus: (id: string) => void;
  viewControls: ReactNode;
}) {
  const [query, setQuery] = useState("");
  const [searchActive, setSearchActive] = useState(false);
  const results = useMemo(() => searchBindings(nodes, query, 8), [nodes, query]);
  const showResults = searchActive && query.trim() !== "";

  function choose(id: string) {
    onFocus(id);
    setQuery("");
    setSearchActive(false);
  }

  return (
    <div className="pfig-focus-bar">
      {viewControls}
      <form
        role="search"
        aria-label="Binding search"
        onFocus={() => setSearchActive(true)}
        onBlur={(e) => {
          if (!e.currentTarget.contains(e.relatedTarget)) setSearchActive(false);
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            setQuery("");
            setSearchActive(false);
          }
        }}
        onSubmit={(e) => {
          e.preventDefault();
          if (results.length > 0) choose(results[0].id);
        }}
      >
        <input
          type="search"
          aria-label="Find a binding"
          placeholder="Find a binding…"
          autoComplete="off"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setSearchActive(true);
          }}
        />
        {showResults ? (
          <ul className="pfig-focus-results" aria-label="Matching bindings">
            {results.length === 0 ? <li className="pfig-focus-no-results">No matching bindings</li> : results.map((node) => (
              <li key={node.id}>
                <button type="button" onClick={() => choose(node.id)}>
                  {node.label}
                </button>
              </li>
            ))}
          </ul>
        ) : null}
      </form>
      {anchorLabel !== null ? (
        <span className="pfig-focus-anchor" title={anchorLabel} role="status">Solo: <code>{anchorLabel}</code></span>
      ) : null}
    </div>
  );
}
