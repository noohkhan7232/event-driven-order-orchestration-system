import { OrderDetail } from "@/components/order-detail";
import { session } from "@/lib/server/session";
import { redirect } from "next/navigation";
export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { tenant } = await session();
  if (!tenant) redirect("/platform");
  return <OrderDetail tenant={tenant.id} id={(await params).id} />;
}
