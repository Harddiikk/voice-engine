import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * Deliberate width scale for top-level pages. Before this existed every page
 * hand-rolled its own `max-w-*` (2xl / 3xl / 4xl / 6xl / none) and its own
 * padding (`py-8`/`py-10`/`py-12`/`p-6`), which is the single biggest driver of
 * the "inconsistent / unfinished" feel. Pick a tier by content, not per page:
 *
 *  - `narrow`  — single-column forms & settings (Settings, Integrations, Credits)
 *  - `default` — dashboards / mixed content (Home)
 *  - `wide`    — tables, lists, multi-column tools (Campaigns, Analytics, Tools)
 *  - `full`    — the page manages its own width (builders, canvases)
 */
const WIDTHS = {
  narrow: "max-w-2xl",
  default: "max-w-4xl",
  // Data-dense pages (dashboards, tables, analytics) fill more of the screen —
  // 1536px reclaims the wasted horizontal margin on wide monitors while staying
  // readable. Forms stay narrow/default on purpose (line length).
  wide: "max-w-[96rem]",
  full: "max-w-none",
} as const;

export type PageWidth = keyof typeof WIDTHS;

export function PageShell({
  width = "default",
  stagger = true,
  className,
  children,
}: {
  width?: PageWidth;
  /** Fade-up reveal on the shell's direct children (first 8). */
  stagger?: boolean;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className="px-4 py-10 sm:px-6">
      <div
        className={cn(
          "mx-auto w-full space-y-6",
          stagger && "stagger",
          WIDTHS[width],
          className,
        )}
      >
        {children}
      </div>
    </div>
  );
}
