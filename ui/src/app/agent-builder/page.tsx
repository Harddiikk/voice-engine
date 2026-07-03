"use client";

import {
    ArrowRight,
    CalendarClock,
    Headphones,
    Home as HomeIcon,
    Loader2,
    Sparkles,
    Target,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
    Card,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useAppConfig } from "@/context/AppConfigContext";
import { useUserConfig } from "@/context/UserConfigContext";
import {
    type AgentTemplate,
    createAgent,
    listAgentTemplates,
} from "@/lib/api/agentBuilder";
import { resolveBrowserBackendUrl } from "@/lib/apiClient";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { BRAND } from "@/lib/brand";
import logger from "@/lib/logger";

interface GenerateResponse {
    workflow_id: number | string;
    status: "draft";
    name: string;
    warnings: string[];
    editor_path: string;
}

const PRICING_OPTIONS = [
    { value: "trust_spoken_price", label: "Trust the spoken price" },
    { value: "from_backend", label: "From my backend" },
] as const;

const LANGUAGE_OPTIONS = [
    { value: "Hinglish (a natural, friendly Hindi-English mix)", label: "Hinglish (Hindi-English mix)" },
    { value: "Hindi", label: "Hindi" },
    { value: "English", label: "English" },
];

const TEMPLATE_ICONS: Record<string, typeof HomeIcon> = {
    real_estate_cold_caller: HomeIcon,
    appointment_setter: CalendarClock,
    lead_qualifier: Target,
    support_callback: Headphones,
};

