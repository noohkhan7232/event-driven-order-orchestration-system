import { Notifications } from "@/components/notifications";
import { ownerSession } from "@/lib/server/workspace";
export default async function Page() {
  const { tenant } = await ownerSession();
  return <Notifications tenant={tenant.id} />;
}
