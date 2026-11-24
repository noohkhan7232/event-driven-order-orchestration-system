"use client";
import Link from "next/link";
import { useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import * as Dialog from "@radix-ui/react-dialog";
import { Menu, X, ArrowUpRight, LogOut, PackageCheck } from "lucide-react";
import { toast } from "./toasts";
import { api } from "@/lib/api/client";
import { navigation, type User } from "@/lib/roles";
import { strings as s } from "@/lib/strings";
import { Button } from "./ui/button";
export function Shell({
  user,
  tenantName,
  children,
}: {
  user: User;
  tenantName: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const router = useRouter();
  const current = usePathname();
  const links = (
    <nav aria-label={s.menu} className="space-y-1">
      {navigation(user).map((item) =>
        ["/dashboard", "/platform"].includes(item.href) ? (
          <Link
            key={item.href}
            href={item.href}
            aria-current={current === item.href ? "page" : undefined}
            onClick={() => setOpen(false)}
            className="nav-link"
          >
            {item.label}
            <ArrowUpRight size={16} aria-hidden />
          </Link>
        ) : (
          <span
            key={item.href}
            className="nav-link opacity-60"
            aria-disabled="true"
            title={s.notBuilt}
          >
            {item.label}
          </span>
        ),
      )}
    </nav>
  );
  async function logout() {
    try {
      await api("/bff/auth/logout", { method: "POST" });
      router.replace("/login");
      router.refresh();
    } catch {
      toast.error(s.errors.upstream_unavailable);
    }
  }
  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[256px_minmax(0,1fr)]">
      <a className="skip-link" href="#content">
        {s.skip}
      </a>
      <aside className="hidden border-r border-slate-200 bg-white p-6 lg:block">
        <div className="mb-12 flex items-center gap-3 font-bold text-teal-900">
          <PackageCheck aria-hidden />
          {s.brand}
        </div>
        {links}
        <p className="mt-10 text-xs text-slate-600">{s.tagline}</p>
      </aside>
      <div className="min-w-0">
        <header className="flex min-h-20 items-center justify-between gap-3 border-b border-slate-200 bg-white px-5 sm:px-10">
          <div className="flex min-w-0 items-center gap-3">
            <Dialog.Root open={open} onOpenChange={setOpen}>
              <Dialog.Trigger asChild>
                <Button
                  variant="ghost"
                  className="lg:hidden"
                  aria-label={s.menu}
                >
                  <Menu aria-hidden />
                </Button>
              </Dialog.Trigger>
              <Dialog.Portal>
                <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/40" />
                <Dialog.Content className="fixed inset-y-0 left-0 z-50 w-72 bg-white p-6">
                  <Dialog.Title className="mb-8 text-lg font-bold">
                    {s.brand}
                  </Dialog.Title>
                  <Dialog.Description className="sr-only">
                    {s.menu}
                  </Dialog.Description>
                  <Dialog.Close asChild>
                    <Button
                      variant="ghost"
                      aria-label={s.close}
                      className="absolute right-3 top-3"
                    >
                      <X aria-hidden />
                    </Button>
                  </Dialog.Close>
                  {links}
                </Dialog.Content>
              </Dialog.Portal>
            </Dialog.Root>
            <div className="min-w-0">
              <p className="text-xs text-slate-600">
                {user.is_platform_admin ? s.nav.platform : s.store}
              </p>
              <p className="truncate font-semibold">{tenantName}</p>
            </div>
          </div>
          <details className="relative min-w-0 max-w-[45%] shrink-0">
            <summary className="block cursor-pointer truncate rounded-lg p-2 text-sm font-medium focus-visible:outline-2 focus-visible:outline-teal-800">
              {user.full_name}
            </summary>
            <div className="absolute right-0 z-20 mt-2 min-w-48 rounded-xl border border-slate-200 bg-white p-3 shadow-lg">
              <p className="mb-2 text-xs text-slate-600">{s.account}</p>
              <Button variant="ghost" onClick={logout}>
                <LogOut size={16} aria-hidden />
                {s.logout}
              </Button>
            </div>
          </details>
        </header>
        <main
          id="content"
          tabIndex={-1}
          className="mx-auto max-w-6xl px-5 py-10 sm:px-10"
        >
          {children}
        </main>
      </div>
    </div>
  );
}
