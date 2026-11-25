import { FailedJobs } from "@/components/failed-jobs";
import { ownerSession } from "@/lib/server/workspace";
export default async function Page() {
  const { tenant } = await ownerSession();
  return <FailedJobs tenant={tenant.id} />;
}
