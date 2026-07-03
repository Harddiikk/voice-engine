'use client';

import {
    ArrowDown,
    ArrowUp,
    ArrowUpDown,
    CheckCircle2,
    Hash,
    PhoneCall,
    PhoneIncoming,
    Timer,
} from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

import { client } from '@/client/client.gen';
import { StatTile } from '@/components/dashboard/StatTile';
import { EmptyState } from '@/components/layout/EmptyState';
import { SectionCard } from '@/components/layout/SectionCard';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';

interface NumberRow {
    number: string;
    label: string;
    calls: number;
    connected: number;
    success_rate: number;
    avg_duration_seconds: number;
    total_minutes: number;
    top_dispositions: Array<{ disposition: string; count: number }>;
}

interface ByNumberResponse {
    numbers: NumberRow[];
}

type CallTypeFilter = 'all' | 'inbound' | 'outbound';
type SortKey =
    | 'calls'
    | 'connected'
    | 'success_rate'
    | 'avg_duration_seconds'
    | 'total_minutes';

const RANGES: { label: string; days: number }[] = [
    { label: '7 days', days: 7 },
    { label: '30 days', days: 30 },
    { label: '90 days', days: 90 },
    { label: 'All time', days: 0 },
];

const CALL_TYPES: { label: string; value: CallTypeFilter }[] = [
    { label: 'All', value: 'all' },
    { label: 'Outbound', value: 'outbound' },
    { label: 'Inbound', value: 'inbound' },
];

function fmtDur(sec: number): string {
    const s = Math.round(sec);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const r = s % 60;
    return r ? `${m}m ${r}s` : `${m}m`;
}

function connectPct(row: NumberRow): number {
    return row.calls > 0 ? (row.connected / row.calls) * 100 : 0;
}

/** Small segmented control shared by the range + direction filters. */
function Segmented<T extends string | number>({
    options,
    value,
    onChange,
    ariaLabel,
    disabled,
}: {
    options: { label: string; value: T }[];
    value: T;
    onChange: (v: T) => void;
    ariaLabel: string;
    disabled?: boolean;
}) {
    return (
        <div
            role="group"
            aria-label={ariaLabel}
            className="inline-flex rounded-lg border border-border/60 bg-muted p-[3px]"
        >
            {options.map((o) => (
                <button
                    key={String(o.value)}
                    type="button"
                    aria-pressed={value === o.value}
                    disabled={disabled}
                    onClick={() => onChange(o.value)}
                    className={cn(
                        'rounded-md px-3 py-1 text-sm font-medium transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50',
                        value === o.value
                            ? 'bg-card text-foreground shadow-[var(--shadow-card)]'
                            : 'text-muted-foreground hover:text-foreground',
                    )}
                >
                    {o.label}
                </button>
            ))}
        </div>
    );
}

function SortHeader({
    label,
    sortKey,
    activeKey,
    dir,
    onSort,
    className,
}: {
    label: string;
    sortKey: SortKey;
    activeKey: SortKey;
    dir: 'asc' | 'desc';
    onSort: (k: SortKey) => void;
    className?: string;
}) {
    const active = activeKey === sortKey;
    const Icon = !active ? ArrowUpDown : dir === 'desc' ? ArrowDown : ArrowUp;
    return (
        <th className={cn('pb-2 font-semibold', className)}>
            <button
                type="button"
                onClick={() => onSort(sortKey)}
                className={cn(
                    'ml-auto inline-flex items-center gap-1 transition-colors hover:text-foreground',
                    active ? 'text-foreground' : 'text-muted-foreground',
                )}
            >
                {label}
                <Icon className="h-3 w-3" aria-hidden />
            </button>
        </th>
    );
}

