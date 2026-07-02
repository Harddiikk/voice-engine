import type { ReactNode } from "react";

// Deliberately NOT AdminGuard-wrapped: clients pick their own voice/language
// here. The page's API endpoints are org-scoped and secrets are masked; the
// page itself renders a trimmed view for non-admins (ModelConfigurationV2).
export default function ModelConfigurationsLayout({ children }: { children: ReactNode }) {
    return <>{children}</>;
}
