import { strings as s } from "@/lib/strings";
export default function Loading() {
  return (
    <div
      role="status"
      aria-label={s.pending}
      className="space-y-6 motion-safe:animate-pulse"
    >
      <div className="h-10 w-2/3 rounded-xl bg-slate-200" />
      <div className="h-40 rounded-2xl bg-slate-200" />
    </div>
  );
}
