"use client";
import { cloneElement, useId } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Button } from "@/components/ui/button";
import { strings as s } from "@/lib/strings";
import { ApiError } from "@/lib/api/client";
import { ErrorNotice } from "./error-notice";
export function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90dvh] w-[calc(100%-2rem)] max-w-xl -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl bg-white p-6 shadow-xl">
          <Dialog.Title className="text-xl font-bold">{title}</Dialog.Title>
          <Dialog.Description className="mt-2 mb-6 text-sm text-slate-600">
            {s.orders.confirmation}
          </Dialog.Description>
          {children}
          <Button variant="ghost" onClick={onClose} className="mt-4">
            {s.orders.dismiss}
          </Button>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
export function Field({
  label,
  children,
  error,
}: {
  label: string;
  children: React.ReactElement<{
    id?: string;
    "aria-invalid"?: boolean;
    "aria-describedby"?: string;
  }>;
  error?: string;
}) {
  const generatedId = useId();
  const id = children.props.id || generatedId;
  return (
    <div className="space-y-2">
      <label htmlFor={id}>{label}</label>
      {cloneElement(children, {
        id,
        "aria-invalid": !!error,
        "aria-describedby": error ? id + "-error" : undefined,
      })}
      {error && (
        <span id={id + "-error"} className="field-error block" role="alert">
          {error}
        </span>
      )}
    </div>
  );
}
export function Problem({
  error,
  retry,
}: {
  error: Error;
  retry?: () => void;
}) {
  return (
    <div className="space-y-3">
      <ErrorNotice
        error={error instanceof ApiError ? error : new ApiError(0, "unknown")}
      />
      {retry && (
        <Button variant="outline" onClick={retry}>
          {s.retry}
        </Button>
      )}
    </div>
  );
}
export function Badge({ status }: { status: string }) {
  return (
    <span className="inline-flex rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-800">
      {s.statuses[status] || s.orders.unknown}
    </span>
  );
}
export function Pager({
  offset,
  total,
  onChange,
}: {
  offset: number;
  total: number;
  onChange: (n: number) => void;
}) {
  return (
    <nav
      aria-label={s.orders.title}
      className="mt-5 flex flex-wrap items-center justify-between gap-3"
    >
      <Button
        variant="outline"
        disabled={!offset}
        onClick={() => onChange(Math.max(0, offset - 20))}
      >
        {s.orders.previous}
      </Button>
      <span className="text-sm">
        {total ? s.orders.page(offset, total) : "0"}
      </span>
      <Button
        variant="outline"
        disabled={offset + 20 >= total}
        onClick={() => onChange(offset + 20)}
      >
        {s.orders.next}
      </Button>
    </nav>
  );
}
