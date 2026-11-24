import { redirect } from "next/navigation";
import { session } from "@/lib/server/session";
import { EmptyState } from "@/components/empty-state";
import { strings as s } from "@/lib/strings";
export default async function Dashboard() {
  const { user } = await session();
  if (user.is_platform_admin) redirect("/platform");
  return (
    <>
      <p className="mb-3 text-xs font-bold uppercase tracking-widest text-teal-800">
        {s.foundation}
      </p>
      <h1 className="text-3xl font-bold tracking-tight">{s.homeTitle}</h1>
      <p className="mb-10 mt-3 max-w-xl text-slate-600">{s.homeHint}</p>
      <EmptyState />
    </>
  );
}
