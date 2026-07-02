"use client";

import { TriangleAlert } from "lucide-react";

import type { ModelConfigurationDefaultsV2 } from "@/components/AIModelConfigurationV2Editor";
import { RealtimeVoicePreviewButton } from "@/components/RealtimeVoicePreviewButton";
import type { ProviderSchema } from "@/components/ServiceConfigurationForm";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { VoiceSelectorModal } from "@/components/VoiceSelectorModal";
import { LANGUAGE_DISPLAY_NAMES } from "@/constants/languages";

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

/**
 * Voice + language picker shared by the workflow "Voice" settings card and
 * the trimmed client Models view. Realtime providers get a Select with a
 * live preview button; catalog TTS providers reuse the voice-catalog modal.
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
