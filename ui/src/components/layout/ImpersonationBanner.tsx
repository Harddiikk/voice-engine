"use client";

import { UserCog } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";

const STASH_KEY = "dograh_admin_session";

interface AdminStash {
  token?: string;
  user?: Record<string, unknown> | null;
  clientLabel?: string;
}

/**
 * Persistent top banner shown while a superuser is impersonating a client
 * account (local auth mode). "Return to admin" restores the stashed admin
 * session via the same session route impersonation used, then lands on
 * /superadmin. The stash is written by `impersonateAsSuperadmin` before it
 * overwrites the session cookie.
 */
export function ImpersonationBanner() {
  const [stash, setStash] = useState<AdminStash | null>(null);
  const [returning, setReturning] = useState(false);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(STASH_KEY);
      if (raw) setStash(JSON.parse(raw) as AdminStash);
    } catch {
      /* malformed stash — ignore, no banner */
    }
  }, []);

  if (!stash?.token) return null;

  const label = stash.clientLabel || "a client account";

  const returnToAdmin = async () => {
    setReturning(true);
    try {
      const res = await fetch("/api/auth/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: stash.token, user: stash.user ?? null }),
      });
      if (!res.ok) throw new Error("restore_failed");
      localStorage.removeItem(STASH_KEY);
      window.location.href = "/superadmin";
    } catch {
      setReturning(false);
      toast.error("Couldn't return to your admin account — please log in again.");
    }
  };

  return (
    <div
      role="alert"
      className="border-b border-primary/30 bg-primary/10 px-4 py-2.5 text-foreground"
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex min-w-0 items-center gap-2.5">
          <UserCog className="h-4 w-4 shrink-0 text-primary" />
          <p className="min-w-0 truncate text-sm">
            You&apos;re viewing{" "}
            <span className="font-semibold">{label}</span> as an admin.
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={returnToAdmin}
          disabled={returning}
          className="h-8 shrink-0 border-primary/40 bg-transparent hover:bg-primary/10"
        >
          {returning ? "Returning…" : "Return to my admin account"}
        </Button>
      </div>
    </div>
  );
}
