import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

import { PageHeader } from "@/components/layout/PageHeader";

interface Highlight {
  icon: LucideIcon;
  title: string;
  description: string;
}

/**
 * Integration-page hero: the canonical `PageHeader` (icon badge + eyebrow +
 * title + subtitle) plus an optional 3-up highlights grid. Built on PageHeader
 * so the Integrations pages share the exact same header as every other page —
 * they just add the highlights row on top.
 *
 * Render as a direct child of the page's `PageShell` so its `space-y-6` handles
 * the gap between the header, the grid, and the page's own card.
 */
export function IntegrationHero({
  icon,
  eyebrow,
  title,
  subtitle,
  highlights,
  children,
}: {
  icon: LucideIcon;
  eyebrow: string;
  title: string;
  subtitle: string;
  highlights?: Highlight[];
  children?: ReactNode;
}) {
  return (
    <>
      <PageHeader
        icon={icon}
        eyebrow={eyebrow}
        title={title}
        subtitle={subtitle}
        actions={children}
      />

      {highlights && highlights.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-3">
          {highlights.map(({ icon: HighlightIcon, title: hTitle, description }) => (
            <div
              key={hTitle}
              className="rounded-2xl border border-border/60 bg-card p-4 shadow-[var(--shadow-card)] transition-all duration-200"
            >
              <HighlightIcon className="h-5 w-5 text-primary" />
              <p className="text-label mt-3">{hTitle}</p>
              <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                {description}
              </p>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
