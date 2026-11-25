"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import * as Dialog from "@radix-ui/react-dialog";
import { strings as s } from "@/lib/strings";
import { Button } from "./ui/button";
import { Modal, Problem } from "./workspace";
import { localDate } from "@/lib/format";
import { toast } from "./toasts";
export function useSecretDisclosure() {
  const [value, setValue] = useState<string | null>(null);
  const active = useRef(false);
  const generation = useRef(0);
  const path = usePathname();
  const forget = useCallback(() => {
    generation.current++;
    setValue(null);
  }, []);
  const publish = useCallback((secret: string) => {
    if (active.current && document.visibilityState === "visible")
      setValue(secret);
  }, []);
  const begin = useCallback(() => {
    const ticket = ++generation.current;
    return (secret: string) => {
      if (generation.current === ticket) publish(secret);
    };
  }, [publish]);
  useEffect(() => {
    active.current = true;
    const visibility = () => {
      active.current = document.visibilityState === "visible";
      if (!active.current) forget();
    };
    const leave = () => {
      active.current = false;
      forget();
    };
    const enter = () => {
      active.current = document.visibilityState === "visible";
    };
    window.addEventListener("pagehide", leave);
    window.addEventListener("pageshow", enter);
    window.addEventListener("dashboard:session-changed", leave);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      active.current = false;
      forget();
      window.removeEventListener("pagehide", leave);
      window.removeEventListener("pageshow", enter);
      window.removeEventListener("dashboard:session-changed", leave);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [path, forget]);
  return { value, publish, begin, forget };
}
export async function copy(value: string) {
  try {
    await navigator.clipboard.writeText(value);
    toast.success(s.ops.copied);
  } catch {
    toast.error(s.ops.copyFailed);
  }
}
export function SecretOnce({
  value,
  onForget,
}: {
  value: string;
  onForget: () => void;
}) {
  const [saved, setSaved] = useState(false);
  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open && saved) onForget();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-slate-950/40" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90dvh] w-[calc(100%-2rem)] max-w-xl -translate-x-1/2 -translate-y-1/2 overflow-auto rounded-2xl bg-white p-6 shadow-xl">
          <Dialog.Title className="text-xl font-bold">
            {s.ops.secretTitle}
          </Dialog.Title>
          <Dialog.Description className="my-4 text-sm text-slate-600">
            {s.ops.secretHint}
          </Dialog.Description>
          <label>
            {s.ops.secretValue}
            <textarea
              readOnly
              autoComplete="off"
              spellCheck={false}
              value={value}
              className="mt-2 break-all"
            />
          </label>
          <div className="my-4">
            <Button variant="outline" onClick={() => void copy(value)}>
              {s.ops.copy}
            </Button>
          </div>
          <label className="flex items-center gap-3">
            <input
              type="checkbox"
              checked={saved}
              onChange={(event) => setSaved(event.target.checked)}
            />
            {s.ops.saved}
          </label>
          <Button className="mt-4" disabled={!saved} onClick={onForget}>
            {s.ops.done}
          </Button>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
export function ConfirmAction({
  title,
  hint,
  perform,
  onClose,
}: {
  title: string;
  hint: string;
  perform: () => Promise<void>;
  onClose: () => void;
}) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<Error>();
  return (
    <Modal
      title={title}
      onClose={() => {
        if (!pending) onClose();
      }}
    >
      <p className="mb-5 break-words text-sm text-slate-600">{hint}</p>
      {error && <Problem error={error} />}
      <Button
        className="mt-4"
        disabled={pending}
        onClick={async () => {
          if (pending) return;
          setPending(true);
          setError(undefined);
          try {
            await perform();
            onClose();
          } catch (cause) {
            setError(cause as Error);
          } finally {
            setPending(false);
          }
        }}
      >
        {pending ? s.ops.busy : s.ops.confirm}
      </Button>
    </Modal>
  );
}
export function Stamp({ value }: { value: string | null }) {
  if (!value) return <span>{s.ops.never}</span>;
  const date = localDate(value);
  return (
    <time dateTime={value} title={date.tooltip}>
      {date.label}
    </time>
  );
}
export function State({ value }: { value: string }) {
  return (
    <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold text-slate-800">
      {s.ops.statusLabels[value] || s.orders.unknown}
    </span>
  );
}
