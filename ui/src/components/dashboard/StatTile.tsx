'use client';

import type { LucideIcon } from 'lucide-react';

import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

interface StatTileProps {
    icon: LucideIcon;
    label: string;
    /** Big value. Pass a preformatted string (e.g. "56.6 min", "42", "Unlimited"). */
    value: React.ReactNode;
    /** Optional sub-line under the value (context, delta, plan name…). */
    sub?: React.ReactNode;
    /** Renders a pulsing "LIVE" indicator next to the value when true. */
    live?: boolean;
    /** Shows a skeleton in place of the value while the source is loading. */
    loading?: boolean;
    className?: string;
}

/**
 * Compact metric tile: icon + label + big value + optional sub / LIVE dot.
 *
 * Theme-token only — no hardcoded colors. The optional LIVE dot uses the
 * success token because it signals healthy in-progress activity, not an
 * outcome. Reuses the shared Card primitive so it stays visually cohesive
 * with the rest of the app.
 */
export function StatTile({
    icon: Icon,
    label,
    value,
    sub,
    live,
    loading,
    className,
}: StatTileProps) {
    return (
        <Card className={cn('flex flex-col gap-3 p-4', className)}>
            <div className="flex items-center justify-between gap-2">
                <span className="text-eyebrow text-muted-foreground">{label}</span>
                <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-border/60 bg-muted text-muted-foreground">
                    <Icon className="h-3.5 w-3.5" aria-hidden />
                </span>
            </div>

            {loading ? (
                <Skeleton className="h-8 w-20" />
            ) : (
                <div className="flex items-center gap-2">
                    <p className="metric text-h2 leading-none tabular-nums">{value}</p>
                    {live && (
                        <span className="inline-flex items-center gap-1 text-[0.625rem] font-semibold uppercase tracking-wider text-success">
                            <span className="relative flex h-2 w-2" aria-hidden>
                                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-70" />
                                <span className="relative inline-flex h-2 w-2 rounded-full bg-success" />
                            </span>
                            Live
                        </span>
                    )}
                </div>
            )}

            <p className="min-h-[1rem] text-small text-muted-foreground">
                {loading ? <Skeleton className="h-3 w-16" /> : (sub ?? ' ')}
            </p>
        </Card>
    );
}
