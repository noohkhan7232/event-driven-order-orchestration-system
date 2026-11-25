import { Webhooks } from "@/components/webhooks";
import { ownerSession } from "@/lib/server/workspace";
export default async function Page() {
  const { tenant } = await ownerSession();
  return <Webhooks tenant={tenant.id} />;
}
