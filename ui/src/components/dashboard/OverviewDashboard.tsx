'use client';

import { format, parseISO } from 'date-fns';
import {
    Activity,
    BarChart3,
    Bot,
    CheckCircle2,
    CreditCard,
    PhoneCall,
    Radio,
    Timer,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import {
    Area,
    AreaChart,
    CartesianGrid,
    Cell,
    Pie,
    PieChart,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from 'recharts';

import { client } from '@/client/client.gen';
import { StatTile } from '@/components/dashboard/StatTile';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';

type Period = 'day' | 'week' | 'month';

interface OverviewResponse {
    period: string;
    range: { start: string; end: string; timezone: string };
    totals: {
        total_minutes: number;
        total_calls: number;
        connected_calls: number;
        success_rate: number;
        active_agents: number;
        live_calls: number;
        credits_seconds_remaining: number | null;
        unlimited: boolean;
    };
    trends: Array<{ bucket: string; calls: number; minutes: number }>;
    outcomes: {
        success: number;
        failed: number;
        other: number;
        by_disposition: Array<{ disposition: string; count: number }>;
    };
}

const PERIODS: { value: Period; label: string }[] = [
    { value: 'day', label: 'Day' },
    { value: 'week', label: 'Week' },
    { value: 'month', label: 'Month' },
];

const PERIOD_NOUN: Record<Period, string> = {
    day: 'the last 30 days',
    week: 'the last 12 weeks',
    month: 'the last 12 months',
};

function formatMinutes(minutes: number): string {
    return `${minutes.toFixed(1)} min`;
}

function formatBucket(iso: string, period: Period): string {
    try {
        const d = parseISO(iso);
        if (period === 'month') return format(d, 'MMM');
        return format(d, 'MMM d');
    } catch {
        return iso;
    }
}

/** Segmented Day/Week/Month control that drives the trends window + refetch. */
function PeriodToggle({
    value,
    onChange,
    disabled,
}: {
    value: Period;
    onChange: (p: Period) => void;
    disabled?: boolean;
}) {
    return (
        <div
            role="group"
            aria-label="Trend granularity"
            className="inline-flex rounded-lg border border-border/60 bg-muted p-[3px]"
        >
            {PERIODS.map((p) => (
                <button
                    key={p.value}
                    type="button"
                    aria-pressed={value === p.value}
                    disabled={disabled}
                    onClick={() => onChange(p.value)}
                    className={cn(
                        'rounded-md px-3 py-1 text-sm font-medium transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50',
                        value === p.value
                            ? 'bg-card text-foreground shadow-[var(--shadow-card)]'
                            : 'text-muted-foreground hover:text-foreground',
                    )}
                >
                    {p.label}
                </button>
            ))}
        </div>
    );
}

function TrendTooltip({
    active,
    payload,
    label,
    unit,
    period,
}: {
    active?: boolean;
    payload?: Array<{ value: number }>;
    label?: string;
    unit: string;
    period: Period;
}) {
    if (!active || !payload || !payload[0]) return null;
    const v = payload[0].value;
    return (
        <div className="rounded-lg border border-border bg-popover px-3 py-2 shadow-[var(--shadow-pop)]">
            <p className="text-small font-medium text-foreground">
                {label ? formatBucket(label, period) : ''}
            </p>
            <p className="text-small text-muted-foreground">
                {unit === 'min' ? `${v.toFixed(1)} min` : `${v.toLocaleString()} ${unit}`}
            </p>
        </div>
    );
}

/** A single-series area chart used as one small-multiple panel. */
function TrendPanel({
    title,
    data,
    dataKey,
    color,
    unit,
    period,
    gradientId,
}: {
    title: string;
    data: OverviewResponse['trends'];
    dataKey: 'calls' | 'minutes';
    color: string;
    unit: string;
    period: Period;
    gradientId: string;
}) {
    const hasData = data.length > 0;
    return (
        <div className="rounded-xl border border-border/60 bg-background/40 p-3">
            <p className="mb-2 text-eyebrow text-muted-foreground">{title}</p>
            {hasData ? (
                <ResponsiveContainer width="100%" height={200}>
                    <AreaChart data={data} margin={{ top: 6, right: 8, left: -12, bottom: 0 }}>
                        <defs>
                            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                                <stop offset="0%" stopColor={color} stopOpacity={0.28} />
                                <stop offset="100%" stopColor={color} stopOpacity={0} />
                            </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} opacity={0.12} />
                        <XAxis
                            dataKey="bucket"
                            tickFormatter={(v: string) => formatBucket(v, period)}
                            tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }}
                            tickLine={false}
                            axisLine={false}
                            minTickGap={24}
                        />
                        <YAxis
                            width={40}
                            allowDecimals={dataKey === 'minutes'}
                            tick={{ fontSize: 11, fill: 'var(--muted-foreground)' }}
                            tickLine={false}
                            axisLine={false}
                        />
                        <Tooltip
                            content={<TrendTooltip unit={unit} period={period} />}
                            cursor={{ stroke: 'var(--border)', strokeWidth: 1 }}
                        />
                        <Area
                            type="monotone"
                            dataKey={dataKey}
                            stroke={color}
                            strokeWidth={2}
                            fill={`url(#${gradientId})`}
                            dot={false}
                            activeDot={{ r: 4, strokeWidth: 0 }}
                        />
                    </AreaChart>
                </ResponsiveContainer>
            ) : (
                <div className="flex h-[200px] flex-col items-center justify-center text-center">
                    <BarChart3 className="mb-2 h-6 w-6 text-muted-foreground/50" aria-hidden />
                    <p className="text-small text-muted-foreground">No calls yet</p>
                </div>
            )}
        </div>
    );
}

