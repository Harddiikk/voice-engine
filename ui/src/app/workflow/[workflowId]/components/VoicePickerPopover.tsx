"use client";

import { ChevronDown, Loader2, Volume2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import type { OrganizationAiModelConfigurationResponse } from "@/client/types.gen";
import type { ModelConfigurationDefaultsV2 } from "@/components/AIModelConfigurationV2Editor";
import { Button } from "@/components/ui/button";
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@/components/ui/popover";
import {
    deriveVoicePickTarget,
    VoiceLanguagePicker,
    voicePickOptions,
} from "@/components/VoiceLanguagePicker";
import { detailFromError } from "@/lib/apiError";
import type { WorkflowConfigurations } from "@/types/workflow-configurations";

interface VoicePickerPopoverProps {
    workflowConfigurations: WorkflowConfigurations;
    workflowName: string;
    onSave: (configurations: WorkflowConfigurations, workflowName: string) => Promise<void>;
    modelConfigurationDefaults: ModelConfigurationDefaultsV2 | null;
    organizationModelConfiguration: OrganizationAiModelConfigurationResponse | null;
    /** Read-only (e.g. viewing a historical version) — disables the picker. */
    disabled?: boolean;
}

/**
 * Retell-style quick voice picker for the editor top bar. The trigger shows the
 * current effective voice; the popover mounts the shared VoiceLanguagePicker
 * (Gemini gets the rich catalog with per-voice preview) plus Save and "Use
 * organization default" actions. Saving sets
 * workflow_configurations.model_voice_override without touching the canvas.
 */
export function VoicePickerPopover({
    workflowConfigurations,
    workflowName,
    onSave,
    modelConfigurationDefaults,
    organizationModelConfiguration,
    disabled,
}: VoicePickerPopoverProps) {
    const [open, setOpen] = useState(false);

    const target = useMemo(
        () => deriveVoicePickTarget(workflowConfigurations, organizationModelConfiguration),
        [workflowConfigurations, organizationModelConfiguration],
    );
    const options = useMemo(
        () => voicePickOptions(modelConfigurationDefaults, target),
        [modelConfigurationDefaults, target],
    );

    const savedOverride = workflowConfigurations.model_voice_override;
    const baseVoice = target?.baseVoice ?? "";
    const baseLanguage = target?.baseLanguage ?? "";
    const savedVoice = savedOverride?.voice || baseVoice;
    const savedLanguage = savedOverride?.language || baseLanguage;

    const [draftVoice, setDraftVoice] = useState(savedVoice);
    const [draftLanguage, setDraftLanguage] = useState(savedLanguage);
    const [isSaving, setIsSaving] = useState(false);

    useEffect(() => {
        setDraftVoice(savedVoice);
        setDraftLanguage(savedLanguage);
    }, [savedVoice, savedLanguage]);

    const isDirty = draftVoice !== savedVoice || draftLanguage !== savedLanguage;

    // Both configs are fetched async by the editor; null means still loading.
    const notReady = !modelConfigurationDefaults || !organizationModelConfiguration;

    const triggerVoice = savedOverride?.voice || target?.baseVoice || "default";

    const handleSave = async () => {
        if (!draftVoice.trim()) {
            toast.error("Pick a voice first");
            return;
        }
        setIsSaving(true);
        try {
            const next: WorkflowConfigurations = {
                ...workflowConfigurations,
                model_voice_override: {
                    voice: draftVoice.trim(),
                    ...(draftLanguage.trim() ? { language: draftLanguage.trim() } : {}),
                },
            };
            await onSave(next, workflowName);
            toast.success("Voice saved for this agent");
            setOpen(false);
        } catch (err) {
            toast.error(err instanceof Error && err.message ? err.message : detailFromError(err, "Failed to save voice"));
        } finally {
            setIsSaving(false);
        }
    };

    const handleReset = async () => {
        setIsSaving(true);
        try {
            const next: WorkflowConfigurations = { ...workflowConfigurations };
            delete next.model_voice_override;
            await onSave(next, workflowName);
            setDraftVoice(baseVoice);
            setDraftLanguage(baseLanguage);
            toast.success("Using the organization default voice");
            setOpen(false);
        } catch (err) {
            toast.error(err instanceof Error && err.message ? err.message : detailFromError(err, "Failed to reset voice"));
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <Popover open={open} onOpenChange={setOpen}>
            <PopoverTrigger asChild>
                <Button
                    variant="outline"
                    disabled={disabled}
                    className="flex items-center gap-2 bg-transparent border-[#3a3a3a] hover:bg-[#2a2a2a] text-white"
                    title="Pick the voice this agent speaks with"
                >
                    <Volume2 className="w-4 h-4" />
                    <span className="hidden sm:inline text-gray-400">Voice:</span>
                    <span className="max-w-[8rem] truncate font-medium">{triggerVoice}</span>
                    <ChevronDown className="w-4 h-4 opacity-60" />
                </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-96 p-4">
                {notReady ? (
                    <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" />
                        Loading voice options
                    </div>
                ) : !target ? (
                    <p className="py-2 text-sm text-muted-foreground">
                        Voice selection needs a configured model. Ask your administrator to set up
                        the model configuration first.
                    </p>
                ) : (
                    <div className="space-y-4">
                        <div className="rounded-md border bg-muted/20 p-3 text-sm">
                            <span className="text-muted-foreground">Current voice:</span>{" "}
                            <span className="font-medium">{savedVoice || "not set"}</span>{" "}
                            <span className="text-xs text-muted-foreground">
                                {savedOverride?.voice ? "(this agent)" : "(organization default)"}
                            </span>
                        </div>
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
                        <div className="flex items-center justify-end gap-2 border-t pt-3">
                            {savedOverride?.voice && (
                                <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={handleReset}
                                    disabled={isSaving}
                                >
                                    Use organization default
                                </Button>
                            )}
                            <Button
                                size="sm"
                                onClick={handleSave}
                                disabled={isSaving || !isDirty || !draftVoice.trim()}
                            >
                                {isSaving ? "Saving..." : "Save"}
                            </Button>
                        </div>
                    </div>
                )}
            </PopoverContent>
        </Popover>
    );
}
