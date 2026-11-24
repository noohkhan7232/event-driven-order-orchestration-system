import { PackageOpen } from "lucide-react";
import { strings as s } from "@/lib/strings";
export function EmptyState() {
  return (
    <div className="rounded-2xl border border-dashed border-slate-300 bg-white p-12 text-center">
      <PackageOpen
        size={40}
        className="mx-auto mb-4 text-slate-600"
        aria-hidden
      />
      <h2 className="font-semibold">{s.emptyTitle}</h2>
      <p className="mt-2 text-sm text-slate-600">{s.emptyHint}</p>
    </div>
  );
}
