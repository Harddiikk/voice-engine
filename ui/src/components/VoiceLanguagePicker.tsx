"use client";

import { Check, Loader2, Search, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import type { OrganizationAiModelConfigurationResponse } from "@/client/types.gen";
import type { ModelConfigurationDefaultsV2 } from "@/components/AIModelConfigurationV2Editor";
import { RealtimeVoicePreviewButton } from "@/components/RealtimeVoicePreviewButton";
import type { ProviderSchema } from "@/components/ServiceConfigurationForm";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { VoiceSelectorModal } from "@/components/VoiceSelectorModal";
import { LANGUAGE_DISPLAY_NAMES } from "@/constants/languages";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";
import {
    fetchRealtimeVoiceCatalog,
    type RealtimeVoiceCatalogEntry,
    supportsRealtimeVoiceCatalog,
} from "@/lib/voiceCatalog";
import type { WorkflowConfigurations } from "@/types/workflow-configurations";

/** Pipeline TTS providers with a browsable voice catalog + hosted previews. */
const CATALOG_TTS_PROVIDERS = [
    "elevenlabs",
    "deepgram",
    "sarvam",
    "cartesia",
    "dograh",
    "rime",
];

// ---------------------------------------------------------------------------
// Voice target derivation (shared by workflow settings + client Models view)
// ---------------------------------------------------------------------------

function asRecord(value: unknown): Record<string, unknown> | null {
    return value && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : null;
}

export interface VoicePickTarget {
    isRealtime: boolean;
    /** Realtime provider, or the TTS provider for pipeline configs. */
    provider: string;
    /** Realtime model — forwarded to the preview endpoint. */
    model?: string;
    baseVoice: string;
    baseLanguage: string;
    sttProvider?: string;
}

/** Derive the voice target from a v2 configuration dict (org or override). */
export function deriveVoiceTargetFromV2(
    configuration: Record<string, unknown> | null | undefined,
): VoicePickTarget | null {
    const config = asRecord(configuration);
    if (!config) return null;
    if (config.mode === "dograh") {
        const dograh = asRecord(config.dograh);
        return {
            isRealtime: false,
            provider: "dograh",
            baseVoice: String(dograh?.voice ?? ""),
            baseLanguage: String(dograh?.language ?? ""),
            sttProvider: "dograh",
        };
    }
    const byok = asRecord(config.byok);
    if (byok?.mode === "realtime") {
        const realtime = asRecord(asRecord(byok.realtime)?.realtime);
        if (!realtime?.provider) return null;
        return {
            isRealtime: true,
            provider: String(realtime.provider),
            model: realtime.model ? String(realtime.model) : undefined,
            baseVoice: String(realtime.voice ?? ""),
            baseLanguage: String(realtime.language ?? ""),
        };
    }
    if (byok?.mode === "pipeline") {
        const pipeline = asRecord(byok.pipeline);
        const tts = asRecord(pipeline?.tts);
        const stt = asRecord(pipeline?.stt);
        if (!tts?.provider) return null;
        return {
            isRealtime: false,
            provider: String(tts.provider),
            baseVoice: String(tts.voice ?? ""),
            baseLanguage: String(stt?.language ?? ""),
            sttProvider: stt?.provider ? String(stt.provider) : undefined,
        };
    }
    return null;
}

/** Derive the voice target from an effective (legacy-shape) configuration. */
export function deriveVoiceTargetFromEffective(
    effectiveConfiguration: Record<string, unknown> | null | undefined,
): VoicePickTarget | null {
    const effective = asRecord(effectiveConfiguration);
    if (!effective) return null;
    if (effective.is_realtime) {
        const realtime = asRecord(effective.realtime);
        if (!realtime?.provider) return null;
        return {
            isRealtime: true,
            provider: String(realtime.provider),
            model: realtime.model ? String(realtime.model) : undefined,
            baseVoice: String(realtime.voice ?? ""),
            baseLanguage: String(realtime.language ?? ""),
        };
    }
    const tts = asRecord(effective.tts);
    const stt = asRecord(effective.stt);
    if (!tts?.provider) return null;
    return {
        isRealtime: false,
        provider: String(tts.provider),
        baseVoice: String(tts.voice ?? ""),
        baseLanguage: String(stt?.language ?? ""),
        sttProvider: stt?.provider ? String(stt.provider) : undefined,
    };
}

/**
 * Resolve which service the voice pick applies to: a workflow-level full v2
 * override wins over the organization's effective configuration — mirroring
 * the backend resolution in get_effective_ai_model_configuration_for_workflow.
 */
export function deriveVoicePickTarget(
    workflowConfigurations: WorkflowConfigurations,
    organizationModelConfiguration: OrganizationAiModelConfigurationResponse | null,
): VoicePickTarget | null {
    const v2Override = workflowConfigurations.model_configuration_v2_override;
    if (v2Override) {
        return deriveVoiceTargetFromV2(v2Override as unknown as Record<string, unknown>);
    }
    return deriveVoiceTargetFromEffective(
        organizationModelConfiguration?.effective_configuration as
            | Record<string, unknown>
            | null
            | undefined,
    );
}

function schemaFieldOptions(schema: ProviderSchema | undefined, field: string): string[] {
    const property = schema?.properties?.[field];
    return (property?.enum || property?.examples || []) as string[];
}

/** Voice/language choices for a target, from the fetched v2 defaults schema. */
export function voicePickOptions(
    defaults: ModelConfigurationDefaultsV2 | null,
    target: VoicePickTarget | null,
): { voices: string[]; languages: string[] } {
    if (!defaults || !target) return { voices: [], languages: [] };
    if (target.isRealtime) {
        const schema = defaults.byok.realtime.realtime[target.provider];
        return {
            voices: schemaFieldOptions(schema, "voice"),
            languages: schemaFieldOptions(schema, "language"),
        };
    }
    if (target.provider === "dograh") {
        return {
            voices: defaults.dograh.voices ?? [],
            languages: defaults.dograh.languages ?? [],
        };
    }
    const ttsSchema = defaults.byok.pipeline.tts[target.provider];
    const sttSchema = target.sttProvider
        ? defaults.byok.pipeline.stt[target.sttProvider]
        : undefined;
    return {
        voices: schemaFieldOptions(ttsSchema, "voice"),
        languages: schemaFieldOptions(sttSchema, "language"),
    };
}

export interface VoiceLanguagePickerProps {
    /** Realtime (speech-to-speech) vs pipeline (TTS/STT) target. */
    isRealtime: boolean;
    /** Realtime provider, or the TTS provider for pipeline configs. */
    provider: string;
    /** Realtime model — forwarded to the preview endpoint / catalog query. */
    model?: string;
    voice: string;
    language: string;
    /** Known voices (schema examples); empty → free-text input. */
    voiceOptions: string[];
    /** Known languages (schema examples); empty → free-text input. */
    languageOptions: string[];
    onVoiceChange: (voice: string) => void;
    onLanguageChange: (language: string) => void;
    disabled?: boolean;
}

/** Ensure the active value is always an option so the Select can render it. */
function withSelected(options: string[], selected: string): string[] {
    if (!selected || options.includes(selected)) return options;
    return [selected, ...options];
}

const languageLabel = (code: string) => LANGUAGE_DISPLAY_NAMES[code] || code;

// ---------------------------------------------------------------------------
// Realtime voice catalog picker (Gemini): gender/character tags + per-row play
// ---------------------------------------------------------------------------

const GENDER_FILTERS = [
    { value: "all", label: "All" },
    { value: "female", label: "Female" },
    { value: "male", label: "Male" },
] as const;

type GenderFilter = (typeof GENDER_FILTERS)[number]["value"];

const genderBadgeVariant = (gender: string): "secondary" | "muted" =>
    gender.toLowerCase() === "female" ? "secondary" : "muted";

/**
 * Rich realtime voice picker: a scrollable list of the provider's prebuilt
 * voices, each with a perceived-gender badge, a character tag, and a Play
 * button so the user can listen before selecting. Falls back to `fallback`
 * (the plain Select + preview button) when the catalog can't be loaded or the
 * provider ships no catalog.
 */
function RealtimeVoiceCatalogPicker({
    provider,
    model,
    voice,
    language,
    onVoiceChange,
    disabled,
    fallback,
}: {
    provider: string;
    model?: string;
    voice: string;
    language: string;
    onVoiceChange: (voice: string) => void;
    disabled?: boolean;
    fallback: React.ReactNode;
}) {
    const { getAccessToken, loading: authLoading, user } = useAuth();
    const [entries, setEntries] = useState<RealtimeVoiceCatalogEntry[] | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [failed, setFailed] = useState(false);
    const [genderFilter, setGenderFilter] = useState<GenderFilter>("all");
    const [search, setSearch] = useState("");

    useEffect(() => {
        if (authLoading || !user) return;
        let active = true;
        setIsLoading(true);
        setFailed(false);
        (async () => {
            try {
                const token = await getAccessToken();
                const catalog = await fetchRealtimeVoiceCatalog(token, provider);
                if (!active) return;
                setEntries(catalog);
            } catch {
                if (!active) return;
                setFailed(true);
                setEntries(null);
            } finally {
                if (active) setIsLoading(false);
            }
        })();
        return () => {
            active = false;
        };
    }, [authLoading, user, getAccessToken, provider]);

    const filtered = useMemo(() => {
        if (!entries) return [];
        const query = search.trim().toLowerCase();
        return entries.filter((entry) => {
            if (genderFilter !== "all" && entry.gender.toLowerCase() !== genderFilter) return false;
            if (!query) return true;
            return (
                entry.name.toLowerCase().includes(query) ||
                entry.characteristic.toLowerCase().includes(query)
            );
        });
    }, [entries, genderFilter, search]);

    if (authLoading || isLoading) {
        return (
            <div className="flex items-center justify-center rounded-md border py-10">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
        );
    }

    // Fetch failed or provider ships no catalog → plain control keeps working.
    if (failed || !entries || entries.length === 0) {
        return <>{fallback}</>;
    }

    return (
        <div className="space-y-2">
            {/* Filter row: gender segmented control + search */}
            <div className="flex flex-wrap items-center gap-2">
                <div className="flex rounded-md border p-0.5">
                    {GENDER_FILTERS.map((option) => (
                        <button
                            key={option.value}
                            type="button"
                            onClick={() => setGenderFilter(option.value)}
                            disabled={disabled}
                            aria-pressed={genderFilter === option.value}
                            className={cn(
                                "rounded px-2.5 py-1 text-xs font-medium transition-colors",
                                genderFilter === option.value
                                    ? "bg-primary text-primary-foreground"
                                    : "text-muted-foreground hover:text-foreground",
                            )}
                        >
                            {option.label}
                        </button>
                    ))}
                </div>
                <div className="relative min-w-[140px] flex-1">
                    <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                    <Input
                        value={search}
                        onChange={(event) => setSearch(event.target.value)}
                        placeholder="Search voices..."
                        className="h-9 pl-8"
                        disabled={disabled}
                        aria-label="Search voices"
                    />
                </div>
            </div>

            {/* Voice list */}
            {filtered.length === 0 ? (
                <p className="rounded-md border py-8 text-center text-sm text-muted-foreground">
                    No voices match these filters
                </p>
            ) : (
                <div
                    role="listbox"
                    aria-label="Realtime voices"
                    className="max-h-72 space-y-1.5 overflow-y-auto rounded-md border p-1.5"
                >
                    {filtered.map((entry) => {
                        const isSelected = entry.name === voice;
                        return (
                            <div
                                key={entry.name}
                                role="option"
                                aria-selected={isSelected}
                                tabIndex={disabled ? -1 : 0}
                                onClick={() => !disabled && onVoiceChange(entry.name)}
                                onKeyDown={(event) => {
                                    if (disabled) return;
                                    if (event.key === "Enter" || event.key === " ") {
                                        event.preventDefault();
                                        onVoiceChange(entry.name);
                                    }
                                }}
                                className={cn(
                                    "flex cursor-pointer items-center gap-2 rounded-md border p-2 transition-colors hover:bg-accent",
                                    isSelected ? "border-primary ring-1 ring-primary" : "border-transparent",
                                    disabled && "cursor-not-allowed opacity-60",
                                )}
                            >
                                {/* Play control — stop propagation so previewing doesn't select. */}
                                <span
                                    className="shrink-0"
                                    onClick={(event) => event.stopPropagation()}
                                    onKeyDown={(event) => event.stopPropagation()}
                                >
                                    <RealtimeVoicePreviewButton
                                        provider={provider}
                                        voice={entry.name}
                                        language={language || undefined}
                                        model={model}
                                        disabled={disabled}
                                        className="h-8 w-8"
                                    />
                                </span>
                                <span className="flex min-w-0 flex-1 items-center gap-2">
                                    <span className="truncate text-sm font-medium">{entry.name}</span>
                                    {entry.gender && (
                                        <Badge variant={genderBadgeVariant(entry.gender)} className="capitalize">
                                            {entry.gender}
                                        </Badge>
                                    )}
                                    {entry.characteristic && (
                                        <Badge variant="outline">{entry.characteristic}</Badge>
                                    )}
                                </span>
                                {isSelected && <Check className="h-4 w-4 shrink-0 text-primary" />}
                            </div>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

/**
 * Voice + language picker shared by the workflow "Voice" settings card and
 * the trimmed client Models view. Gemini realtime providers get a rich
 * catalog picker (gender/character tags + per-voice preview); other realtime
 * providers get a Select with a live preview button; catalog TTS providers
 * reuse the voice-catalog modal.
 */
export function VoiceLanguagePicker({
    isRealtime,
    provider,
    model,
    voice,
    language,
    voiceOptions,
    languageOptions,
    onVoiceChange,
    onLanguageChange,
    disabled,
}: VoiceLanguagePickerProps) {
    const voiceNotInOptions =
        Boolean(voice) && voiceOptions.length > 0 && !voiceOptions.includes(voice);
    const useCatalogModal = !isRealtime && CATALOG_TTS_PROVIDERS.includes(provider);
    const useRichRealtimePicker = isRealtime && supportsRealtimeVoiceCatalog(provider);

    const renderVoiceControl = () => {
        if (useCatalogModal) {
            return (
                <VoiceSelectorModal
                    provider={provider}
                    value={voice}
                    onChange={onVoiceChange}
                    allowManualInput
                    className="flex-1"
                />
            );
        }
        if (voiceOptions.length > 0) {
            return (
                <Select value={voice} onValueChange={(value) => value && onVoiceChange(value)} disabled={disabled}>
                    <SelectTrigger className="flex-1">
                        <SelectValue placeholder="Select voice" />
                    </SelectTrigger>
                    <SelectContent>
                        {withSelected(voiceOptions, voice).map((option) => (
                            <SelectItem key={option} value={option}>
                                {option}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            );
        }
        return (
            <Input
                value={voice}
                onChange={(event) => onVoiceChange(event.target.value)}
                placeholder="Enter voice ID"
                className="flex-1"
                disabled={disabled}
            />
        );
    };

    return (
        <div className="space-y-4">
            <div className="space-y-2">
                <Label>Voice</Label>
                {useRichRealtimePicker ? (
                    <RealtimeVoiceCatalogPicker
                        provider={provider}
                        model={model}
                        voice={voice}
                        language={language}
                        onVoiceChange={onVoiceChange}
                        disabled={disabled}
                        fallback={
                            <div className="flex items-center gap-2">
                                {renderVoiceControl()}
                                <RealtimeVoicePreviewButton
                                    provider={provider}
                                    voice={voice}
                                    language={language || undefined}
                                    model={model}
                                    disabled={disabled}
                                />
                            </div>
                        }
                    />
                ) : (
                    <div className="flex items-center gap-2">
                        {renderVoiceControl()}
                        {isRealtime && (
                            <RealtimeVoicePreviewButton
                                provider={provider}
                                voice={voice}
                                language={language || undefined}
                                model={model}
                                disabled={disabled}
                            />
                        )}
                    </div>
                )}
                {voiceNotInOptions && (
                    <p className="flex items-center gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
                        <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
                        <span>
                            Voice &quot;{voice}&quot; is not in the provider&apos;s known voice
                            list — double-check it before going live.
                        </span>
                    </p>
                )}
            </div>

            <div className="space-y-2">
                <Label>Language</Label>
                {languageOptions.length > 0 ? (
                    <Select
                        value={language}
                        onValueChange={(value) => value && onLanguageChange(value)}
                        disabled={disabled}
                    >
                        <SelectTrigger className="w-full">
                            <SelectValue placeholder="Select language" />
                        </SelectTrigger>
                        <SelectContent>
                            {withSelected(languageOptions, language).map((option) => (
                                <SelectItem key={option} value={option}>
                                    {languageLabel(option)}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                ) : (
                    <Input
                        value={language}
                        onChange={(event) => onLanguageChange(event.target.value)}
                        placeholder="Language code (e.g. en, hi)"
                        disabled={disabled}
                    />
                )}
            </div>
        </div>
    );
}
