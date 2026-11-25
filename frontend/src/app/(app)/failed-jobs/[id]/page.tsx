import { FailedJobDetail } from "@/components/failed-jobs";
import { ownerSession } from "@/lib/server/workspace";
export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { tenant } = await ownerSession();
  return <FailedJobDetail tenant={tenant.id} id={(await params).id} />;
}
