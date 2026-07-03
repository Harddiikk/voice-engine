import { redirect } from "next/navigation";

/**
 * The standalone KYC page has been folded into Phone Numbers as an inline,
 * point-of-purchase gate (see `PhoneNumbersSection` + `KycWizard`). This route
 * is kept only so old bookmarks / links don't 404 — it redirects to where KYC
 * now lives.
 */
export default function KycPage() {
  redirect("/phone-numbers");
}
