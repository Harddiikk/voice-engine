import { BadgeCheck, MessageCircle, SlidersHorizontal, Zap } from "lucide-react";

import { IntegrationHero } from "@/components/integrations/IntegrationHero";
import { PageShell } from "@/components/layout/PageShell";
import { SectionCard } from "@/components/layout/SectionCard";
import { Badge } from "@/components/ui/badge";
import { WhatsAppSection } from "@/components/WhatsAppSection";

const highlights = [
  {
    icon: Zap,
    title: "Auto-send after calls",
    description: "Fires the moment a qualifying call completes.",
  },
  {
    icon: BadgeCheck,
    title: "Approved templates",
    description: "Uses your verified Meta template and optional document.",
  },
  {
    icon: SlidersHorizontal,
    title: "Precise targeting",
    description: "Filter by disposition, sentiment and call length.",
  },
];

export default function WhatsAppIntegrationPage() {
  return (
    <PageShell width="narrow">
      <IntegrationHero
        icon={MessageCircle}
        eyebrow="Integration"
        title="WhatsApp Follow-up"
        subtitle="Send an approved WhatsApp template to the lead automatically after each call."
        highlights={highlights}
      />

      <SectionCard
        description="Automatically send an approved WhatsApp template (with an optional document) to the lead after each call. Connect your own provider account and API key."
        actions={
          <Badge
            variant="secondary"
            className="shrink-0 bg-muted text-muted-foreground"
          >
            Bring your own provider
          </Badge>
        }
      >
        <WhatsAppSection />
      </SectionCard>
    </PageShell>
  );
}
