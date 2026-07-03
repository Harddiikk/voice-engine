'use client';

import { ArrowRight, Sparkles } from 'lucide-react';
import Link from 'next/link';

import { OverviewDashboard } from '@/components/dashboard/OverviewDashboard';
import { PageHeader } from '@/components/layout/PageHeader';
import { PageShell } from '@/components/layout/PageShell';
import { useUserConfig } from '@/context/UserConfigContext';

export default function HomePage() {
    const { planFeatures, isSuperuser } = useUserConfig();
    const canBuildWithAI = planFeatures.build_with_ai || isSuperuser;

    return (
        <PageShell width="default">
            <PageHeader
                eyebrow="Dashboard"
                title="Welcome back"
                subtitle="Here's how your voice agents are performing."
            />

            {/* Dashboard — at-a-glance metrics */}
            <OverviewDashboard showHeader={false} />

            {/* Build with AI — prompt-to-agent entry (Growth & higher only) */}
            {canBuildWithAI && (
                    <Link
                        href="/agent-builder"
                        className="group block rounded-2xl border border-border/60 bg-card shadow-[var(--shadow-card)] transition-all duration-200 hover:-translate-y-0.5 hover:border-border hover:shadow-[var(--shadow-pop)] focus-visible:ring-1 focus-visible:ring-ring outline-none"
                    >
                        <div className="flex items-center gap-4 p-5">
                            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-border/60 bg-muted text-cta transition-colors duration-200 group-hover:border-primary/30 group-hover:bg-accent">
                                <Sparkles className="h-5 w-5" />
                            </div>
                            <div className="min-w-0 flex-1">
                                <p className="text-eyebrow text-primary">Build with AI</p>
                                <p className="text-base font-medium">Type a prompt → we build your voice agent</p>
                                <p className="text-small text-muted-foreground">
                                    Describe your business or start from a template — we&apos;ll generate a working workflow.
                                </p>
                            </div>
                            <ArrowRight className="h-5 w-5 shrink-0 text-muted-foreground transition-transform duration-200 group-hover:translate-x-0.5 group-hover:text-primary" />
                        </div>
                    </Link>
                )}
        </PageShell>
    );
}
