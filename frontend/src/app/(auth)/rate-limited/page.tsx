import { RateLimitNotice } from "@/components/rate-limit-notice";
export default async function RateLimited({
  searchParams,
}: {
  searchParams: Promise<{ retry?: string }>;
}) {
  const raw = Number((await searchParams).retry);
  const seconds = Number.isFinite(raw)
    ? Math.max(0, Math.min(3600, Math.ceil(raw)))
    : 60;
  return <RateLimitNotice seconds={seconds} />;
}
