"use client";

import { Loader2, Play, Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import {
    fetchRealtimeVoicePreview,
    supportsRealtimeVoicePreview,
} from "@/lib/voicePreview";

interface RealtimeVoicePreviewButtonProps {
    provider: string;
    voice: string;
    language?: string;
    model?: string;
    disabled?: boolean;
    className?: string;
}

/**
 * Play/Stop button that fetches (and caches server-side) a short spoken
 * sample for a realtime provider voice. Renders nothing for providers the
 * backend cannot synthesize previews for.
 */
export function RealtimeVoicePreviewButton({
    provider,
    voice,
    language,
    model,
    disabled,
    className,
}: RealtimeVoicePreviewButtonProps) {
    const auth = useAuth();
    const [isLoading, setIsLoading] = useState(false);
    const [isPlaying, setIsPlaying] = useState(false);
    const audioRef = useRef<HTMLAudioElement | null>(null);

    const stopPreview = useCallback(() => {
        if (audioRef.current) {
            audioRef.current.pause();
            audioRef.current = null;
        }
        setIsPlaying(false);
    }, []);

    // Stop any playback on unmount.
    useEffect(() => () => stopPreview(), [stopPreview]);

    if (!supportsRealtimeVoicePreview(provider)) return null;

    const handleClick = async () => {
        if (isPlaying) {
            stopPreview();
            return;
        }
        if (!voice) return;
        setIsLoading(true);
        try {
            const token = await auth.getAccessToken();
            const { url } = await fetchRealtimeVoicePreview(token, {
                provider,
                voice,
                language,
                model,
            });
            stopPreview();
            const audio = new Audio(url);
            audioRef.current = audio;
            setIsPlaying(true);
            const clear = () => {
                if (audioRef.current === audio) audioRef.current = null;
                setIsPlaying(false);
            };
            audio.onended = clear;
            audio.onerror = clear;
            audio.play().catch(clear);
        } catch (err) {
            toast.error(
                err instanceof Error && err.message
                    ? err.message
                    : detailFromError(err, "Voice preview failed"),
            );
        } finally {
            setIsLoading(false);
        }
    };

    return (
        <Button
            type="button"
            variant="outline"
            size="icon"
            className={className}
            onClick={handleClick}
            disabled={disabled || isLoading || !voice}
            aria-label={isPlaying ? "Stop voice preview" : "Play voice preview"}
            title={isPlaying ? "Stop preview" : "Preview this voice"}
        >
            {isLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
            ) : isPlaying ? (
                <Square className="h-4 w-4 fill-current" />
            ) : (
                <Play className="h-4 w-4 fill-current" />
            )}
        </Button>
    );
}
