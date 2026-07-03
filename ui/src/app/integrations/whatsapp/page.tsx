import { BadgeCheck, MessageCircle, SlidersHorizontal, Zap } from "lucide-react";

import { IntegrationHero } from "@/components/integrations/IntegrationHero";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
    <div className="flex justify-center px-4 py-12">
      <div className="stagger w-full max-w-2xl space-y-6">
        <IntegrationHero
          icon={MessageCircle}
          eyebrow="Integration"
          title="WhatsApp Follow-up"
          subtitle="Send an approved WhatsApp template to the lead automatically after each call."
          highlights={highlights}
        />

        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <CardTitle className="text-h3">WhatsApp Follow-up</CardTitle>
              <Badge
                variant="secondary"
                className="shrink-0 bg-muted text-muted-foreground"
              >
                Bring your own provider
              </Badge>
            </div>
            <CardDescription className="text-body">
              Automatically send an approved WhatsApp template (with an optional
              document) to the lead after each call. Connect your own provider
              account and API key.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <WhatsAppSection />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
