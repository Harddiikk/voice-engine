import { PhoneCall, ShieldCheck, Wallet } from "lucide-react";

import { IntegrationHero } from "@/components/integrations/IntegrationHero";
import { PageShell } from "@/components/layout/PageShell";
import { SectionCard } from "@/components/layout/SectionCard";
import { PhoneNumbersSection } from "@/components/PhoneNumbersSection";
import { Badge } from "@/components/ui/badge";

const highlights = [
  {
    icon: PhoneCall,
    title: "Outbound-ready",
    description: "Dedicated numbers for your calling campaigns.",
  },
  {
    icon: ShieldCheck,
    title: "KYC-verified",
    description: "Purchases unlock once your KYC is complete.",
  },
  {
    icon: Wallet,
    title: "Pay from credits",
    description: "Charged straight to your call-credit balance.",
  },
];

export default function PhoneNumbersPage() {
  return (
    <PageShell width="wide">
      <IntegrationHero
        icon={PhoneCall}
        eyebrow="Telephony"
        title="Phone Numbers"
        subtitle="Buy and manage outbound numbers for your campaigns — verification is built in at the point of purchase."
        highlights={highlights}
      />

      <SectionCard
        description="Buy and manage outbound numbers. Business verification (KYC) unlocks purchases and is completed right here."
        actions={
          <Badge
            variant="secondary"
            className="shrink-0 bg-muted text-muted-foreground"
          >
            KYC built in
          </Badge>
        }
      >
        <PhoneNumbersSection />
      </SectionCard>
    </PageShell>
  );
}
