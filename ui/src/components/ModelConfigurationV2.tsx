"use client";

import { ExternalLink, RefreshCw, Volume2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { client } from "@/client/client.gen";
import {
    getModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Get,
    getModelConfigurationV2DefaultsApiV1OrganizationsModelConfigurationsV2DefaultsGet,
    migrateModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2MigratePost,
    saveModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Put,
} from "@/client/sdk.gen";
import type {
    OrganizationAiModelConfigurationResponse,
    OrganizationAiModelConfigurationV2,
} from "@/client/types.gen";
import { AIModelConfigurationV2Editor, type ModelConfigurationDefaultsV2 } from "@/components/AIModelConfigurationV2Editor";
import { ServiceConfigurationForm } from "@/components/ServiceConfigurationForm";
import {
    AlertDialog,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import {
    deriveVoiceTargetFromEffective,
    deriveVoiceTargetFromV2,
    VoiceLanguagePicker,
    voicePickOptions,
} from "@/components/VoiceLanguagePicker";
import { useUserConfig } from "@/context/UserConfigContext";
import { useIsAdmin } from "@/hooks/useIsAdmin";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { getModelConfigurationV2Raw } from "@/lib/modelConfigRaw";

/**
 * Swap voice/language into a (masked) v2 configuration without touching
 * anything else. The backend restores masked secrets on save
 * (merge_ai_model_configuration_v2_secrets), so the masked api_key values
 * round-trip safely.
 */
function deepSwapVoiceLanguage(
    configuration: Record<string, unknown>,
    voice: string,
    language: string,
): OrganizationAiModelConfigurationV2 {
    const next = structuredClone(configuration);
    if (next.mode === "dograh") {
        const dograh = next.dograh as Record<string, unknown> | null | undefined;
        if (dograh) {
            dograh.voice = voice;
            if (language) dograh.language = language;
        }
        return next as unknown as OrganizationAiModelConfigurationV2;
    }
    const byok = next.byok as Record<string, unknown> | null | undefined;
    if (byok?.mode === "realtime") {
        const realtime = (byok.realtime as Record<string, unknown> | undefined)
            ?.realtime as Record<string, unknown> | undefined;
        if (realtime) {
            realtime.voice = voice;
            // Only providers that declare a language field accept one.
            if (language && "language" in realtime) realtime.language = language;
        }
    } else if (byok?.mode === "pipeline") {
        const pipeline = byok.pipeline as Record<string, unknown> | undefined;
        const tts = pipeline?.tts as Record<string, unknown> | undefined;
        const stt = pipeline?.stt as Record<string, unknown> | undefined;
        if (tts) tts.voice = voice;
        if (language && stt && "language" in stt) stt.language = language;
    }
    return next as unknown as OrganizationAiModelConfigurationV2;
}

/**
 * Trimmed non-admin Models view for v2 orgs: a Voice & Language card. All
 * provider/model/API-key surfaces stay admin-only.
 */
function ClientVoiceLanguageCard({
    defaults,
    response,
    onSave,
    onManagedSaved,
}: {
    defaults: ModelConfigurationDefaultsV2;
    response: OrganizationAiModelConfigurationResponse;
    onSave: (configuration: OrganizationAiModelConfigurationV2) => Promise<void>;
    onManagedSaved: (next: OrganizationAiModelConfigurationResponse) => Promise<void>;
}) {
    const configuration = (response.configuration ?? null) as Record<string, unknown> | null;
    const effective = (response.effective_configuration ?? null) as Record<string, unknown> | null;
    // Prefer the stored config; when there is none (a managed-Gemini org, whose
    // google_realtime config is synthesized server-side with an injected key),
    // drive the picker from the effective config so the client can still pick a
    // voice instead of hitting the "managed by administrator" dead-end.
    const storedTarget = useMemo(() => deriveVoiceTargetFromV2(configuration), [configuration]);
    const effectiveTarget = useMemo(
        () => deriveVoiceTargetFromEffective(effective),
        [effective],
    );
    const target = storedTarget ?? effectiveTarget;
    // Managed = no stored config but a resolved effective (key is injected
    // server-side; the client saves only the voice via the keyless endpoint).
    const managed = !storedTarget && !!effectiveTarget;
    const options = useMemo(() => voicePickOptions(defaults, target), [defaults, target]);

    const baseVoice = target?.baseVoice ?? "";
    const baseLanguage = target?.baseLanguage ?? "";
    const [draftVoice, setDraftVoice] = useState(baseVoice);
    const [draftLanguage, setDraftLanguage] = useState(baseLanguage);
    const [isSaving, setIsSaving] = useState(false);

    useEffect(() => {
        setDraftVoice(baseVoice);
        setDraftLanguage(baseLanguage);
    }, [baseVoice, baseLanguage]);

    if (!target) {
        return <ManagedByAdminCard />;
    }

    const isDirty = draftVoice !== baseVoice || draftLanguage !== baseLanguage;

    const handleSave = async () => {
        if (!draftVoice.trim()) {
            toast.error("Pick a voice first");
            return;
        }
        setIsSaving(true);
        try {
            if (managed) {
                // Keyless managed-voice save — stores only the chosen voice.
                const res = await client.put({
                    url: "/api/v1/organizations/model-configurations/v2/managed-voice",
                    body: { voice: draftVoice.trim() },
                });
                if (res.error) throw new Error(detailFromError(res.error, "Failed to save voice"));
                await onManagedSaved(res.data as OrganizationAiModelConfigurationResponse);
            } else if (configuration) {
                await onSave(
                    deepSwapVoiceLanguage(configuration, draftVoice.trim(), draftLanguage.trim()),
                );
            }
            toast.success("Voice saved");
        } catch (err) {
            toast.error(err instanceof Error && err.message ? err.message : "Failed to save voice");
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                    <Volume2 className="h-4 w-4" />
                    Voice &amp; Language
                </CardTitle>
                <CardDescription>
                    Choose the voice your agents speak with. Model, provider, and API-key
                    settings are managed by your administrator.
                </CardDescription>
            </CardHeader>
            <CardContent>
                <VoiceLanguagePicker
                    isRealtime={target.isRealtime}
                    provider={target.provider}
                    model={target.model}
                    voice={draftVoice}
                    language={draftLanguage}
                    voiceOptions={options.voices}
                    languageOptions={options.languages}
                    onVoiceChange={setDraftVoice}
                    onLanguageChange={setDraftLanguage}
                    disabled={isSaving}
                />
            </CardContent>
            <CardFooter className="justify-end gap-3 border-t pt-6">
                {isDirty && <span className="text-xs text-muted-foreground">Unsaved changes</span>}
                <Button onClick={handleSave} disabled={isSaving || !isDirty || !draftVoice.trim()}>
                    {isSaving ? "Saving..." : "Save Voice"}
                </Button>
            </CardFooter>
        </Card>
    );
}

function ManagedByAdminCard() {
    return (
        <Card>
            <CardHeader>
                <CardTitle className="text-base">Model setup is managed by your administrator</CardTitle>
                <CardDescription>
                    Your organization&apos;s AI model configuration is handled centrally.
                    Contact your administrator to change models or voices.
                </CardDescription>
            </CardHeader>
        </Card>
    );
}

export default function ModelConfigurationV2({
    docsUrl,
    initialAction,
}: {
    docsUrl?: string;
    initialAction?: string;
}) {
    const auth = useAuth();
    const { refreshConfig, saveUserConfig } = useUserConfig();
    const { isAdmin, isLoaded: adminLoaded } = useIsAdmin();
    const hasFetched = useRef(false);
    const hasAppliedInitialMigrationAction = useRef(false);

    const [defaults, setDefaults] = useState<ModelConfigurationDefaultsV2 | null>(null);
    const [response, setResponse] = useState<OrganizationAiModelConfigurationResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [migrating, setMigrating] = useState(false);
    const [migrationDialogOpen, setMigrationDialogOpen] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const [rawDialogOpen, setRawDialogOpen] = useState(false);
    const [rawPayload, setRawPayload] = useState<string | null>(null);
    const [rawLoading, setRawLoading] = useState(false);
    const [rawError, setRawError] = useState<string | null>(null);

    const applyResponse = (nextResponse: OrganizationAiModelConfigurationResponse) => {
        setResponse(nextResponse);
    };

    useEffect(() => {
        if (auth.loading || !auth.user || hasFetched.current) return;
        hasFetched.current = true;

        const load = async () => {
            setLoading(true);
            setError(null);
            const [defaultsResult, configResult] = await Promise.all([
                getModelConfigurationV2DefaultsApiV1OrganizationsModelConfigurationsV2DefaultsGet(),
                getModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Get(),
            ]);

            if (defaultsResult.error) {
                setError(detailFromError(defaultsResult.error, "Failed to load model configuration defaults"));
                setLoading(false);
                return;
            }
            if (configResult.error) {
                setError(detailFromError(configResult.error, "Failed to load model configuration"));
                setLoading(false);
                return;
            }

            const nextDefaults = defaultsResult.data as ModelConfigurationDefaultsV2;
            if (!nextDefaults || !configResult.data) {
                setError("Failed to load model configuration");
                setLoading(false);
                return;
            }
            setDefaults(nextDefaults);
            applyResponse(configResult.data);
            setLoading(false);
        };

        load();

    }, [auth.loading, auth.user]);

    useEffect(() => {
        if (hasAppliedInitialMigrationAction.current) return;
        if (initialAction !== "migrate_to_v2") return;
        if (loading || response?.source !== "legacy_user_v1") return;
        hasAppliedInitialMigrationAction.current = true;
        setMigrationDialogOpen(true);
    }, [initialAction, loading, response?.source]);

    const saveConfiguration = async (configuration: OrganizationAiModelConfigurationV2) => {
        if (!defaults) return;
        setError(null);
        setNotice(null);

        const result = await saveModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Put({
            body: configuration,
        });

        if (result.error) {
            throw new Error(detailFromError(result.error, "Failed to save model configuration"));
        }
        if (!result.data) {
            throw new Error("Failed to save model configuration");
        }

        applyResponse(result.data);
        await refreshConfig();
        setNotice("Model configuration saved");
    };

    const migrateConfiguration = async () => {
        if (!defaults) return;
        setMigrating(true);
        setError(null);
        setNotice(null);

        const result = await migrateModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2MigratePost();
        if (result.error) {
            setError(detailFromError(result.error, "Failed to migrate model configuration"));
        } else if (!result.data) {
            setError("Failed to migrate model configuration");
        } else {
            applyResponse(result.data);
            await refreshConfig();
            setNotice("Configuration migrated to v2");
            setMigrationDialogOpen(false);
        }
        setMigrating(false);
    };

    // Fields added on the backend after the last OpenAPI client regeneration.
    const invalidInfo = response as
        | (OrganizationAiModelConfigurationResponse & {
              configuration_invalid?: boolean;
              configuration_error?: string | null;
          })
        | null;
    const configurationInvalid = invalidInfo?.configuration_invalid === true;
    const configurationError = invalidInfo?.configuration_error ?? null;

    const openRawPayload = async () => {
        setRawDialogOpen(true);
        setRawLoading(true);
        setRawError(null);
        try {
            const token = await auth.getAccessToken();
            const result = await getModelConfigurationV2Raw(token);
            setRawPayload(JSON.stringify(result, null, 2));
        } catch (e) {
            setRawError(e instanceof Error ? e.message : "Failed to load raw payload");
        } finally {
            setRawLoading(false);
        }
    };

    const invalidConfigurationBanner = configurationInvalid ? (
        <div className="space-y-3 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            <p>
                Your saved model configuration failed validation and is being ignored
                — legacy settings are in effect: {configurationError || "unknown validation error"}
            </p>
            {isAdmin && (
                <Button type="button" variant="outline" size="sm" onClick={openRawPayload}>
                    View raw payload
                </Button>
            )}
        </div>
    ) : null;

    const rawPayloadDialog = (
        <Dialog open={rawDialogOpen} onOpenChange={setRawDialogOpen}>
            <DialogContent className="max-w-2xl">
                <DialogHeader>
                    <DialogTitle>Stored model configuration (raw)</DialogTitle>
                    <DialogDescription>
                        The payload stored for this organization, with secrets masked.
                    </DialogDescription>
                </DialogHeader>
                {rawLoading ? (
                    <Skeleton className="h-48 w-full" />
                ) : rawError ? (
                    <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                        {rawError}
                    </div>
                ) : (
                    <pre className="max-h-96 overflow-auto rounded-md bg-muted p-4 text-xs">
                        {rawPayload}
                    </pre>
                )}
            </DialogContent>
        </Dialog>
    );

    const migrationWarningDialog = (
        <AlertDialog open={migrationDialogOpen} onOpenChange={setMigrationDialogOpen}>
            <AlertDialogContent>
                <AlertDialogHeader>
                    <AlertDialogTitle>Migrate model configuration to v2?</AlertDialogTitle>
                    <AlertDialogDescription>
                        Your configurations will be migrated to v2. After migration, check your global configuration and workflow model overrides, then run a test call to make sure everything is working.
                    </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                    <AlertDialogCancel disabled={migrating}>Cancel</AlertDialogCancel>
                    <Button type="button" onClick={migrateConfiguration} disabled={migrating}>
                        {migrating ? "Migrating..." : "Migrate to v2"}
                    </Button>
                </AlertDialogFooter>
            </AlertDialogContent>
        </AlertDialog>
    );

    if (loading || !adminLoaded) {
        return (
            <div className="w-full max-w-4xl mx-auto space-y-6">
                <Skeleton className="h-10 w-80" />
                <Skeleton className="h-28 w-full" />
                <Skeleton className="h-96 w-full" />
            </div>
        );
    }

    const source = response?.source || "empty";

    // Non-admins never see provider/model/API-key surfaces: v2 orgs get a
    // voice picker that deep-swaps into the masked configuration; everything
    // else is informational.
    if (!isAdmin) {
        return (
            <div className="w-full max-w-4xl mx-auto space-y-6">
                <div>
                    <h1 className="text-h1">AI Models Configuration</h1>
                    <p className="mt-2 text-sm text-muted-foreground">
                        Voice settings for your organization&apos;s agents.{" "}
                        {docsUrl && (
                            <a href={docsUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">
                                Learn more <ExternalLink className="h-3 w-3" />
                            </a>
                        )}
                    </p>
                </div>

                {invalidConfigurationBanner}
                {error && (
                    <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                        {error}
                    </div>
                )}
                {notice && (
                    <div className="rounded-md border border-green-500/40 bg-green-500/10 px-4 py-3 text-sm text-green-700 dark:text-green-300">
                        {notice}
                    </div>
                )}

                {source === "organization_v2" && defaults && response ? (
                    <ClientVoiceLanguageCard
                        defaults={defaults}
                        response={response}
                        onSave={saveConfiguration}
                        onManagedSaved={async (next) => {
                            applyResponse(next);
                            await refreshConfig();
                            setNotice("Voice saved");
                        }}
                    />
                ) : (
                    <ManagedByAdminCard />
                )}
            </div>
        );
    }

    if (source !== "organization_v2") {
        return (
            <div className="w-full max-w-4xl mx-auto space-y-6">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                        <div className="flex items-center gap-2">
                            <h1 className="text-h1">AI Models Configuration</h1>
                            <Badge variant="outline">
                                {source === "legacy_user_v1" ? "legacy" : "v1"}
                            </Badge>
                        </div>
                        <p className="mt-2 text-sm text-muted-foreground">
                            Configure your AI model, voice, and transcription services.{" "}
                            {docsUrl && (
                                <a href={docsUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">
                                    Learn more <ExternalLink className="h-3 w-3" />
                                </a>
                            )}
                        </p>
                    </div>
                    {source === "legacy_user_v1" && (
                        <Button type="button" variant="outline" onClick={() => setMigrationDialogOpen(true)} disabled={migrating}>
                            <RefreshCw className="mr-2 h-4 w-4" />
                            {migrating ? "Migrating..." : "Migrate to v2"}
                        </Button>
                    )}
                </div>

                {invalidConfigurationBanner}
                {error && (
                    <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                        {error}
                    </div>
                )}
                {notice && (
                    <div className="rounded-md border border-green-500/40 bg-green-500/10 px-4 py-3 text-sm text-green-700 dark:text-green-300">
                        {notice}
                    </div>
                )}

                <ServiceConfigurationForm
                    mode="global"
                    onSave={async (config) => {
                        setError(null);
                        setNotice(null);
                        await saveUserConfig(config as Parameters<typeof saveUserConfig>[0]);
                        await refreshConfig();
                        if (defaults) {
                            const configResult = await getModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Get();
                            if (configResult.data) {
                                applyResponse(configResult.data);
                            }
                        }
                        setNotice("Configuration saved");
                    }}
                />
                {migrationWarningDialog}
                {rawPayloadDialog}
            </div>
        );
    }

    return (
        <div className="w-full max-w-4xl mx-auto space-y-6">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                    <h1 className="text-h1">AI Models Configuration</h1>
                    <p className="mt-2 text-sm text-muted-foreground">
                        Organization-scoped model settings.{" "}
                        {docsUrl && (
                            <a href={docsUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">
                                Learn more <ExternalLink className="h-3 w-3" />
                            </a>
                        )}
                    </p>
                </div>
            </div>

            {invalidConfigurationBanner}
            {error && (
                <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    {error}
                </div>
            )}
            {notice && (
                <div className="rounded-md border border-green-500/40 bg-green-500/10 px-4 py-3 text-sm text-green-700 dark:text-green-300">
                    {notice}
                </div>
            )}

            {defaults && response && (
                <AIModelConfigurationV2Editor
                    defaults={defaults}
                    configuration={response.configuration}
                    effectiveConfiguration={response.effective_configuration}
                    onSave={saveConfiguration}
                />
            )}
            {migrationWarningDialog}
            {rawPayloadDialog}
        </div>
    );
}
