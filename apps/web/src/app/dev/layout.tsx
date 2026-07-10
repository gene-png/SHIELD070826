import { getServerSession } from "next-auth";
import { notFound, redirect } from "next/navigation";

import { authOptions } from "@/lib/auth/options";

/**
 * Gate for /dev/* tooling (e.g. the questionnaire-renderer preview).
 *
 * These pages are internal QA aids, not product surfaces. Previously the
 * preview shipped fully unauthenticated (a "use client" page with no auth
 * check, no layout, no middleware). We gate it behind admin auth here rather
 * than excluding it from the build: it stays usable for the team, and an
 * unauthenticated or non-admin visitor can neither see it nor learn it exists
 * (404, not a redirect that would confirm the route).
 */
export default async function DevLayout({
  children,
}: {
  children: React.ReactNode;
}): Promise<JSX.Element> {
  const session = await getServerSession(authOptions);
  if (!session) {
    redirect("/sign-in?callbackUrl=/dev/questionnaire-preview");
  }
  if (session.role !== "admin") {
    notFound();
  }
  return <>{children}</>;
}
