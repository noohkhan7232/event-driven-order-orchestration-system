import { AuthForm } from "@/components/auth-form";
import { strings as s } from "@/lib/strings";
export default function Register() {
  return (
    <>
      <h1 className="text-2xl font-bold tracking-tight">{s.register}</h1>
      <p className="mb-8 mt-2 text-sm text-slate-600">{s.registerHint}</p>
      <AuthForm mode="register" />
    </>
  );
}