export function ByNumberTab() {
    const { user, loading: authLoading } = useAuth();
    const authReady = !authLoading && !!user;

    const [days, setDays] = useState<number>(30);
    const [callType, setCallType] = useState<CallTypeFilter>('all');
    const [rows, setRows] = useState<NumberRow[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [sortKey, setSortKey] = useState<SortKey>('calls');
    const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
    const [selected, setSelected] = useState<Set<string>>(new Set());
    const [compareOnly, setCompareOnly] = useState(false);

    useEffect(() => {
        if (!authReady) return;
        let cancelled = false;
        setLoading(true);
        setError(null);
        (async () => {
            const params = new URLSearchParams();
            if (days > 0) {
                const start = new Date(Date.now() - days * 24 * 60 * 60 * 1000);
                params.set('start_date', start.toISOString());
            }
            if (callType !== 'all') params.set('call_type', callType);
            const qs = params.toString();
            // Endpoint is not in the generated client — call it raw and type-cast.
            const res = await client.get({
                url: `/api/v1/organizations/analytics/by-number${qs ? `?${qs}` : ''}`,
            });
            if (cancelled) return;
            if (res.error) {
                setError(detailFromError(res.error, 'Failed to load per-number analytics'));
                setLoading(false);
                return;
            }
            setRows((res.data as ByNumberResponse).numbers ?? []);
            setLoading(false);
        })();
        return () => {
            cancelled = true;
        };
    }, [authReady, days, callType]);

    const onSort = (k: SortKey) => {
        if (k === sortKey) {
            setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
        } else {
            setSortKey(k);
            setSortDir('desc');
        }
    };

    const toggleSelect = (num: string) => {
        setSelected((prev) => {
            const next = new Set(prev);
            if (next.has(num)) next.delete(num);
            else next.add(num);
            if (next.size === 0) setCompareOnly(false);
            return next;
        });
    };

    const visibleRows = useMemo(() => {
        if (!rows) return [];
        const base = compareOnly ? rows.filter((r) => selected.has(r.number)) : rows;
        const sorted = [...base].sort((a, b) => {
            const av = a[sortKey];
            const bv = b[sortKey];
            return sortDir === 'desc' ? bv - av : av - bv;
        });
        return sorted;
    }, [rows, compareOnly, selected, sortKey, sortDir]);

    // Totals across the currently visible ports (respects the compare filter).
    const totals = useMemo(() => {
        const src = visibleRows;
        const calls = src.reduce((s, r) => s + r.calls, 0);
        const connected = src.reduce((s, r) => s + r.connected, 0);
        const minutes = src.reduce((s, r) => s + r.total_minutes, 0);
        return { ports: src.length, calls, connected, minutes };
    }, [visibleRows]);

    const showSkeleton = loading && !rows;

    if (error) {
        return (
            <Card>
                <CardContent className="py-8 text-center">
                    <p className="text-small text-destructive">{error}</p>
                </CardContent>
            </Card>
        );
    }

    return (
        <div className="space-y-6">
            {/* Filters */}
            <div className="flex flex-wrap items-center gap-3">
                <Segmented
                    options={RANGES.map((r) => ({ label: r.label, value: r.days }))}
                    value={days}
                    onChange={setDays}
                    ariaLabel="Date range"
                    disabled={loading}
                />
                <Segmented
                    options={CALL_TYPES}
                    value={callType}
                    onChange={setCallType}
                    ariaLabel="Call direction"
                    disabled={loading}
                />
                {selected.size > 0 && (
                    <div className="ml-auto flex items-center gap-2">
                        <button
                            type="button"
                            onClick={() => setCompareOnly((v) => !v)}
                            aria-pressed={compareOnly}
                            className={cn(
                                'rounded-lg border px-3 py-1.5 text-sm font-medium transition-colors',
                                compareOnly
                                    ? 'border-primary bg-primary text-primary-foreground'
                                    : 'border-border/60 text-muted-foreground hover:text-foreground',
                            )}
                        >
                            Compare selected ({selected.size})
                        </button>
                        <button
                            type="button"
                            onClick={() => {
                                setSelected(new Set());
                                setCompareOnly(false);
                            }}
                            className="text-sm text-muted-foreground hover:text-foreground"
                        >
                            Clear
                        </button>
                    </div>
                )}
            </div>

            {/* Summary tiles */}
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
                <StatTile
                    icon={Hash}
                    label={compareOnly ? 'Ports (compared)' : 'Ports'}
                    value={showSkeleton ? '—' : totals.ports.toLocaleString()}
                    sub="numbers with calls"
                    loading={showSkeleton}
                />
                <StatTile
                    icon={PhoneCall}
                    label="Total Calls"
                    value={showSkeleton ? '—' : totals.calls.toLocaleString()}
                    sub="across ports"
                    loading={showSkeleton}
                />
                <StatTile
                    icon={CheckCircle2}
                    label="Connected"
                    value={showSkeleton ? '—' : totals.connected.toLocaleString()}
                    sub={
                        showSkeleton
                            ? ' '
                            : `${totals.calls > 0 ? ((totals.connected / totals.calls) * 100).toFixed(0) : 0}% connect rate`
                    }
                    loading={showSkeleton}
                />
                <StatTile
                    icon={Timer}
                    label="Total Minutes"
                    value={showSkeleton ? '—' : `${totals.minutes.toFixed(1)} min`}
                    sub="across ports"
                    loading={showSkeleton}
                />
            </div>

            {/* Table */}
            <SectionCard
                title="Performance by number"
                description="Each originating (outbound) or receiving (inbound) DID your agents used. Select rows to compare."
            >
                {showSkeleton ? (
                    <div className="space-y-2">
                        {Array.from({ length: 5 }).map((_, i) => (
                            <Skeleton key={i} className="h-10 w-full rounded-lg" />
                        ))}
                    </div>
                ) : visibleRows.length === 0 ? (
                    <EmptyState
                        icon={PhoneIncoming}
                        title="No per-number data yet"
                        description="Once your agents place or receive calls, each phone number's performance will show up here."
                    />
                ) : (
                    <div className="overflow-x-auto">
                        <table className="w-full text-sm">
                            <thead>
                                <tr className="text-eyebrow text-muted-foreground">
                                    <th className="w-8 pb-2" aria-label="Select" />
                                    <th className="pb-2 text-left font-semibold">Number</th>
                                    <SortHeader
                                        label="Calls"
                                        sortKey="calls"
                                        activeKey={sortKey}
                                        dir={sortDir}
                                        onSort={onSort}
                                        className="text-right"
                                    />
                                    <SortHeader
                                        label="Connect %"
                                        sortKey="connected"
                                        activeKey={sortKey}
                                        dir={sortDir}
                                        onSort={onSort}
                                        className="hidden text-right sm:table-cell"
                                    />
                                    <SortHeader
                                        label="Success %"
                                        sortKey="success_rate"
                                        activeKey={sortKey}
                                        dir={sortDir}
                                        onSort={onSort}
                                        className="text-right"
                                    />
                                    <SortHeader
                                        label="Avg"
                                        sortKey="avg_duration_seconds"
                                        activeKey={sortKey}
                                        dir={sortDir}
                                        onSort={onSort}
                                        className="hidden text-right sm:table-cell"
                                    />
                                    <SortHeader
                                        label="Minutes"
                                        sortKey="total_minutes"
                                        activeKey={sortKey}
                                        dir={sortDir}
                                        onSort={onSort}
                                        className="hidden text-right md:table-cell"
                                    />
                                    <th className="hidden pb-2 text-left font-semibold lg:table-cell">
                                        Top disposition
                                    </th>
                                </tr>
                            </thead>
                            <tbody className="divide-y">
                                {visibleRows.map((r) => {
                                    const isSel = selected.has(r.number);
                                    const top = r.top_dispositions[0];
                                    const labelIsNumber = r.label === r.number;
                                    return (
                                        <tr
                                            key={r.number}
                                            className={cn('group', isSel && 'bg-primary/5')}
                                        >
                                            <td className="py-2.5 pr-2 align-middle">
                                                <input
                                                    type="checkbox"
                                                    checked={isSel}
                                                    onChange={() => toggleSelect(r.number)}
                                                    aria-label={`Select ${r.label}`}
                                                    className="h-4 w-4 rounded border-border accent-primary"
                                                />
                                            </td>
                                            <td className="py-2.5 pr-3">
                                                <div className="truncate text-label">{r.label}</div>
                                                {!labelIsNumber && (
                                                    <div className="truncate text-small tabular text-muted-foreground">
                                                        {r.number}
                                                    </div>
                                                )}
                                            </td>
                                            <td className="py-2.5 text-right tabular font-medium">
                                                {r.calls.toLocaleString()}
                                            </td>
                                            <td className="hidden py-2.5 text-right tabular text-muted-foreground sm:table-cell">
                                                {connectPct(r).toFixed(0)}%
                                                <span className="ml-1 text-small">
                                                    ({r.connected.toLocaleString()})
                                                </span>
                                            </td>
                                            <td className="py-2.5 text-right tabular">
                                                <span
                                                    className={
                                                        r.success_rate > 0
                                                            ? 'text-[var(--success)]'
                                                            : 'text-muted-foreground'
                                                    }
                                                >
                                                    {r.success_rate.toFixed(1)}%
                                                </span>
                                            </td>
                                            <td className="hidden py-2.5 text-right tabular text-muted-foreground sm:table-cell">
                                                {fmtDur(r.avg_duration_seconds)}
                                            </td>
                                            <td className="hidden py-2.5 text-right tabular text-muted-foreground md:table-cell">
                                                {r.total_minutes.toLocaleString()}
                                            </td>
                                            <td className="hidden py-2.5 text-left lg:table-cell">
                                                {top ? (
                                                    <span className="text-muted-foreground">
                                                        <span className="text-foreground">
                                                            {top.disposition}
                                                        </span>{' '}
                                                        ({top.count.toLocaleString()})
                                                    </span>
                                                ) : (
                                                    <span className="text-muted-foreground">—</span>
                                                )}
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
            </SectionCard>
        </div>
    );
}
