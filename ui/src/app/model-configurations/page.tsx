
import { PageShell } from "@/components/layout/PageShell";
import ModelConfigurationV2 from "@/components/ModelConfigurationV2";
import { SETTINGS_DOCUMENTATION_URLS } from "@/constants/documentation";

interface ServiceConfigurationPageProps {
    searchParams?: Promise<{
        action?: string | string[];
    }>;
}

export default async function ServiceConfigurationPage({ searchParams }: ServiceConfigurationPageProps) {
    const params = searchParams ? await searchParams : {};
    const action = Array.isArray(params.action) ? params.action[0] : params.action;

    return (
        <PageShell width="default">
            <ModelConfigurationV2
                docsUrl={SETTINGS_DOCUMENTATION_URLS.modelOverrides}
                initialAction={action}
            />
        </PageShell>
    );
}
