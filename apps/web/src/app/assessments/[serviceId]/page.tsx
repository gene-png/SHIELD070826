import type { Metadata } from "next";
import { getServerSession } from "next-auth";
import { redirect } from "next/navigation";

import { AssessmentDetailView } from "@/components/assessments/AssessmentDetailView";
import { PublicFooter } from "@/components/site/PublicFooter";
import { PublicHeader } from "@/components/site/PublicHeader";
import { SkipToContent } from "@/components/site/SkipToContent";
import { authOptions } from "@/lib/auth/options";

export const metadata: Metadata = { title: "Assessment" };

export default async function AssessmentDetailPage({
  params,
}: {
  params: { serviceId: string };
}): Promise<JSX.Element> {
  const session = await getServerSession(authOptions);
  if (!session) {
    const cb = encodeURIComponent(`/assessments/${params.serviceId}`);
    redirect(`/sign-in?callbackUrl=${cb}`);
  }

  return (
    <>
      <SkipToContent />
      <PublicHeader />
      <main id="main-content" className="mx-auto w-full max-w-3xl px-6 py-10">
        <AssessmentDetailView serviceId={params.serviceId} />
      </main>
      <PublicFooter />
    </>
  );
}
