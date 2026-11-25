import { OrdersList } from "@/components/orders-list";
import { session } from "@/lib/server/session";
import { redirect } from "next/navigation";
export default async function Page() {
  const { tenant } = await session();
  if (!tenant) redirect("/platform");
  return <OrdersList tenant={tenant.id} />;
}
