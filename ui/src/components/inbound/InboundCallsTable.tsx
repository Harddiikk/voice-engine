'use client';

import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';

import { getUsageHistoryApiV1OrganizationsUsageRunsGet } from '@/client/sdk.gen';
import type { UsageHistoryResponse, WorkflowRunUsageResponse } from '@/client/types.gen';
import { MediaPreviewButton, MediaPreviewDialog } from '@/components/MediaPreviewDialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from '@/components/ui/table';
import { useAuth } from '@/lib/auth';

// Server-side call-direction filter (see api/db/filters.py::ATTRIBUTE_FIELD_MAPPING).
const INBOUND_FILTER = JSON.stringify([
    { attribute: 'callType', type: 'text', value: { value: 'inbound' } },
]);

function formatDuration(seconds: number): string {
    const s = Math.floor(seconds);
    const minutes = Math.floor(s / 60);
    const rem = s % 60;
    if (minutes === 0) return `${rem}s`;
    if (rem === 0) return `${minutes}m`;
    return `${minutes}m ${rem}s`;
}

function formatDateTime(iso: string): string {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '-';
    return d.toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: true,
    });
}

export function InboundCallsTable() {
    const router = useRouter();
    const auth = useAuth();
    const [history, setHistory] = useState<UsageHistoryResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [page, setPage] = useState(1);

    const mediaPreview = MediaPreviewDialog();

    const fetchPage = useCallback(async (targetPage: number) => {
        if (!auth.isAuthenticated) return;
        setLoading(true);
        try {
            const res = await getUsageHistoryApiV1OrganizationsUsageRunsGet({
                query: { page: targetPage, limit: 25, filters: INBOUND_FILTER },
            });
            if (res.data) setHistory(res.data);
        } catch (error) {
            console.error('Failed to fetch inbound calls:', error);
        } finally {
            setLoading(false);
        }
    }, [auth.isAuthenticated]);

    useEffect(() => {
        if (auth.isAuthenticated) fetchPage(page);
    }, [auth.isAuthenticated, page, fetchPage]);

    const handleRowClick = (run: WorkflowRunUsageResponse) => {
        router.push(`/workflow/${run.workflow_id}/run/${run.id}`);
    };

    return (
        <Card>
            <CardHeader>
                <CardTitle>Inbound calls</CardTitle>
                <CardDescription>
                    Calls that came in to your numbers and were routed to an agent.
                </CardDescription>
            </CardHeader>
            <CardContent>
                {loading && !history ? (
                    <div className="space-y-3">
                        {Array.from({ length: 5 }).map((_, i) => (
                            <Skeleton key={i} className="h-12 w-full" />
                        ))}
                    </div>
                ) : history && history.runs.length > 0 ? (
                    <>
                        <div className="overflow-hidden rounded-lg border">
                            <Table>
                                <TableHeader>
                                    <TableRow className="bg-muted/50">
                                        <TableHead className="font-semibold">Run ID</TableHead>
                                        <TableHead className="font-semibold">Agent</TableHead>
                                        <TableHead className="font-semibold">Caller</TableHead>
                                        <TableHead className="font-semibold">Disposition</TableHead>
                                        <TableHead className="font-semibold">Date</TableHead>
                                        <TableHead className="font-semibold text-right">Duration</TableHead>
                                        <TableHead className="font-semibold">Recording</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {history.runs.map((run) => (
                                        <TableRow key={run.id}>
                                            <TableCell
                                                className="cursor-pointer font-mono text-sm hover:underline"
                                                onClick={() => handleRowClick(run)}
                                            >
                                                #{run.id}
                                            </TableCell>
                                            <TableCell>{run.workflow_name || 'Unknown'}</TableCell>
                                            <TableCell className="font-mono text-sm">
                                                {run.caller_number || run.phone_number || '-'}
                                            </TableCell>
                                            <TableCell>
                                                {run.disposition ? (
                                                    <Badge variant="default">{run.disposition}</Badge>
                                                ) : (
                                                    <span className="text-sm text-muted-foreground">-</span>
                                                )}
                                            </TableCell>
                                            <TableCell className="text-sm">
                                                {formatDateTime(run.created_at)}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">
                                                {formatDuration(run.call_duration_seconds)}
                                            </TableCell>
                                            <TableCell>
                                                <MediaPreviewButton
                                                    recordingUrl={run.recording_url}
                                                    transcriptUrl={run.transcript_url}
                                                    runId={run.id}
                                                    onOpenPreview={mediaPreview.openPreview}
                                                />
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>

                        {history.total_pages > 1 && (
                            <div className="mt-6 flex items-center justify-between">
                                <p className="text-sm text-muted-foreground">
                                    Page {history.page} of {history.total_pages} ({history.total_count} total)
                                </p>
                                <div className="flex gap-2">
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => setPage((p) => Math.max(1, p - 1))}
                                        disabled={page === 1 || loading}
                                    >
                                        <ChevronLeft className="h-4 w-4" />
                                        Previous
                                    </Button>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => setPage((p) => p + 1)}
                                        disabled={page >= history.total_pages || loading}
                                    >
                                        Next
                                        <ChevronRight className="h-4 w-4" />
                                    </Button>
                                </div>
                            </div>
                        )}
                    </>
                ) : (
                    <div className="py-10 text-center">
                        <p className="text-small text-muted-foreground">No inbound calls yet.</p>
                        <p className="mt-1 text-small text-muted-foreground">
                            Inbound calls appear here once a number with an assigned agent receives one.
                        </p>
                    </div>
                )}
                {mediaPreview.dialog}
            </CardContent>
        </Card>
    );
}
