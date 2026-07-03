import { CreditsSection } from "@/components/CreditsSection";
import { IntegrationPage } from "@/components/IntegrationPage";

export default function CreditsPage() {
  return (
    <IntegrationPage
      eyebrow="Billing"
      title="Credits & Billing"
      subtitle="Track your plan, monitor remaining call credits, and top up in seconds with secure payments via PayU."
    >
      <CreditsSection />
    </IntegrationPage>
  );
}
