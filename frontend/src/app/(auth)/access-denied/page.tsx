import Link from "next/link";
import { strings as s } from "@/lib/strings";
export default function AccessDenied() {
  return (
    <>
      <h1 className="text-2xl font-bold">{s.customerTitle}</h1>
      <p className="my-4 text-slate-600">{s.customerHint}</p>
      <Link href="/login" className="underline">
        {s.login}
      </Link>
    </>
  );
}
