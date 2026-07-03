import { Activity, Coins, ShieldCheck, Wallet } from "lucide-react";

import { CreditsSection } from "@/components/CreditsSection";
import { IntegrationHero } from "@/components/integrations/IntegrationHero";

const highlights = [
  {
    icon: Coins,
    title: "Pay as you go",
    description: "Buy call credits in seconds — no lock-in, no minimums.",
  },
  {
    icon: ShieldCheck,
    title: "Secure PayU checkout",
    description: "Top up through PayU's PCI-compliant payment gateway.",
  },
  {
    icon: Activity,
    title: "Live balance & spend",
    description: "Track remaining credits and usage as calls complete.",
  },
];

export default function CreditsPage() {
  return (
    <div className="flex justify-center px-4 py-12">
      <div className="stagger w-full max-w-2xl space-y-6">
        <IntegrationHero
          icon={Wallet}
          eyebrow="Billing"
          title="Credits & Billing"
          subtitle="Track your plan, monitor remaining call credits, and top up in seconds with secure payments via PayU."
          highlights={highlights}
        />

        <CreditsSection />
      </div>
    </div>
  );
}
