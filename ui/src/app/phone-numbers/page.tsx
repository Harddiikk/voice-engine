import { PhoneCall, ShieldCheck, Wallet } from "lucide-react";

import { IntegrationHero } from "@/components/integrations/IntegrationHero";
import { PhoneNumbersSection } from "@/components/PhoneNumbersSection";
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
    <div className="flex justify-center px-4 py-12">
      <div className="stagger w-full max-w-2xl space-y-6">
        <IntegrationHero
          icon={PhoneCall}
          eyebrow="Telephony"
          title="Phone Numbers"
          subtitle="Buy and manage outbound numbers for your campaigns."
          highlights={highlights}
        />

        <Card>
          <CardHeader>
            <div className="flex items-center justify-between gap-3">
              <CardTitle className="text-h3">Phone Numbers</CardTitle>
              <Badge
                variant="secondary"
                className="shrink-0 bg-muted text-muted-foreground"
              >
                KYC required
              </Badge>
            </div>
            <CardDescription className="text-body">
              Buy a phone number for outbound calls. Requires completed KYC;
              charged to your call-credit balance.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <PhoneNumbersSection />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