export default function AgentBuilderPage() {
    const router = useRouter();
    const { user, loading: authLoading, getAccessToken } = useAuth();
    const { config } = useAppConfig();
    const { planFeatures, isSuperuser, planLoaded } = useUserConfig();
    const canBuildWithAI = planFeatures.build_with_ai || isSuperuser;

    // The free-form business prompt — the only required field.
    const [prompt, setPrompt] = useState("");

    // Questionnaire (the `business` object). All optional.
    const [businessType, setBusinessType] = useState("");
    const [sellsTo, setSellsTo] = useState("");
    const [catalog, setCatalog] = useState("");
    const [orderWebhookUrl, setOrderWebhookUrl] = useState("");
    const [customerLookup, setCustomerLookup] = useState("");
    const [kycRequired, setKycRequired] = useState(false);
    const [pricingSource, setPricingSource] = useState("");
    const [personaName, setPersonaName] = useState("");
    const [language, setLanguage] = useState("");
    const [voice, setVoice] = useState("");
    const [goal, setGoal] = useState("");
    const [objections, setObjections] = useState("");
    const [crossSell, setCrossSell] = useState("");
    const [fulfillment, setFulfillment] = useState("");

    const [isSubmitting, setIsSubmitting] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Templates ("Or start from a template").
    const [templates, setTemplates] = useState<AgentTemplate[]>([]);
    const [templatesLoading, setTemplatesLoading] = useState(true);
    const [templatesError, setTemplatesError] = useState<string | null>(null);
    const templatesFetched = useRef(false);

    // Template details dialog state.
    const [selectedTemplate, setSelectedTemplate] = useState<AgentTemplate | null>(null);
    const [templateBusinessName, setTemplateBusinessName] = useState("");
    const [templateIndustry, setTemplateIndustry] = useState("");
    const [templateDetails, setTemplateDetails] = useState("");
    const [templateLanguage, setTemplateLanguage] = useState(LANGUAGE_OPTIONS[0].value);
    const [isCreatingTemplate, setIsCreatingTemplate] = useState(false);
    const [dialogError, setDialogError] = useState<string | null>(null);

    const authReady = !authLoading && !!user;
    const canSubmit = authReady && prompt.trim().length > 0 && !isSubmitting;

    useEffect(() => {
        if (!authReady || !canBuildWithAI || templatesFetched.current) return;
        templatesFetched.current = true;

        const fetchTemplates = async () => {
            try {
                const accessToken = await getAccessToken();
                const data = await listAgentTemplates(accessToken);
                setTemplates(data);
            } catch (err) {
                logger.error(`Error loading agent templates: ${err}`);
                setTemplatesError("Could not load templates. Please refresh the page.");
            } finally {
                setTemplatesLoading(false);
            }
        };
        fetchTemplates();
    }, [authReady, canBuildWithAI, getAccessToken]);

    const openTemplateDialog = (template: AgentTemplate) => {
        setSelectedTemplate(template);
        setTemplateBusinessName("");
        setTemplateIndustry("");
        setTemplateDetails("");
        setTemplateLanguage(LANGUAGE_OPTIONS[0].value);
        setDialogError(null);
    };

    const handleTemplateCreate = async () => {
        if (!selectedTemplate || !templateBusinessName.trim() || isCreatingTemplate) return;
        setIsCreatingTemplate(true);
        setDialogError(null);
        try {
            const accessToken = await getAccessToken();
            const result = await createAgent(
                {
                    mode: "template",
                    template_id: selectedTemplate.id,
                    business: {
                        name: templateBusinessName.trim(),
                        industry: templateIndustry.trim() || undefined,
                        details: templateDetails.trim() || undefined,
                        language: templateLanguage,
                    },
                },
                accessToken,
            );
            router.push(`/workflow/${result.workflow_id}`);
        } catch (err) {
            logger.error(`Error creating agent from template: ${err}`);
            setDialogError(err instanceof Error ? err.message : "Failed to create the agent. Please try again.");
            setIsCreatingTemplate(false);
        }
    };

    const handleSubmit = async () => {
        // Guard the call on auth being ready (the interceptor that attaches the
        // bearer token is only available once auth is fully loaded).
        if (!authReady) return;
        if (!prompt.trim() || isSubmitting) return;

        setIsSubmitting(true);
        setError(null);

        // Only send fields the user actually filled in.
        const business: Record<string, unknown> = {};
        const setIf = (key: string, value: string) => {
            const trimmed = value.trim();
            if (trimmed) business[key] = trimmed;
        };
        setIf("business_type", businessType);
        setIf("sells_to", sellsTo);
        setIf("catalog", catalog);
        setIf("order_webhook_url", orderWebhookUrl);
        setIf("customer_lookup", customerLookup);
        setIf("pricing_source", pricingSource);
        setIf("persona_name", personaName);
        setIf("language", language);
        setIf("voice", voice);
        setIf("goal", goal);
        setIf("objections", objections);
        setIf("cross_sell", crossSell);
        setIf("fulfillment", fulfillment);
        if (kycRequired) business.kyc_required = true;

        try {
            const accessToken = await getAccessToken();
            const baseUrl = resolveBrowserBackendUrl(config?.backendApiEndpoint);
            const res = await fetch(`${baseUrl}/api/v1/agent-builder/generate`, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    Authorization: `Bearer ${accessToken}`,
                },
                body: JSON.stringify({ prompt: prompt.trim(), business }),
            });

            // fetch does not reject on 4xx/5xx — inspect the status explicitly.
            if (!res.ok) {
                if (res.status === 503) {
                    setError(
                        "Agent builder isn't configured yet; the owner needs to set the AI key.",
                    );
                    return;
                }
                let body: unknown = null;
                try {
                    body = await res.json();
                } catch {
                    // Non-JSON error body — fall through to the generic message.
                }
                setError(detailFromError(body, "Failed to build your agent. Please try again."));
                return;
            }

            const data = (await res.json()) as GenerateResponse;
            const warnings = Array.isArray(data.warnings) ? data.warnings : [];
            if (warnings.length > 0) {
                warnings.forEach((w) => toast.warning(w));
            }
            router.push(data.editor_path || `/workflow/${data.workflow_id}`);
        } catch (err) {
            logger.error(`Error generating agent: ${err}`);
            setError(detailFromError(err, "Failed to build your agent. Please try again."));
        } finally {
            setIsSubmitting(false);
        }
    };

    // Wait for the plan fetch before deciding what to render (avoids flashing
    // the upgrade card to eligible users on a hard navigation).
    if (!planLoaded) {
        return (
            <div className="flex min-h-screen items-center justify-center bg-background">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-label="Loading" />
            </div>
        );
    }

    // Route guard: Build with AI is a Growth-and-higher feature. Superusers
    // always pass; everyone else gets a friendly upgrade card.
    if (!canBuildWithAI) {
        return (
            <div className="min-h-screen bg-background">
                <div className="container mx-auto max-w-2xl px-4 py-16">
                    <div className="rounded-2xl border border-border/60 bg-card p-8 text-center shadow-[var(--shadow-card)]">
                        <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-xl border border-border/60 bg-muted text-cta">
                            <Sparkles className="h-6 w-6" />
                        </div>
                        <p className="text-eyebrow text-primary">Build with AI</p>
                        <h1 className="text-h2 mt-1">Available on Growth and higher</h1>
                        <p className="text-body mt-2 text-muted-foreground">
                            Describe your business or start from a template and we&apos;ll generate a working
                            voice-agent workflow. Upgrade your plan to unlock it.
                        </p>
                        <Button asChild size="lg" className="mt-6 bg-cta text-cta-foreground hover:bg-cta/90">
                            <Link href="/credits">
                                Upgrade
                                <ArrowRight className="ml-2 h-4 w-4" />
                            </Link>
                        </Button>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="min-h-screen bg-background">
            <div className="container mx-auto max-w-3xl px-4 py-12">
                {/* Header */}
                <div className="mb-8">
                    <p className="text-eyebrow text-primary">{BRAND.name}</p>
                    <h1 className="text-h1 mt-1 flex items-center gap-2">
                        <Sparkles className="h-7 w-7 text-cta" aria-hidden />
                        Build an agent from a prompt
                    </h1>
                    <p className="text-body mt-2 text-muted-foreground">
                        Describe your business and we&apos;ll generate a working voice-agent workflow you can refine.
                    </p>
                </div>

                {/* Prompt */}
                <div className="rounded-2xl border border-border/60 bg-card p-5 shadow-[var(--shadow-card)]">
                    <Label htmlFor="prompt" className="text-small font-medium">
                        Your prompt
                    </Label>
                    <Textarea
                        id="prompt"
                        value={prompt}
                        onChange={(e) => setPrompt(e.target.value)}
                        placeholder="Describe your business and what the agent should do…"
                        className="mt-2 min-h-[140px] resize-none"
                        disabled={isSubmitting}
                    />
                </div>

                {/* Questionnaire */}
                <div className="mt-6 rounded-2xl border border-border/60 bg-card p-5 shadow-[var(--shadow-card)]">
                    <p className="text-eyebrow text-primary">Tell us more</p>
                    <h2 className="text-h3 mt-1">A few optional details</h2>
                    <p className="text-body mt-1 text-muted-foreground">
                        The more you share, the better the first draft. Everything here is optional.
                    </p>

                    <div className="mt-5 grid grid-cols-1 gap-5 sm:grid-cols-2">
                        <div className="space-y-2">
                            <Label htmlFor="business-type">Business type</Label>
                            <Input
                                id="business-type"
                                value={businessType}
                                onChange={(e) => setBusinessType(e.target.value)}
                                placeholder="e.g. Wholesale FMCG distributor"
                                disabled={isSubmitting}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="sells-to">Who you sell to</Label>
                            <Input
                                id="sells-to"
                                value={sellsTo}
                                onChange={(e) => setSellsTo(e.target.value)}
                                placeholder="e.g. Kirana stores, retailers"
                                disabled={isSubmitting}
                            />
                        </div>

                        <div className="space-y-2 sm:col-span-2">
                            <Label htmlFor="catalog">Product catalog</Label>
                            <Textarea
                                id="catalog"
                                value={catalog}
                                onChange={(e) => setCatalog(e.target.value)}
                                placeholder="Paste your products + pack sizes…"
                                className="min-h-[100px]"
                                disabled={isSubmitting}
                            />
                        </div>

                        <div className="space-y-2 sm:col-span-2">
                            <Label htmlFor="order-webhook">Order / lead webhook URL (optional)</Label>
                            <Input
                                id="order-webhook"
                                type="url"
                                value={orderWebhookUrl}
                                onChange={(e) => setOrderWebhookUrl(e.target.value)}
                                placeholder="https://example.com/webhooks/orders"
                                disabled={isSubmitting}
                            />
                        </div>

                        <div className="space-y-3 sm:col-span-2">
                            <div className="flex items-start gap-3">
                                <Checkbox
                                    id="kyc-required"
                                    checked={kycRequired}
                                    onCheckedChange={(checked) => setKycRequired(checked === true)}
                                    disabled={isSubmitting}
                                    className="mt-0.5"
                                />
                                <Label htmlFor="kyc-required" className="font-normal leading-snug">
                                    Customer lookup / KYC required before taking an order
                                </Label>
                            </div>
                            <Input
                                id="customer-lookup"
                                value={customerLookup}
                                onChange={(e) => setCustomerLookup(e.target.value)}
                                placeholder="Optional note on how to look up / verify customers"
                                disabled={isSubmitting}
                            />
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="pricing-source">Pricing source</Label>
                            <Select
                                value={pricingSource}
                                onValueChange={setPricingSource}
                                disabled={isSubmitting}
                            >
                                <SelectTrigger id="pricing-source">
                                    <SelectValue placeholder="Select pricing source" />
                                </SelectTrigger>
                                <SelectContent>
                                    {PRICING_OPTIONS.map((option) => (
                                        <SelectItem key={option.value} value={option.value}>
                                            {option.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="persona-name">Persona name</Label>
                            <Input
                                id="persona-name"
                                value={personaName}
                                onChange={(e) => setPersonaName(e.target.value)}
                                placeholder="e.g. Riya"
                                disabled={isSubmitting}
                            />
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="language">Language</Label>
                            <Input
                                id="language"
                                value={language}
                                onChange={(e) => setLanguage(e.target.value)}
                                placeholder="e.g. Hinglish, Hindi, English"
                                disabled={isSubmitting}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="voice">Voice</Label>
                            <Input
                                id="voice"
                                value={voice}
                                onChange={(e) => setVoice(e.target.value)}
                                placeholder="e.g. Warm female, energetic male"
                                disabled={isSubmitting}
                            />
                        </div>

                        <div className="space-y-2 sm:col-span-2">
                            <Label htmlFor="goal">Primary goal</Label>
                            <Input
                                id="goal"
                                value={goal}
                                onChange={(e) => setGoal(e.target.value)}
                                placeholder="e.g. Take a confirmed order and a delivery date"
                                disabled={isSubmitting}
                            />
                        </div>

                        <div className="space-y-2 sm:col-span-2">
                            <Label htmlFor="objections">Common objections</Label>
                            <Textarea
                                id="objections"
                                value={objections}
                                onChange={(e) => setObjections(e.target.value)}
                                placeholder="Price too high, already stocked, slow delivery…"
                                className="min-h-[80px]"
                                disabled={isSubmitting}
                            />
                        </div>

                        <div className="space-y-2 sm:col-span-2">
                            <Label htmlFor="cross-sell">Cross-sell rules</Label>
                            <Textarea
                                id="cross-sell"
                                value={crossSell}
                                onChange={(e) => setCrossSell(e.target.value)}
                                placeholder="e.g. If they order tea, offer biscuits in the same pack range."
                                className="min-h-[80px]"
                                disabled={isSubmitting}
                            />
                        </div>

                        <div className="space-y-2 sm:col-span-2">
                            <Label htmlFor="fulfillment">Fulfillment / next steps</Label>
                            <Textarea
                                id="fulfillment"
                                value={fulfillment}
                                onChange={(e) => setFulfillment(e.target.value)}
                                placeholder="What happens after the call — dispatch, confirmation SMS, follow-up…"
                                className="min-h-[80px]"
                                disabled={isSubmitting}
                            />
                        </div>
                    </div>
                </div>

                {/* Error panel */}
                {error && (
                    <div className="mt-6 rounded-2xl border border-destructive/40 bg-destructive/10 p-4">
                        <p className="text-small font-medium text-destructive">{error}</p>
                    </div>
                )}

                {/* CTA */}
                <div className="mt-6 flex flex-col items-start gap-2">
                    <Button
                        size="lg"
                        onClick={handleSubmit}
                        disabled={!canSubmit}
                        className="bg-cta text-cta-foreground hover:bg-cta/90"
                    >
                        {isSubmitting ? (
                            <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                Building your agent… this can take ~30–60s
                            </>
                        ) : (
                            <>
                                <Sparkles className="mr-2 h-4 w-4" />
                                Build my agent
                            </>
                        )}
                    </Button>
                    {!authReady && (
                        <p className="text-small text-muted-foreground">Preparing your session…</p>
                    )}
                </div>

                {/* Templates — "Or start from a template" */}
                <div className="mt-14 mb-6">
                    <p className="text-eyebrow text-primary">Templates</p>
                    <h2 className="text-h3 mt-1">Or start from a template</h2>
                    <p className="text-body mt-1 text-muted-foreground">
                        Pick a ready-made agent and fill in your business details.
                    </p>
                </div>

                {templatesError && (
                    <p className="text-small text-destructive mb-4">{templatesError}</p>
                )}

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    {templatesLoading
                        ? Array.from({ length: 4 }).map((_, i) => (
                              <Card
                                  key={i}
                                  className="rounded-2xl border-border/60 bg-card shadow-[var(--shadow-card)]"
                              >
                                  <CardHeader>
                                      <div className="flex items-center gap-3">
                                          <Skeleton className="h-10 w-10 shrink-0 rounded-xl" />
                                          <Skeleton className="h-5 w-40" />
                                      </div>
                                      <Skeleton className="mt-1 h-4 w-full" />
                                      <Skeleton className="h-4 w-2/3" />
                                  </CardHeader>
                              </Card>
                          ))
                        : templates.map((template) => {
                              const Icon = TEMPLATE_ICONS[template.id] ?? Target;
                              return (
                                  <Card
                                      key={template.id}
                                      role="button"
                                      tabIndex={0}
                                      onClick={() => openTemplateDialog(template)}
                                      onKeyDown={(e) => {
                                          if (e.key === "Enter" || e.key === " ") {
                                              e.preventDefault();
                                              openTemplateDialog(template);
                                          }
                                      }}
                                      className="group cursor-pointer rounded-2xl border-border/60 bg-card shadow-[var(--shadow-card)] transition-all duration-200 hover:-translate-y-0.5 hover:border-border hover:shadow-[var(--shadow-pop)] focus-visible:ring-1 focus-visible:ring-ring outline-none"
                                  >
                                      <CardHeader>
                                          <div className="flex items-center gap-3">
                                              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border border-border/60 bg-muted text-muted-foreground transition-colors duration-200 group-hover:border-primary/30 group-hover:bg-accent group-hover:text-primary">
                                                  <Icon className="h-[18px] w-[18px]" />
                                              </div>
                                              <CardTitle className="text-base">{template.name}</CardTitle>
                                          </div>
                                          <CardDescription className="pt-1">
                                              {template.description}
                                          </CardDescription>
                                      </CardHeader>
                                  </Card>
                              );
                          })}
                </div>
            </div>

            {/* Template details dialog */}
            <Dialog
                open={selectedTemplate !== null}
                onOpenChange={(open) => {
                    if (!open && !isCreatingTemplate) setSelectedTemplate(null);
                }}
            >
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>{selectedTemplate?.name}</DialogTitle>
                        <DialogDescription>
                            Tell us about your business so we can personalise this agent.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4 py-2">
                        <div className="space-y-2">
                            <Label htmlFor="template-business-name">Business name</Label>
                            <Input
                                id="template-business-name"
                                placeholder="e.g. Sunrise Homes"
                                value={templateBusinessName}
                                onChange={(e) => setTemplateBusinessName(e.target.value)}
                                disabled={isCreatingTemplate}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="template-industry">Industry (optional)</Label>
                            <Input
                                id="template-industry"
                                placeholder="e.g. Real estate"
                                value={templateIndustry}
                                onChange={(e) => setTemplateIndustry(e.target.value)}
                                disabled={isCreatingTemplate}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="template-details">Business details (optional)</Label>
                            <Textarea
                                id="template-details"
                                placeholder="What you sell, offers, prices, locations — anything the agent should know."
                                value={templateDetails}
                                onChange={(e) => setTemplateDetails(e.target.value)}
                                className="min-h-[90px]"
                                disabled={isCreatingTemplate}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="template-language">Language</Label>
                            <Select
                                value={templateLanguage}
                                onValueChange={setTemplateLanguage}
                                disabled={isCreatingTemplate}
                            >
                                <SelectTrigger id="template-language">
                                    <SelectValue placeholder="Select language" />
                                </SelectTrigger>
                                <SelectContent>
                                    {LANGUAGE_OPTIONS.map((option) => (
                                        <SelectItem key={option.value} value={option.value}>
                                            {option.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        {dialogError && <p className="text-sm text-destructive">{dialogError}</p>}
                    </div>

                    <DialogFooter>
                        <Button
                            onClick={handleTemplateCreate}
                            disabled={isCreatingTemplate || !templateBusinessName.trim()}
                            className="w-full"
                        >
                            {isCreatingTemplate ? (
                                <>
                                    <Loader2 className="h-4 w-4 animate-spin mr-2" />
                                    Creating agent…
                                </>
                            ) : (
                                "Create agent"
                            )}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
