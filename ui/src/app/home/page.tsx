'use client';

import { ArrowRight, BarChart3, Sparkles } from 'lucide-react';
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
                actions={
                    <Link
                        href="/analytics"
                        className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 bg-card px-3 py-2 text-small font-medium text-foreground shadow-[var(--shadow-card)] transition-all duration-200 hover:-translate-y-0.5 hover:border-border hover:shadow-[var(--shadow-pop)] focus-visible:ring-1 focus-visible:ring-ring outline-none"
                    >
                        <BarChart3 className="h-4 w-4 text-muted-foreground" />
                        View full analytics
                        <ArrowRight className="h-4 w-4 text-muted-foreground" />
                    </Link>
                }
            />

            {/* At-a-glance dashboard — compact tiles + one sparkline + outcomes donut */}
            <OverviewDashboard compact showHeader={false} />

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
