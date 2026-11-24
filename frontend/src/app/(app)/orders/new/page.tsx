import { CreateOrder } from "@/components/create-order";
import { session } from "@/lib/server/session";
import { redirect } from "next/navigation";
export default async function Page() {
  const { tenant } = await session();
  if (!tenant) redirect("/platform");
  return <CreateOrder />;
}
