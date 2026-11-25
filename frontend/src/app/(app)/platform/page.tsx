import { PlatformTenants } from "@/components/platform-tenants";
import { platformSession } from "@/lib/server/workspace";
export default async function Platform() {
  const { user } = await platformSession();
  return <PlatformTenants user={user.id} />;
}
