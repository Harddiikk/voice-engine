'use client';

import { ExternalLink, Info, Pencil, PhoneIncoming, Plus } from 'lucide-react';
import Link from 'next/link';
import { useCallback, useEffect, useState } from 'react';

import {
    listPhoneNumbersApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersGet,
    listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet,
} from '@/client/sdk.gen';
import type {
    PhoneNumberResponse,
    TelephonyConfigurationListItem,
} from '@/client/types.gen';
import { InboundCallsTable } from '@/components/inbound/InboundCallsTable';
import { PageHeader } from '@/components/layout/PageHeader';
import { PageShell } from '@/components/layout/PageShell';
import { PhoneNumberDialog } from '@/components/telephony/PhoneNumberDialog';
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
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';

interface InboundRow {
    config: TelephonyConfigurationListItem;
    number: PhoneNumberResponse;
}

export default function InboundPage() {
    const { user, getAccessToken, loading: authLoading } = useAuth();
    const [rows, setRows] = useState<InboundRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const [assignTarget, setAssignTarget] = useState<InboundRow | null>(null);
    const [dialogOpen, setDialogOpen] = useState(false);

    const fetchAll = useCallback(async () => {
        if (authLoading || !user) return;
        setLoading(true);
        setError(null);
        try {
            const token = await getAccessToken();
            const authHeader = { Authorization: `Bearer ${token}` };
            const cfgRes = await listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet({
                headers: authHeader,
            });
            if (cfgRes.error) throw new Error(detailFromError(cfgRes.error, 'Failed to load telephony configs'));
            const configs = cfgRes.data?.configurations ?? [];

            const numbersByConfig = await Promise.all(
                configs.map(async (config) => {
                    const res = await listPhoneNumbersApiV1OrganizationsTelephonyConfigsConfigIdPhoneNumbersGet({
                        headers: authHeader,
                        path: { config_id: config.id },
                    });
                    return (res.data?.phone_numbers ?? []).map((number) => ({ config, number }));
                }),
            );
            setRows(numbersByConfig.flat());
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load inbound configuration');
        } finally {
            setLoading(false);
        }
    }, [authLoading, user, getAccessToken]);

    useEffect(() => {
        fetchAll();
    }, [fetchAll]);

    const openAssign = (row: InboundRow) => {
        setAssignTarget(row);
        setDialogOpen(true);
    };

    return (
        <PageShell width="wide">
            <PageHeader
                eyebrow="Telephony"
                title="Inbound"
                subtitle="Route incoming calls to a voice agent and review inbound call history."
            />

            {/* Honest activation note */}
            <Card className="border-primary/30 bg-accent/30">
                <CardContent className="flex items-start gap-3 py-4">
                    <Info className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
                    <p className="text-small text-muted-foreground">
                        Inbound routing sends a call to the agent assigned to the number it came in on.
                        For VoiceLink numbers, point your VoiceLink inbound webhook at this platform to
                        activate it — until that webhook is configured, inbound calls will not reach an
                        agent here.
                    </p>
                </CardContent>
            </Card>

            {/* Section A — inbound agents */}
            <Card>
                <CardHeader>
                    <CardTitle>Inbound agents</CardTitle>
                    <CardDescription>
                        Each phone number and the agent that answers calls to it.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    {error ? (
                        <p className="py-8 text-center text-small text-destructive">{error}</p>
                    ) : loading ? (
                        <div className="space-y-3">
                            {Array.from({ length: 4 }).map((_, i) => (
                                <Skeleton key={i} className="h-12 w-full" />
                            ))}
                        </div>
                    ) : rows.length === 0 ? (
                        <div className="py-10 text-center">
                            <PhoneIncoming className="mx-auto mb-2 h-6 w-6 text-muted-foreground/50" aria-hidden />
                            <p className="text-small text-muted-foreground">No phone numbers yet.</p>
                            <Button asChild variant="outline" size="sm" className="mt-3">
                                <Link href="/telephony-configurations">
                                    <Plus className="mr-2 h-4 w-4" /> Add a number under Telephony
                                </Link>
                            </Button>
                        </div>
                    ) : (
                        <div className="overflow-hidden rounded-lg border">
                            <Table>
                                <TableHeader>
                                    <TableRow className="bg-muted/50">
                                        <TableHead className="font-semibold">Number</TableHead>
                                        <TableHead className="font-semibold">Configuration</TableHead>
                                        <TableHead className="font-semibold">Inbound agent</TableHead>
                                        <TableHead className="font-semibold text-right">Actions</TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {rows.map(({ config, number }) => (
                                        <TableRow key={`${config.id}-${number.id}`}>
                                            <TableCell className="font-mono text-sm">
                                                {number.address}
                                                {!number.is_active && (
                                                    <Badge variant="outline" className="ml-2">
                                                        Inactive
                                                    </Badge>
                                                )}
                                            </TableCell>
                                            <TableCell>
                                                <Link
                                                    href={`/telephony-configurations/${config.id}`}
                                                    className="inline-flex items-center gap-1.5 hover:underline"
                                                >
                                                    <span className="truncate">{config.name}</span>
                                                    <Badge variant="secondary">{config.provider}</Badge>
                                                </Link>
                                            </TableCell>
                                            <TableCell>
                                                {number.inbound_workflow_id ? (
                                                    <Link
                                                        href={`/workflow/${number.inbound_workflow_id}`}
                                                        className="inline-flex items-center gap-1 hover:text-foreground hover:underline"
                                                    >
                                                        <span>#{number.inbound_workflow_id}</span>
                                                        {number.inbound_workflow_name && (
                                                            <span
                                                                className="truncate max-w-[180px]"
                                                                title={number.inbound_workflow_name}
                                                            >
                                                                {number.inbound_workflow_name}
                                                            </span>
                                                        )}
                                                    </Link>
                                                ) : (
                                                    <span className="text-sm text-muted-foreground">Not assigned</span>
                                                )}
                                            </TableCell>
                                            <TableCell className="text-right">
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    onClick={() => openAssign({ config, number })}
                                                >
                                                    <Pencil className="mr-1.5 h-4 w-4" />
                                                    {number.inbound_workflow_id ? 'Change' : 'Assign'}
                                                </Button>
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                    <p className="mt-4 text-small text-muted-foreground">
                        Manage numbers and credentials in{' '}
                        <Link
                            href="/telephony-configurations"
                            className="inline-flex items-center gap-0.5 underline hover:text-foreground"
                        >
                            Telephony <ExternalLink className="h-3 w-3" />
                        </Link>
                        .
                    </p>
                </CardContent>
            </Card>

            {/* Section B — inbound calls */}
            <InboundCallsTable />

            {assignTarget && (
                <PhoneNumberDialog
                    open={dialogOpen}
                    onOpenChange={setDialogOpen}
                    configId={assignTarget.config.id}
                    existing={assignTarget.number}
                    onSaved={fetchAll}
                />
            )}
        </PageShell>
    );
}
