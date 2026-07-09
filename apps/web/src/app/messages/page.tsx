import type { Metadata } from "next";

import { ClientInbox } from "@/components/messages/ClientInbox";
import { PublicFooter } from "@/components/site/PublicFooter";
import { PublicHeader } from "@/components/site/PublicHeader";
import { SkipToContent } from "@/components/site/SkipToContent";

export const metadata: Metadata = { title: "Messages" };

export default function MessagesPage(): JSX.Element {
  return (
    <>
      <SkipToContent />
      <PublicHeader />
      <main
        id="main-content"
        className="mx-auto flex max-w-3xl flex-col gap-6 px-6 py-10"
      >
        <div>
          <h1 className="text-2xl font-semibold text-ink-primary">Messages</h1>
          <p className="mt-1 text-sm text-ink-secondary">
            Conversations with your SHIELD analyst, organised by assessment.
            Open a thread to read and reply.
          </p>
        </div>
        <ClientInbox />
      </main>
      <PublicFooter />
    </>
  );
}
