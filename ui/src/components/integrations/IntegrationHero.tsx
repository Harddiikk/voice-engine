import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

interface Highlight {
  icon: LucideIcon;
  title: string;
  description: string;
}

/**
 * Shared hero header for the Integrations pages (WhatsApp, CRM, Phone Numbers,
 * Credits). Renders the icon badge + eyebrow + title + subtitle row and, when
 * provided, the 3-up highlights grid — extracted verbatim from the pages that
 * previously hand-rolled this block so every integration page reads the same.
 *
 * Render this as a direct child of the page's `space-y-6` container so the
 * spacing between the header, the grid, and the page's own card is preserved.
 */
export function IntegrationHero({
  icon: Icon,
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
      <div className="flex items-start gap-4">
        <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-border/60 bg-accent text-accent-foreground shadow-[var(--shadow-card)]">
          <Icon className="h-6 w-6" />
        </div>
        <div>
          <p className="text-eyebrow text-primary">{eyebrow}</p>
          <h1 className="text-h1 mt-1">{title}</h1>
          <p className="text-body mt-2 text-muted-foreground">{subtitle}</p>
        </div>
        {children ? <div className="ml-auto shrink-0">{children}</div> : null}
      </div>

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
