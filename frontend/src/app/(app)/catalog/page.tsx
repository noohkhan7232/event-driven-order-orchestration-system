import { Catalog } from "@/components/catalog";
import { session } from "@/lib/server/session";
import { redirect } from "next/navigation";
export default async function Page() {
  const { tenant } = await session();
  if (!tenant) redirect("/platform");
  return <Catalog tenant={tenant.id} />;
}
