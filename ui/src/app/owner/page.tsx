/* Hallmark · component-scope: owner console · genre: modern-minimal
 * Extends the existing app system (PageShell / PageHeader / Card / shadcn
 * tokens) rather than introducing a macrostructure — this is an internal
 * console inside an established product, not a standalone page.
 * pre-emit critique: P4 H5 E4 S5 R5 V4
 */
"use client";

import { AlertTriangle, Loader2, PauseCircle, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/layout/PageHeader";
import { PageShell } from "@/components/layout/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
    getPlatformOverview,
    type PlatformOverviewResult,
} from "@/lib/adminClients";
import { useAuth } from "@/lib/auth";

type Period = "day" | "week" | "month";

const PERIODS: { value: Period; label: string }[] = [
    { value: "day", label: "30 days" },
    { value: "week", label: "12 weeks" },
    { value: "month", label: "12 months" },
];

function formatMinutes(minutes: number): string {
    if (minutes < 60) return `${Math.round(minutes)}m`;
    const hours = minutes / 60;
    return hours < 100 ? `${hours.toFixed(1)}h` : `${Math.round(hours)}h`;
}

function formatInr(value: number): string {
    return `₹${value.toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

/** Balance phrased in minutes; null means unmetered, which is not a number. */
function formatBalance(seconds: number | null | undefined, unmetered: boolean) {
    if (unmetered) return "Unlimited";
    const mins = Math.max(0, Math.round((seconds ?? 0) / 60));
    return `${mins} min`;
}

function Stat({
    label,
    value,
    hint,
    tone = "default",
}: {
    label: string;
    value: string;
    hint?: string;
    tone?: "default" | "warn";
}) {
    return (
        <Card>
            <CardContent className="p-4">
                <p className="text-xs uppercase tracking-wide text-muted-foreground">
                    {label}
                </p>
                <p
                    className={
                        tone === "warn"
                            ? "mt-1 text-2xl font-semibold tabular-nums text-amber-600 dark:text-amber-500"
                            : "mt-1 text-2xl font-semibold tabular-nums"
                    }
                >
                    {value}
                </p>
                {hint ? (
                    <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
                ) : null}
            </CardContent>
        </Card>
    );
}

export default function OwnerConsolePage() {
    const { user, getAccessToken, loading: authLoading } = useAuth();
    const [data, setData] = useState<PlatformOverviewResult | null>(null);
    const [period, setPeriod] = useState<Period>("month");
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [activeTag, setActiveTag] = useState<string | null>(null);
    const hasFetched = useRef(false);

    const load = useCallback(
        async (nextPeriod: Period, showSpinner = false) => {
            if (showSpinner) setRefreshing(true);
            try {
                const token = await getAccessToken();
                if (!token) throw new Error("Missing access token");
                setData(await getPlatformOverview(token, nextPeriod));
            } catch (err) {
                toast.error(
                    err instanceof Error
                        ? err.message
                        : "Failed to load the platform overview",
                );
            } finally {
                setLoading(false);
                if (showSpinner) setRefreshing(false);
            }
        },
        [getAccessToken],
    );

    // The auth interceptor only attaches the Bearer token once auth is loaded,
    // so fetching earlier would silently 401.
    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        load(period);
    }, [authLoading, user, load, period]);

    const handlePeriod = (next: Period) => {
        setPeriod(next);
        load(next, true);
    };

    const clients = useMemo(() => {
        if (!data) return [];
        if (!activeTag) return data.clients;
        return data.clients.filter((c) => c.tags.includes(activeTag));
    }, [data, activeTag]);

    if (loading) {
        return (
            <PageShell>
                <div className="flex items-center gap-2 py-16 text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Loading platform overview…
                </div>
            </PageShell>
        );
    }

    const totals = data?.totals;

    return (
        <PageShell>
            <PageHeader
                title="Owner console"
                subtitle="Every client on the platform, in one view."
                actions={
                    <div className="flex items-center gap-2">
                        <div className="flex rounded-md border">
                            {PERIODS.map((p) => (
                                <button
                                    key={p.value}
                                    type="button"
                                    onClick={() => handlePeriod(p.value)}
                                    aria-pressed={period === p.value}
                                    className={
                                        period === p.value
                                            ? "bg-muted px-3 py-1.5 text-xs font-medium first:rounded-l-md last:rounded-r-md"
                                            : "px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:text-foreground first:rounded-l-md last:rounded-r-md"
                                    }
                                >
                                    {p.label}
                                </button>
                            ))}
                        </div>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={() => load(period, true)}
                            disabled={refreshing}
                        >
                            <RefreshCw
                                className={
                                    refreshing
                                        ? "mr-2 h-3.5 w-3.5 animate-spin"
                                        : "mr-2 h-3.5 w-3.5"
                                }
                            />
                            Refresh
                        </Button>
                    </div>
                }
            />

            {totals ? (
                <>
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                        <Stat
                            label="Clients"
                            value={String(totals.clients)}
                            hint={`${totals.active_clients} active · ${totals.idle_clients} idle`}
                        />
                        <Stat
                            label="Calls"
                            value={totals.total_calls.toLocaleString("en-IN")}
                            hint={`${totals.success_rate}% connected`}
                        />
                        <Stat
                            label="Talk time"
                            value={formatMinutes(totals.total_minutes)}
                        />
                        <Stat
                            label="Revenue"
                            value={formatInr(totals.revenue_inr)}
                            hint="Client spend in this window"
                        />
                    </div>

                    {/* Attention row: the two states worth acting on today. */}
                    {(totals.low_balance_clients > 0 ||
                        totals.suspended_clients > 0) && (
                        <div className="mt-3 grid gap-3 sm:grid-cols-2">
                            {totals.low_balance_clients > 0 && (
                                <Card className="border-amber-500/40">
                                    <CardContent className="flex items-center gap-3 p-4">
                                        <AlertTriangle className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-500" />
                                        <p className="text-sm">
                                            <span className="font-semibold tabular-nums">
                                                {totals.low_balance_clients}
                                            </span>{" "}
                                            {totals.low_balance_clients === 1
                                                ? "client is"
                                                : "clients are"}{" "}
                                            running low on credits
                                        </p>
                                    </CardContent>
                                </Card>
                            )}
                            {totals.suspended_clients > 0 && (
                                <Card>
                                    <CardContent className="flex items-center gap-3 p-4">
                                        <PauseCircle className="h-4 w-4 shrink-0 text-muted-foreground" />
                                        <p className="text-sm">
                                            <span className="font-semibold tabular-nums">
                                                {totals.suspended_clients}
                                            </span>{" "}
                                            suspended
                                        </p>
                                    </CardContent>
                                </Card>
                            )}
                        </div>
                    )}
                </>
            ) : null}

            {/* Tag filter — the segmentation control. */}
            {data && data.tags.length > 0 && (
                <div className="mt-6 flex flex-wrap items-center gap-2">
                    <button
                        type="button"
                        onClick={() => setActiveTag(null)}
                        aria-pressed={activeTag === null}
                    >
                        <Badge variant={activeTag === null ? "default" : "outline"}>
                            All {data.clients.length}
                        </Badge>
                    </button>
                    {data.tags.map((t) => (
                        <button
                            key={t.tag}
                            type="button"
                            onClick={() =>
                                setActiveTag(activeTag === t.tag ? null : t.tag)
                            }
                            aria-pressed={activeTag === t.tag}
                        >
                            <Badge
                                variant={activeTag === t.tag ? "default" : "outline"}
                            >
                                {t.tag} {t.clients}
                            </Badge>
                        </button>
                    ))}
                </div>
            )}

            <Card className="mt-4">
                <CardHeader className="pb-2">
                    <CardTitle className="text-sm font-medium">
                        Clients{activeTag ? ` · ${activeTag}` : ""}
                    </CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                    {clients.length === 0 ? (
                        <p className="px-4 py-8 text-center text-sm text-muted-foreground">
                            {activeTag
                                ? `No clients tagged “${activeTag}”.`
                                : "No clients yet."}
                        </p>
                    ) : (
                        /* Wide table scrolls inside its own container so the page
                           body never scrolls horizontally on mobile. */
                        <div className="overflow-x-auto">
                            <table className="w-full min-w-[46rem] text-sm">
                                <thead>
                                    <tr className="border-b text-left text-xs uppercase tracking-wide text-muted-foreground">
                                        <th className="px-4 py-2 font-medium">Client</th>
                                        <th className="px-4 py-2 text-right font-medium">Calls</th>
                                        <th className="px-4 py-2 text-right font-medium">Talk time</th>
                                        <th className="px-4 py-2 text-right font-medium">Spend</th>
                                        <th className="px-4 py-2 text-right font-medium">Balance</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {clients.map((c) => {
                                        const low =
                                            !c.unmetered &&
                                            (c.credits_seconds_remaining ?? 0) <= 1800;
                                        return (
                                            <tr
                                                key={c.organization_id}
                                                className="border-b last:border-0"
                                            >
                                                <td className="px-4 py-2.5">
                                                    <div className="flex flex-wrap items-center gap-1.5">
                                                        <Link
                                                            href={`/clients/${c.organization_id}`}
                                                            className="font-medium hover:underline"
                                                        >
                                                            {c.organization_name}
                                                        </Link>
                                                        {c.suspended && (
                                                            <Badge variant="outline">
                                                                suspended
                                                            </Badge>
                                                        )}
                                                        {c.tags.map((tag) => (
                                                            <Badge
                                                                key={tag}
                                                                variant="secondary"
                                                            >
                                                                {tag}
                                                            </Badge>
                                                        ))}
                                                    </div>
                                                </td>
                                                <td className="px-4 py-2.5 text-right tabular-nums">
                                                    {c.calls.toLocaleString("en-IN")}
                                                </td>
                                                <td className="px-4 py-2.5 text-right tabular-nums">
                                                    {formatMinutes(c.minutes)}
                                                </td>
                                                <td className="px-4 py-2.5 text-right tabular-nums">
                                                    {formatInr(c.money_spent_inr)}
                                                </td>
                                                <td
                                                    className={
                                                        low
                                                            ? "px-4 py-2.5 text-right tabular-nums text-amber-600 dark:text-amber-500"
                                                            : "px-4 py-2.5 text-right tabular-nums"
                                                    }
                                                >
                                                    {formatBalance(
                                                        c.credits_seconds_remaining,
                                                        c.unmetered,
                                                    )}
                                                </td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </CardContent>
            </Card>
        </PageShell>
    );
}