const OUTCOME_COLORS = {
    success: 'var(--success)',
    failed: 'var(--destructive)',
    other: 'var(--muted-foreground)',
} as const;

function CallOutcomes({ outcomes, successRate }: { outcomes: OverviewResponse['outcomes']; successRate: number }) {
    const total = outcomes.success + outcomes.failed + outcomes.other;
    const slices = [
        { key: 'success', label: 'Success', count: outcomes.success, color: OUTCOME_COLORS.success },
        { key: 'failed', label: 'Failed', count: outcomes.failed, color: OUTCOME_COLORS.failed },
        { key: 'other', label: 'Other', count: outcomes.other, color: OUTCOME_COLORS.other },
    ].filter((s) => s.count > 0);

    return (
        <Card>
            <CardHeader>
                <CardTitle>Call Outcomes</CardTitle>
                <CardDescription>Connected vs. failed, plus disposition breakdown</CardDescription>
            </CardHeader>
            <CardContent>
                {total === 0 ? (
                    <div className="flex h-[220px] flex-col items-center justify-center text-center">
                        <CheckCircle2 className="mb-2 h-6 w-6 text-muted-foreground/50" aria-hidden />
                        <p className="text-small text-muted-foreground">No outcomes yet</p>
                    </div>
                ) : (
                    <div className="flex flex-col gap-6 sm:flex-row sm:items-center">
                        <div className="relative mx-auto h-[168px] w-[168px] shrink-0">
                            <ResponsiveContainer width="100%" height="100%">
                                <PieChart>
                                    <Pie
                                        data={slices}
                                        dataKey="count"
                                        nameKey="label"
                                        innerRadius={58}
                                        outerRadius={80}
                                        paddingAngle={2}
                                        stroke="var(--card)"
                                        strokeWidth={2}
                                    >
                                        {slices.map((s) => (
                                            <Cell key={s.key} fill={s.color} />
                                        ))}
                                    </Pie>
                                </PieChart>
                            </ResponsiveContainer>
                            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
                                <span className="metric text-h2 leading-none tabular-nums">
                                    {successRate.toFixed(1)}%
                                </span>
                                <span className="text-[0.6875rem] uppercase tracking-wider text-muted-foreground">
                                    success
                                </span>
                            </div>
                        </div>

                        <div className="min-w-0 flex-1 space-y-3">
                            <ul className="space-y-1.5">
                                {slices.map((s) => (
                                    <li key={s.key} className="flex items-center justify-between gap-3 text-small">
                                        <span className="flex items-center gap-2">
                                            <span
                                                className="h-2.5 w-2.5 shrink-0 rounded-full"
                                                style={{ backgroundColor: s.color }}
                                                aria-hidden
                                            />
                                            <span className="text-foreground">{s.label}</span>
                                        </span>
                                        <span className="tabular-nums text-muted-foreground">
                                            {s.count.toLocaleString()} ({((s.count / total) * 100).toFixed(0)}%)
                                        </span>
                                    </li>
                                ))}
                            </ul>

                            {outcomes.by_disposition.length > 0 && (
                                <div className="border-t border-border/50 pt-3">
                                    <p className="mb-1.5 text-eyebrow text-muted-foreground">Top dispositions</p>
                                    <ul className="space-y-1">
                                        {outcomes.by_disposition.slice(0, 5).map((d) => (
                                            <li
                                                key={d.disposition}
                                                className="flex items-center justify-between gap-3 text-small"
                                            >
                                                <span className="truncate text-foreground">{d.disposition}</span>
                                                <span className="tabular-nums text-muted-foreground">
                                                    {d.count.toLocaleString()}
                                                </span>
                                            </li>
                                        ))}
                                    </ul>
                                </div>
                            )}
                        </div>
                    </div>
                )}
            </CardContent>
        </Card>
    );
}

