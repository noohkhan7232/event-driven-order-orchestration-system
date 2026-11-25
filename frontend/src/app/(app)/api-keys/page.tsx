import { ApiKeys } from "@/components/api-keys";
import { ownerSession } from "@/lib/server/workspace";
export default async function Page() {
  const { tenant } = await ownerSession();
  return <ApiKeys tenant={tenant.id} />;
}
