import { notFound } from "next/navigation";
import { session } from "@/lib/server/session";
import { strings as s } from "@/lib/strings";
export default async function Platform() {
  const { user } = await session();
  if (!user.is_platform_admin) notFound();
  return (
    <>
      <h1 className="text-3xl font-bold">{s.platformTitle}</h1>
      <p className="mt-3 text-slate-600">{s.platformHint}</p>
    </>
  );
}
