import { PackageCheck } from "lucide-react";
import { strings as s } from "@/lib/strings";
export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <main className="grid min-h-screen place-items-center px-5 py-12">
      <div className="w-full max-w-md">
        <div className="mb-8 flex items-center justify-center gap-2 font-bold text-teal-900">
          <PackageCheck aria-hidden />
          {s.brand}
        </div>
        <div className="rounded-2xl border border-slate-200 bg-white p-7 shadow-sm sm:p-10">
          {children}
        </div>
        <p className="mt-6 text-center text-xs text-slate-600">{s.tagline}</p>
      </div>
    </main>
  );
}
