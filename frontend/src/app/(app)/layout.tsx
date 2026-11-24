import { session } from "@/lib/server/session";
import { Shell } from "@/components/shell";
import { strings as s } from "@/lib/strings";
export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { user, tenant } = await session();
  return (
    <Shell user={user} tenantName={tenant?.name || s.nav.platform}>
      {children}
    </Shell>
  );
}
