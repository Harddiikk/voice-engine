import type { ReactNode } from "react";

import { SuperuserGuard } from "@/components/SuperuserGuard";

/** The owner console is deployment-owner only — org admins are not enough. */
export default function OwnerLayout({ children }: { children: ReactNode }) {
    return <SuperuserGuard>{children}</SuperuserGuard>;
}
