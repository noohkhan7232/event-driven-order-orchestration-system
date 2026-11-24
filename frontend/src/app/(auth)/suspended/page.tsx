import { strings as s } from "@/lib/strings";
export default function Suspended() {
  return (
    <>
      <h1 className="text-2xl font-bold">{s.suspendedTitle}</h1>
      <p className="mt-3 text-slate-600">{s.suspendedHint}</p>
    </>
  );
}
