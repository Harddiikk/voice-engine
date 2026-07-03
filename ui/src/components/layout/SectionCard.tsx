import type { ReactNode } from "react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * The standard content card — `rounded-2xl border-border/60 bg-card` with the
 * shared card shadow. This exact markup was retyped on nearly every page
 * (Settings, Campaigns, Integrations…); centralising it keeps cards identical.
 * Pass `title`/`description` for the standard header, or omit both and supply a
 * fully custom body via `children`.
 */
export function SectionCard({
  title,
  description,
  actions,
  className,
  contentClassName,
  children,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
  contentClassName?: string;
  children: ReactNode;
}) {
  const hasHeader = title || description || actions;
  return (
    <Card
      className={cn(
        "rounded-2xl border-border/60 bg-card shadow-[var(--shadow-card)] transition-all duration-200",
        className,
      )}
    >
      {hasHeader && (
        <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
          <div className="grid gap-1.5">
            {title && <CardTitle className="text-h3">{title}</CardTitle>}
            {description && (
              <CardDescription className="text-body">{description}</CardDescription>
            )}
          </div>
          {actions && <div className="shrink-0">{actions}</div>}
        </CardHeader>
      )}
      <CardContent className={contentClassName}>{children}</CardContent>
    </Card>
  );
}
