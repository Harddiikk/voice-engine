"use client";

import { ExternalLink } from "lucide-react";

import { PageHeader } from "@/components/layout/PageHeader";
import { PageShell } from "@/components/layout/PageShell";
import { SectionCard } from "@/components/layout/SectionCard";
import { MCPSection } from "@/components/MCPSection";
import { OrganizationPreferencesSection } from "@/components/OrganizationPreferencesSection";
import { TelemetrySection } from "@/components/TelemetrySection";
import { INTEGRATION_DOCUMENTATION_URLS } from "@/constants/documentation";
import { useFeature } from "@/hooks/useFeature";
import { BRAND } from "@/lib/brand";

export default function SettingsPage() {
  // MCP is a Scale-plan feature (superuser always). Billing, Phone Numbers,
  // WhatsApp and CRM now live on their own pages in the sidebar.
  const mcp = useFeature("mcp");

  return (
    <PageShell width="narrow">
      <PageHeader
        eyebrow="Configuration"
        title="Settings"
        subtitle="Platform configuration. Manage Billing, Phone Numbers, WhatsApp and CRM from the Integrations section in the sidebar."
      />

        <SectionCard
          title="Preferences"
          description="Set organization-wide defaults such as the test phone number and timezone."
        >
          <OrganizationPreferencesSection />
        </SectionCard>

        {mcp.enabled && (
          <SectionCard
            title="MCP Server"
            description={
              <>
                Let AI agents access your {BRAND.name} workspace and documentation
                via the Model Context Protocol.{" "}
                <a
                  href={INTEGRATION_DOCUMENTATION_URLS.mcp}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-0.5 font-medium text-foreground underline underline-offset-2 transition-colors hover:text-primary"
                >
                  Learn more <ExternalLink className="h-3 w-3" />
                </a>
              </>
            }
          >
            <MCPSection />
          </SectionCard>
        )}

        <SectionCard
          title="Telemetry"
          description={
            <>
              Configure Langfuse tracing for your voice agent calls.{" "}
              <a
                href={INTEGRATION_DOCUMENTATION_URLS.tracing}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-0.5 font-medium text-foreground underline underline-offset-2 transition-colors hover:text-primary"
              >
                Learn more <ExternalLink className="h-3 w-3" />
              </a>
            </>
          }
        >
          <TelemetrySection />
        </SectionCard>
    </PageShell>
  );
}
