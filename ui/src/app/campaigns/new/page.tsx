"use client";

import { ArrowLeft, ChevronDown, ChevronRight } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import type { ITimezoneOption } from 'react-timezone-select';
import { toast } from 'sonner';

import {
    createCampaignApiV1CampaignCreatePost,
    getCampaignDefaultsApiV1OrganizationsCampaignDefaultsGet,
    getWorkflowsSummaryApiV1WorkflowSummaryGet,
    listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet
} from '@/client/sdk.gen';
import type { CreateCampaignRequest, TelephonyConfigurationListItem, WorkflowSummaryResponse } from '@/client/types.gen';
import { PageHeader } from '@/components/layout/PageHeader';
import { PageShell } from '@/components/layout/PageShell';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import { useAuth } from '@/lib/auth';

import CampaignAdvancedSettings, { getTimezoneValue, type TimeSlot } from '../CampaignAdvancedSettings';
import CsvColumnMapping from '../CsvColumnMapping';
import CsvUploadSelector from '../CsvUploadSelector';

export default function NewCampaignPage() {
    const { user, getAccessToken, redirectToLogin, loading } = useAuth();
    const router = useRouter();

    // Form state
    const [campaignName, setCampaignName] = useState('');
    const [selectedWorkflowId, setSelectedWorkflowId] = useState<string>('');
    // CSV is the only data source; the Dropbox-style uploader below replaces the
    // former single-option "Data Source Type" dropdown.
    const sourceType = 'csv' as const;
    const [sourceId, setSourceId] = useState('');
    const [selectedFileName, setSelectedFileName] = useState('');
    const [defaultCountryCode, setDefaultCountryCode] = useState<string>('+91');
    const [columnMapping, setColumnMapping] = useState<Record<string, string>>({});
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [createError, setCreateError] = useState<string | null>(null);

    // Workflows state
    const [workflows, setWorkflows] = useState<WorkflowSummaryResponse[]>([]);
    const [isLoadingWorkflows, setIsLoadingWorkflows] = useState(true);

    // Telephony configurations state
    const [telephonyConfigs, setTelephonyConfigs] = useState<TelephonyConfigurationListItem[]>([]);
    const [selectedTelephonyConfigId, setSelectedTelephonyConfigId] = useState<string>('');
    const [isLoadingTelephonyConfigs, setIsLoadingTelephonyConfigs] = useState(true);

    // Advanced settings state
    const [showAdvancedSettings, setShowAdvancedSettings] = useState(false);
    const [orgConcurrentLimit, setOrgConcurrentLimit] = useState<number>(2);
    const [fromNumbersCount, setFromNumbersCount] = useState<number>(0);
    const [defaultChannelCapacity, setDefaultChannelCapacity] = useState<number>(0);
    const [maxConcurrency, setMaxConcurrency] = useState<string>('');
    const [budgetMinutes, setBudgetMinutes] = useState<string>('');
    // Retry config state
    const [retryEnabled, setRetryEnabled] = useState(true);
    const [maxRetries, setMaxRetries] = useState<string>('2');
    const [retryDelaySeconds, setRetryDelaySeconds] = useState<string>('120');
    const [retryOnBusy, setRetryOnBusy] = useState(true);
    const [retryOnNoAnswer, setRetryOnNoAnswer] = useState(true);
    const [retryOnVoicemail, setRetryOnVoicemail] = useState(true);
    const [retryOnFailed, setRetryOnFailed] = useState(false);
    const [retryDelaysSeconds, setRetryDelaysSeconds] = useState("");
    // Hang up on voicemail / IVR — default ON to match the platform's
    // default-on voicemail detection (saves credits on answering machines).
    const [hangupOnVoicemail, setHangupOnVoicemail] = useState(true);
    // Schedule config state — default calling window: 09:00-21:00 IST every
    // day (mirrors the backend's DEFAULT_CAMPAIGN_CALLING_WINDOW). Restored
    // last-campaign settings (below) intentionally override these defaults.
    const [scheduleEnabled, setScheduleEnabled] = useState(true);
    const [scheduleTimezone, setScheduleTimezone] = useState<ITimezoneOption | string>('Asia/Kolkata');
    const [timeSlots, setTimeSlots] = useState<TimeSlot[]>(
        Array.from({ length: 7 }, (_, day) => ({ day_of_week: day, start_time: '09:00', end_time: '21:00' })),
    );
    // Circuit breaker config state
    const [circuitBreakerEnabled, setCircuitBreakerEnabled] = useState(true);
    const [circuitBreakerFailureThreshold, setCircuitBreakerFailureThreshold] = useState<string>('50');
    const [circuitBreakerWindowSeconds, setCircuitBreakerWindowSeconds] = useState<string>('120');
    const [circuitBreakerMinCalls, setCircuitBreakerMinCalls] = useState<string>('5');

    // Redirect if not authenticated
    useEffect(() => {
        if (!loading && !user) {
            redirectToLogin();
        }
    }, [loading, user, redirectToLogin]);

    // Fetch workflows
    const fetchWorkflows = useCallback(async () => {
        if (!user) return;
        try {
            const accessToken = await getAccessToken();
            const response = await getWorkflowsSummaryApiV1WorkflowSummaryGet({
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
                query: {
                    status: 'active',
                },
            });

            if (response.data) {
                setWorkflows(response.data);
            }
        } catch (error) {
            console.error('Failed to fetch workflows:', error);
            toast.error('Failed to load workflows');
        } finally {
            setIsLoadingWorkflows(false);
        }
    }, [user, getAccessToken]);

    // Fetch telephony configurations
    const fetchTelephonyConfigs = useCallback(async () => {
        if (!user) return;
        try {
            const accessToken = await getAccessToken();
            const response = await listTelephonyConfigurationsApiV1OrganizationsTelephonyConfigsGet({
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                const configs = response.data.configurations ?? [];
                setTelephonyConfigs(configs);
                const defaultConfig = configs.find((c) => c.is_default_outbound) ?? configs[0];
                if (defaultConfig) {
                    setSelectedTelephonyConfigId(String(defaultConfig.id));
                }
            }
        } catch (error) {
            console.error('Failed to fetch telephony configurations:', error);
            toast.error('Failed to load telephony configurations');
        } finally {
            setIsLoadingTelephonyConfigs(false);
        }
    }, [user, getAccessToken]);

    // Fetch campaign limits
    const fetchCampaignDefaults = useCallback(async () => {
        if (!user) return;
        try {
            const accessToken = await getAccessToken();
            const response = await getCampaignDefaultsApiV1OrganizationsCampaignDefaultsGet({
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.data) {
                setOrgConcurrentLimit(response.data.concurrent_call_limit);
                setFromNumbersCount(response.data.from_numbers_count);
                // channel_capacity predates the generated client types — the
                // trunk's channel count is the real concurrency bound.
                const capacity = (response.data as { channel_capacity?: number }).channel_capacity;
                if (typeof capacity === 'number') setDefaultChannelCapacity(capacity);

                const last = (response.data as { last_campaign_settings?: {
                    retry_config?: { enabled: boolean; max_retries: number; retry_delay_seconds: number; retry_on_busy: boolean; retry_on_no_answer: boolean; retry_on_voicemail: boolean; retry_on_failed?: boolean; retry_delays_seconds?: number[] | null };
                    max_concurrency?: number | null;
                    schedule_config?: { enabled: boolean; timezone: string; slots: TimeSlot[] } | null;
                    circuit_breaker?: { enabled: boolean; failure_threshold: number; window_seconds: number; min_calls_in_window: number } | null;
                    hangup_on_voicemail?: boolean | null;
                } | null }).last_campaign_settings;

                if (last) {
                    // Pre-populate from last campaign
                    if (last.retry_config) {
                        setRetryEnabled(last.retry_config.enabled);
                        setMaxRetries(String(last.retry_config.max_retries));
                        setRetryDelaySeconds(String(last.retry_config.retry_delay_seconds));
                        setRetryOnBusy(last.retry_config.retry_on_busy);
                        setRetryOnNoAnswer(last.retry_config.retry_on_no_answer);
                        setRetryOnVoicemail(last.retry_config.retry_on_voicemail);
                        setRetryOnFailed(last.retry_config.retry_on_failed ?? false);
                        setRetryDelaysSeconds((last.retry_config.retry_delays_seconds ?? []).join(", "));
                    } else {
                        const retryConfig = response.data.default_retry_config;
                        setRetryEnabled(retryConfig.enabled);
                        setMaxRetries(String(retryConfig.max_retries));
                        setRetryDelaySeconds(String(retryConfig.retry_delay_seconds));
                        setRetryOnBusy(retryConfig.retry_on_busy);
                        setRetryOnNoAnswer(retryConfig.retry_on_no_answer);
                        setRetryOnVoicemail(retryConfig.retry_on_voicemail);
                        setRetryOnFailed((retryConfig as { retry_on_failed?: boolean }).retry_on_failed ?? false);
                    }
                    if (last.max_concurrency) {
                        setMaxConcurrency(String(last.max_concurrency));
                    }
                    if (last.schedule_config) {
                        setScheduleEnabled(last.schedule_config.enabled);
                        setScheduleTimezone(last.schedule_config.timezone);
                        setTimeSlots(last.schedule_config.slots);
                    }
                    if (last.circuit_breaker) {
                        setCircuitBreakerEnabled(last.circuit_breaker.enabled);
                        setCircuitBreakerFailureThreshold(String(Math.round(last.circuit_breaker.failure_threshold * 100)));
                        setCircuitBreakerWindowSeconds(String(last.circuit_breaker.window_seconds));
                        setCircuitBreakerMinCalls(String(last.circuit_breaker.min_calls_in_window));
                    }
                    if (typeof last.hangup_on_voicemail === 'boolean') {
                        setHangupOnVoicemail(last.hangup_on_voicemail);
                    }
                } else {
                    // No previous campaign — use defaults
                    const retryConfig = response.data.default_retry_config;
                    setRetryEnabled(retryConfig.enabled);
                    setMaxRetries(String(retryConfig.max_retries));
                    setRetryDelaySeconds(String(retryConfig.retry_delay_seconds));
                    setRetryOnBusy(retryConfig.retry_on_busy);
                    setRetryOnNoAnswer(retryConfig.retry_on_no_answer);
                    setRetryOnVoicemail(retryConfig.retry_on_voicemail);
                    setRetryOnFailed((retryConfig as { retry_on_failed?: boolean }).retry_on_failed ?? false);
                }
            }
        } catch (error) {
            console.error('Failed to fetch campaign limits:', error);
        }
    }, [user, getAccessToken]);

    // Initial load
    useEffect(() => {
        if (user) {
            fetchWorkflows();
            fetchCampaignDefaults();
            fetchTelephonyConfigs();
        }
    }, [fetchWorkflows, fetchCampaignDefaults, fetchTelephonyConfigs, user]);

    const selectedTelephonyConfig = telephonyConfigs.find(
        (c) => String(c.id) === selectedTelephonyConfigId,
    );
    const availableFromNumbersCount = selectedTelephonyConfig?.phone_number_count ?? fromNumbersCount;

    // Concurrency is bounded by the trunk's CHANNEL capacity, not the number
    // count: the selected config's max_concurrent_calls (not in the generated
    // types yet), else the default config's capacity from campaign-defaults,
    // else the platform default of 5 when numbers exist.
    const selectedCapacity = (selectedTelephonyConfig as { max_concurrent_calls?: number } | undefined)
        ?.max_concurrent_calls;
    const channelCapacity = selectedCapacity
        ?? (availableFromNumbersCount > 0 ? (defaultChannelCapacity || 5) : 0);

    const effectiveLimit = channelCapacity > 0
        ? Math.min(orgConcurrentLimit, channelCapacity)
        : orgConcurrentLimit;

    // Handle form submission
    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setCreateError(null);

        if (!campaignName || !selectedWorkflowId || !sourceId || !selectedTelephonyConfigId) {
            toast.error('Please fill in all fields');
            return;
        }

        // Validate max_concurrency if provided
        const maxConcurrencyValue = maxConcurrency ? parseInt(maxConcurrency) : null;
        if (maxConcurrencyValue !== null) {
            if (isNaN(maxConcurrencyValue) || maxConcurrencyValue < 1 || maxConcurrencyValue > 100) {
                toast.error('Max concurrent calls must be between 1 and 100');
                return;
            }
            if (maxConcurrencyValue > effectiveLimit) {
                if (channelCapacity > 0 && channelCapacity < orgConcurrentLimit) {
                    toast.error(`Max concurrent calls cannot exceed ${effectiveLimit}: the selected configuration supports ${channelCapacity} concurrent calls (channels). Raise its channel capacity if your trunk has more.`);
                } else {
                    toast.error(`Max concurrent calls cannot exceed organization limit (${effectiveLimit})`);
                }
                return;
            }
        }

        setIsSubmitting(true);

        try {
            const accessToken = await getAccessToken();

            const retryConfig = {
                enabled: retryEnabled,
                max_retries: parseInt(maxRetries) || 2,
                retry_delay_seconds: parseInt(retryDelaySeconds) || 120,
                retry_on_busy: retryOnBusy,
                retry_on_no_answer: retryOnNoAnswer,
                retry_on_voicemail: retryOnVoicemail,
                retry_on_failed: retryOnFailed,
                retry_delays_seconds: retryDelaysSeconds
                    .split(",")
                    .map((s) => parseInt(s.trim(), 10))
                    .filter((n) => Number.isFinite(n) && n > 0),
            };

            // Build schedule_config if enabled
            const timezoneValue = getTimezoneValue(scheduleTimezone);
            const scheduleConfig = scheduleEnabled && timeSlots.length > 0
                ? {
                    enabled: true,
                    timezone: timezoneValue,
                    slots: timeSlots,
                }
                : undefined;

            // Build circuit_breaker config
            const circuitBreakerConfig = {
                enabled: circuitBreakerEnabled,
                failure_threshold: (parseInt(circuitBreakerFailureThreshold) || 50) / 100,
                window_seconds: parseInt(circuitBreakerWindowSeconds) || 120,
                min_calls_in_window: parseInt(circuitBreakerMinCalls) || 5,
            };


            const response = await createCampaignApiV1CampaignCreatePost({
                body: {
                    name: campaignName,
                    workflow_id: parseInt(selectedWorkflowId),
                    source_type: sourceType,
                    source_id: sourceId,
                    telephony_configuration_id: parseInt(selectedTelephonyConfigId),
                    retry_config: retryConfig,
                    max_concurrency: maxConcurrencyValue,
                    budget_minutes: budgetMinutes ? parseInt(budgetMinutes, 10) : null,
                    schedule_config: scheduleConfig,
                    circuit_breaker: circuitBreakerConfig,
                    column_mapping: Object.keys(columnMapping).length ? columnMapping : undefined,
                    default_country_code: defaultCountryCode !== 'none' ? defaultCountryCode : undefined,
                    hangup_on_voicemail: hangupOnVoicemail,
                } as unknown as CreateCampaignRequest,
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                }
            });

            if (response.error) {
                // Extract error message from API response
                const errorDetail = (response.error as { detail?: string })?.detail;
                const errorMessage = errorDetail || 'Failed to create campaign';
                setCreateError(errorMessage);
                toast.error(errorMessage);
                return;
            }

            if (response.data) {
                toast.success('Campaign created successfully');
                router.push(`/campaigns/${response.data.id}`);
            }
        } catch (error: unknown) {
            console.error('Failed to create campaign:', error);
            const errorMessage = 'Failed to create campaign';
            setCreateError(errorMessage);
            toast.error(errorMessage);
        } finally {
            setIsSubmitting(false);
        }
    };

    // Handle back navigation
    const handleBack = () => {
        router.push('/campaigns');
    };

    // Handle CSV file upload
    const handleFileUploaded = (fileKey: string, fileName: string) => {
        setSourceId(fileKey);
        setSelectedFileName(fileName);
        setCreateError(null);
    };

    return (
        <PageShell width="narrow">
            <div>
                <Button
                    variant="ghost"
                    onClick={handleBack}
                    className="mb-4 -ml-2 text-muted-foreground"
                >
                    <ArrowLeft className="mr-2 h-4 w-4" />
                    Back to Campaigns
                </Button>
                <PageHeader
                    eyebrow="New Campaign"
                    title="Create New Campaign"
                    subtitle="Set up a new campaign to execute workflows at scale"
                />
            </div>

            <Card className="rounded-2xl border border-border/60 bg-card shadow-[var(--shadow-card)] transition-all duration-200">
                    <CardHeader>
                        <CardTitle className="text-h3">Campaign Details</CardTitle>
                        <CardDescription className="text-small">
                            Configure your campaign settings
                        </CardDescription>
                    </CardHeader>
                    <CardContent>
                        <form onSubmit={handleSubmit} className="space-y-6">
                            <div className="space-y-2">
                                <Label htmlFor="campaign-name">Campaign Name</Label>
                                <Input
                                    id="campaign-name"
                                    placeholder="Enter campaign name"
                                    value={campaignName}
                                    onChange={(e) => setCampaignName(e.target.value)}
                                    maxLength={255}
                                    required
                                />
                                <p className="text-sm text-muted-foreground">
                                    Choose a descriptive name for your campaign
                                </p>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="workflow">Workflow</Label>
                                <Select
                                    value={selectedWorkflowId}
                                    onValueChange={setSelectedWorkflowId}
                                    required
                                >
                                    <SelectTrigger id="workflow">
                                        <SelectValue placeholder="Select a workflow" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {isLoadingWorkflows ? (
                                            <SelectItem value="loading" disabled>
                                                Loading workflows...
                                            </SelectItem>
                                        ) : workflows.length === 0 ? (
                                            <SelectItem value="none" disabled>
                                                No workflows found
                                            </SelectItem>
                                        ) : (
                                            workflows.map((workflow) => (
                                                <SelectItem
                                                    key={workflow.id}
                                                    value={workflow.id.toString()}
                                                >
                                                    {workflow.name} (#{workflow.id})
                                                </SelectItem>
                                            ))
                                        )}
                                    </SelectContent>
                                </Select>
                                <p className="text-sm text-muted-foreground">
                                    Select the workflow to execute for each row in the data source
                                </p>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="telephony-config">Telephony Configuration</Label>
                                {!isLoadingTelephonyConfigs && telephonyConfigs.length === 0 ? (
                                    <div className="rounded-md border border-dashed p-3 text-sm text-muted-foreground">
                                        No telephony configurations yet.{' '}
                                        <Link
                                            href="/telephony-configurations"
                                            className="underline text-foreground"
                                        >
                                            Add one
                                        </Link>{' '}
                                        to create a campaign.
                                    </div>
                                ) : (
                                    <Select
                                        value={selectedTelephonyConfigId}
                                        onValueChange={setSelectedTelephonyConfigId}
                                        required
                                    >
                                        <SelectTrigger id="telephony-config">
                                            <SelectValue placeholder="Select a telephony configuration" />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {isLoadingTelephonyConfigs ? (
                                                <SelectItem value="loading" disabled>
                                                    Loading configurations...
                                                </SelectItem>
                                            ) : (
                                                telephonyConfigs.map((config) => (
                                                    <SelectItem
                                                        key={config.id}
                                                        value={config.id.toString()}
                                                    >
                                                        {config.name} ({config.provider})
                                                        {config.is_default_outbound ? ' - default' : ''}
                                                    </SelectItem>
                                                ))
                                            )}
                                        </SelectContent>
                                    </Select>
                                )}
                                <p className="text-sm text-muted-foreground">
                                    Outbound calls for this campaign will use this configuration&apos;s caller IDs
                                </p>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="default-country-code">Default Country Calling Code</Label>
                                <Select
                                    value={defaultCountryCode}
                                    onValueChange={setDefaultCountryCode}
                                >
                                    <SelectTrigger id="default-country-code">
                                        <SelectValue placeholder="Select country calling code" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="none">None (Already has country code, e.g. +91...)</SelectItem>
                                        <SelectItem value="+91">India (+91)</SelectItem>
                                        <SelectItem value="+1">United States / Canada (+1)</SelectItem>
                                        <SelectItem value="+44">United Kingdom (+44)</SelectItem>
                                        <SelectItem value="+61">Australia (+61)</SelectItem>
                                        <SelectItem value="+971">United Arab Emirates (+971)</SelectItem>
                                        <SelectItem value="+65">Singapore (+65)</SelectItem>
                                        <SelectItem value="+966">Saudi Arabia (+966)</SelectItem>
                                        <SelectItem value="+974">Qatar (+974)</SelectItem>
                                        <SelectItem value="+965">Kuwait (+965)</SelectItem>
                                        <SelectItem value="+973">Bahrain (+973)</SelectItem>
                                        <SelectItem value="+968">Oman (+968)</SelectItem>
                                    </SelectContent>
                                </Select>
                                <p className="text-sm text-muted-foreground">
                                    Automatically prepends this country code to any uploaded phone number that does not start with &apos;+&apos;
                                </p>
                            </div>

                            <CsvUploadSelector
                                onFileUploaded={handleFileUploaded}
                                selectedFileName={selectedFileName}
                            />

                            {sourceId && (
                                <CsvColumnMapping
                                    sourceId={sourceId}
                                    workflowId={selectedWorkflowId}
                                    onChange={setColumnMapping}
                                    defaultCountryCode={defaultCountryCode}
                                />
                            )}

                            {/* Advanced Settings */}
                            <Collapsible
                                open={showAdvancedSettings}
                                onOpenChange={setShowAdvancedSettings}
                                className="rounded-xl border border-border/60"
                            >
                                <CollapsibleTrigger className="flex w-full items-center justify-between rounded-xl p-4 transition-colors duration-200 hover:bg-muted/40">
                                    <span className="text-label text-foreground">Advanced Settings</span>
                                    {showAdvancedSettings ? (
                                        <ChevronDown className="h-4 w-4" />
                                    ) : (
                                        <ChevronRight className="h-4 w-4" />
                                    )}
                                </CollapsibleTrigger>
                                <CollapsibleContent className="px-4 pb-4">
                                    <CampaignAdvancedSettings
                                        maxConcurrency={maxConcurrency}
                                        budgetMinutes={budgetMinutes}
                                        onBudgetMinutesChange={setBudgetMinutes}
                                        onMaxConcurrencyChange={setMaxConcurrency}
                                        effectiveLimit={effectiveLimit}
                                        orgConcurrentLimit={orgConcurrentLimit}
                                        fromNumbersCount={fromNumbersCount}
                                        channelCapacity={channelCapacity}
                                        retryEnabled={retryEnabled}
                                        onRetryEnabledChange={setRetryEnabled}
                                        maxRetries={maxRetries}
                                        onMaxRetriesChange={setMaxRetries}
                                        retryDelaySeconds={retryDelaySeconds}
                                        onRetryDelaySecondsChange={setRetryDelaySeconds}
                                        retryOnBusy={retryOnBusy}
                                        onRetryOnBusyChange={setRetryOnBusy}
                                        retryOnNoAnswer={retryOnNoAnswer}
                                        onRetryOnNoAnswerChange={setRetryOnNoAnswer}
                                        retryOnVoicemail={retryOnVoicemail}
                                        onRetryOnVoicemailChange={setRetryOnVoicemail}
                                        retryOnFailed={retryOnFailed}
                                        onRetryOnFailedChange={setRetryOnFailed}
                                        retryDelaysSeconds={retryDelaysSeconds}
                                        onRetryDelaysSecondsChange={setRetryDelaysSeconds}
                                        hangupOnVoicemail={hangupOnVoicemail}
                                        onHangupOnVoicemailChange={setHangupOnVoicemail}
                                        scheduleEnabled={scheduleEnabled}
                                        onScheduleEnabledChange={setScheduleEnabled}
                                        scheduleTimezone={scheduleTimezone}
                                        onScheduleTimezoneChange={setScheduleTimezone}
                                        timeSlots={timeSlots}
                                        onTimeSlotsChange={setTimeSlots}
                                        circuitBreakerEnabled={circuitBreakerEnabled}
                                        onCircuitBreakerEnabledChange={setCircuitBreakerEnabled}
                                        circuitBreakerFailureThreshold={circuitBreakerFailureThreshold}
                                        onCircuitBreakerFailureThresholdChange={setCircuitBreakerFailureThreshold}
                                        circuitBreakerWindowSeconds={circuitBreakerWindowSeconds}
                                        onCircuitBreakerWindowSecondsChange={setCircuitBreakerWindowSeconds}
                                        circuitBreakerMinCalls={circuitBreakerMinCalls}
                                        onCircuitBreakerMinCallsChange={setCircuitBreakerMinCalls}
                                    />
                                </CollapsibleContent>
                            </Collapsible>

                            {createError && (
                                <div className="rounded-md bg-destructive/15 p-3 text-sm text-destructive">
                                    {createError}
                                </div>
                            )}

                            <p className="text-sm text-muted-foreground">
                                Calls are placed 09:00&ndash;21:00 IST by default &mdash; edit under Advanced Settings.
                            </p>

                            <div className="flex gap-4 pt-4">
                                <Button
                                    type="submit"
                                    disabled={isSubmitting || !campaignName || !selectedWorkflowId || !sourceId || !selectedTelephonyConfigId}
                                >
                                    {isSubmitting ? 'Creating...' : 'Create Campaign'}
                                </Button>
                                <Button
                                    type="button"
                                    variant="outline"
                                    onClick={handleBack}
                                    disabled={isSubmitting}
                                >
                                    Cancel
                                </Button>
                            </div>
                        </form>
                    </CardContent>
                </Card>
        </PageShell>
    );
}