export function OverviewDashboard({ showHeader = true }: { showHeader?: boolean }) {
    const { user, loading: authLoading } = useAuth();
    const authReady = !authLoading && !!user;

    const [period, setPeriod] = useState<Period>('month');
    const [data, setData] = useState<OverviewResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        setLoading(true);
        setError(null);
        (async () => {
            // The /overview endpoint is not yet in the generated client, so call
            // it via the raw client and type-cast the response.
            const res = await client.get({
                url: `/api/v1/organizations/overview?period=${period}`,
            });
            if (cancelled) return;
            if (res.error) {
                setError(detailFromError(res.error, 'Failed to load overview'));
                setLoading(false);
                return;
            }
            setData(res.data as OverviewResponse);
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady, period]);

    const t = data?.totals;
    const creditsValue = useMemo(() => {
        if (!t) return '—';
        if (t.unlimited) return 'Unlimited';
        if (t.credits_seconds_remaining == null) return '—';
        return `${Math.floor(t.credits_seconds_remaining / 60).toLocaleString()} min`;
    }, [t]);

    const showTileSkeleton = loading && !data;

    const tiles = [
        {
            icon: Timer,
            label: 'Total Minutes',
            value: t ? formatMinutes(t.total_minutes) : '—',
            sub: PERIOD_NOUN[period],
        },
        {
            icon: PhoneCall,
            label: 'Total Calls',
            value: t ? t.total_calls.toLocaleString() : '—',
            sub: PERIOD_NOUN[period],
        },
        {
            icon: CheckCircle2,
            label: 'Connected',
            value: t ? t.connected_calls.toLocaleString() : '—',
            sub: t ? `of ${t.total_calls.toLocaleString()} calls` : ' ',
        },
        {
            icon: Activity,
            label: 'Success Rate',
            value: t ? `${t.success_rate.toFixed(1)}%` : '—',
            sub: 'connected & completed',
        },
        {
            icon: Bot,
            label: 'Active Agents',
            value: t ? t.active_agents.toLocaleString() : '—',
            sub: 'ran a call this period',
        },
        {
            icon: Radio,
            label: 'Live Calls',
            value: t ? t.live_calls.toLocaleString() : '—',
            sub: t && t.live_calls > 0 ? 'in progress' : 'none in progress',
            live: !!t && t.live_calls > 0,
        },
        {
            icon: CreditCard,
            label: 'Credits',
            value: creditsValue,
            sub: t?.unlimited ? 'no cap' : 'remaining',
        },
    ];

    return (
        <div className="space-y-6">
            {showHeader && (
                <div>
                    <p className="text-eyebrow text-primary">Dashboard</p>
                    <h1 className="text-h1 mt-1">Overview</h1>
                    <p className="text-body mt-1 text-muted-foreground">
                        {t
                            ? `${t.success_rate.toFixed(1)}% success rate across ${t.total_calls.toLocaleString()} calls over ${PERIOD_NOUN[period]}.`
                            : 'Overview of your voice platform.'}
                    </p>
                </div>
            )}

            {error ? (
                <Card>
                    <CardContent className="py-8 text-center">
                        <p className="text-small text-destructive">{error}</p>
                    </CardContent>
                </Card>
            ) : (
                <>
                    {/* Stat tiles */}
                    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
                        {tiles.map((tile) => (
                            <StatTile
                                key={tile.label}
                                icon={tile.icon}
                                label={tile.label}
                                value={tile.value}
                                sub={tile.sub}
                                live={tile.live}
                                loading={showTileSkeleton}
                            />
                        ))}
                    </div>

                    {/* Usage Trends */}
                    <Card>
                        <CardHeader className="flex flex-row items-start justify-between gap-4 space-y-0">
                            <div className="space-y-1.5">
                                <CardTitle>Usage Trends</CardTitle>
                                <CardDescription>
                                    Calls and minutes over {PERIOD_NOUN[period]}
                                </CardDescription>
                            </div>
                            <PeriodToggle value={period} onChange={setPeriod} disabled={loading} />
                        </CardHeader>
                        <CardContent>
                            {showTileSkeleton ? (
                                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                                    <Skeleton className="h-[232px] w-full rounded-xl" />
                                    <Skeleton className="h-[232px] w-full rounded-xl" />
                                </div>
                            ) : (
                                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                                    <TrendPanel
                                        title="Calls"
                                        data={data?.trends ?? []}
                                        dataKey="calls"
                                        color="var(--chart-1)"
                                        unit="calls"
                                        period={period}
                                        gradientId="trend-calls"
                                    />
                                    <TrendPanel
                                        title="Minutes"
                                        data={data?.trends ?? []}
                                        dataKey="minutes"
                                        color="var(--chart-2)"
                                        unit="min"
                                        period={period}
                                        gradientId="trend-minutes"
                                    />
                                </div>
                            )}
                        </CardContent>
                    </Card>

                    {/* Call Outcomes */}
                    {showTileSkeleton ? (
                        <Skeleton className="h-[300px] w-full rounded-2xl" />
                    ) : (
                        data && <CallOutcomes outcomes={data.outcomes} successRate={data.totals.success_rate} />
                    )}
                </>
            )}
        </div>
    );
}
