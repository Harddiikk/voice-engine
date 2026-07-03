import { Database, FileText, SlidersHorizontal, UserPlus } from "lucide-react";

import { CrmSection } from "@/components/CrmSection";
import { IntegrationHero } from "@/components/integrations/IntegrationHero";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const highlights = [
  {
    icon: UserPlus,
    title: "Upsert the contact",
    description: "Matches the lead by phone and keeps them current.",
  },
  {
    icon: FileText,
    title: "Log the full call",
    description: "Outcome, recording, transcript and sentiment as a note.",
  },
  {
    icon: SlidersHorizontal,
    title: "Sync what matters",
    description: "Filter by disposition, sentiment and call length.",
  },
];

export default function CrmIntegrationPage() {
  return (
    <div className="flex justify-center px-4 py-12">
      <div className="stagger w-full max-w-2xl space-y-6">
        <IntegrationHero
          icon={Database}
          eyebrow="Integration"
          title="Connect your CRM"
          subtitle="Push every call to your CRM — contact, outcome, recording, transcript and sentiment."
          highlights={highlights}
        />

        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <CardTitle className="text-h3">Connect your CRM</CardTitle>
              <Badge
                variant="secondary"
                className="shrink-0 bg-muted text-muted-foreground"
              >
                Bring your own account
              </Badge>
            </div>
            <CardDescription className="text-body">
              Automatically push every call to your CRM — upsert the contact and
              log the outcome, recording, transcript and sentiment. Connect your
              own CRM account and API token.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <CrmSection />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
