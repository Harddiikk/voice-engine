import type { ReactNode } from "react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/**
 * Shared shell for a single self-serve integration/account page (Credits,
 * PayU test, …). Renders the page header and — when `cardTitle` is provided —
 * wraps the children in a titled card so simple Section components drop
 * straight in. Pages that already render their own cards (e.g. Credits) omit
 * `cardTitle`/`cardDescription` and get the header + bare children instead, so
 * the page heading isn't duplicated by the card title.
 */
export function IntegrationPage({
  eyebrow,
  title,
  subtitle,
  cardTitle,
  cardDescription,
  children,
}: {
  eyebrow: string;
  title: string;
  subtitle?: string;
  cardTitle?: string;
  cardDescription?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex min-h-full justify-center px-4 pb-16 pt-10 sm:pt-14">
      <div className="stagger w-full max-w-2xl space-y-6">
        <div>
          <p className="text-eyebrow text-primary">{eyebrow}</p>
          <h1 className="text-h1 mt-1">{title}</h1>
          {subtitle && (
            <p className="text-body mt-2 text-muted-foreground">{subtitle}</p>
          )}
        </div>
        {cardTitle ? (
          <Card>
            <CardHeader>
              <CardTitle>{cardTitle}</CardTitle>
              {cardDescription && (
                <CardDescription>{cardDescription}</CardDescription>
              )}
            </CardHeader>
            <CardContent>{children}</CardContent>
          </Card>
        ) : (
          children
        )}
      </div>
    </div>
  );
}
