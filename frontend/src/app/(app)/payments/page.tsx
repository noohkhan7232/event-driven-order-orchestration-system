import { PaymentSettings } from "@/components/payment-settings";
import { ownerSession } from "@/lib/server/workspace";
export default async function Page() {
  const { tenant } = await ownerSession();
  return <PaymentSettings tenant={tenant.id} />;
}
