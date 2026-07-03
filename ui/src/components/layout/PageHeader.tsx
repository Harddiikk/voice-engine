import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

/**
 * Canonical page header: eyebrow + title + subtitle, with an optional icon
 * badge and a right-aligned actions slot. Every top-level page should render
 * exactly one of these so titles share the same size, weight, accent colour and
 * rhythm. Replaces the per-page mix of raw `text-3xl font-bold` headings and
 * `text-muted-foreground` eyebrows that made pages look like different apps.
 */
export function PageHeader({
  eyebrow,
  title,
  subtitle,
  icon: Icon,
  actions,
}: {
  eyebrow?: string;
  title: string;
  subtitle?: ReactNode;
  icon?: LucideIcon;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
      <div className="flex items-start gap-4">
        {Icon && (
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl border border-border/60 bg-accent text-accent-foreground shadow-[var(--shadow-card)]">
            <Icon className="h-6 w-6" />
          </div>
        )}
        <div className="min-w-0">
          {eyebrow && <p className="text-eyebrow text-primary">{eyebrow}</p>}
          <h1 className="text-h1 mt-1">{title}</h1>
          {subtitle && (
            <p className="text-body mt-2 max-w-2xl text-muted-foreground">
              {subtitle}
            </p>
          )}
        </div>
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}
