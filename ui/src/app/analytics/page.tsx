'use client';

import { Suspense } from 'react';

import { ByNumberTab } from '@/components/dashboard/ByNumberTab';
import { OverviewDashboard } from '@/components/dashboard/OverviewDashboard';
import { PageHeader } from '@/components/layout/PageHeader';
import { PageShell } from '@/components/layout/PageShell';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

import { RunsView } from '../usage/RunsView';

export default function AnalyticsPage() {
  return (
    <PageShell width="wide">
      <PageHeader
        eyebrow="Observe"
        title="Analytics"
        subtitle="Performance insights and a complete history of every agent run, in one place."
      />

      <Suspense fallback={null}>
        <Tabs defaultValue="overview" className="gap-6">
          <TabsList className="h-11 rounded-2xl border border-border/60 bg-card p-1 shadow-[var(--shadow-card)]">
            <TabsTrigger
              value="overview"
              className="rounded-xl px-5 text-sm font-medium text-muted-foreground transition-all duration-200 data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-[var(--shadow-pop)]"
            >
              Overview
            </TabsTrigger>
            <TabsTrigger
              value="by-number"
              className="rounded-xl px-5 text-sm font-medium text-muted-foreground transition-all duration-200 data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-[var(--shadow-pop)]"
            >
              By Number
            </TabsTrigger>
            <TabsTrigger
              value="runs"
              className="rounded-xl px-5 text-sm font-medium text-muted-foreground transition-all duration-200 data-[state=active]:bg-primary data-[state=active]:text-primary-foreground data-[state=active]:shadow-[var(--shadow-pop)]"
            >
              Runs
            </TabsTrigger>
          </TabsList>

          <TabsContent value="overview" className="mt-0">
            <OverviewDashboard showHeader={false} />
          </TabsContent>

          <TabsContent value="by-number" className="mt-0">
            <ByNumberTab />
          </TabsContent>

          <TabsContent value="runs" className="mt-0">
            <RunsView showHeader={false} />
          </TabsContent>
        </Tabs>
      </Suspense>
    </PageShell>
  );
}
