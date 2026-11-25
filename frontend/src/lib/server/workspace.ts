import "server-only";
import { notFound } from "next/navigation";
import { session } from "./session";
export async function ownerSession() {
  const context = await session();
  if (
    context.user.is_platform_admin ||
    context.user.role !== "admin" ||
    !context.tenant
  )
    notFound();
  return { ...context, tenant: context.tenant! };
}
export async function platformSession() {
  const context = await session();
  if (!context.user.is_platform_admin) notFound();
  return context;
}
