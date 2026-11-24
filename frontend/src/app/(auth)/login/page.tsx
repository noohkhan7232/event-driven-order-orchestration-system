import { AuthForm } from "@/components/auth-form";
import { strings as s } from "@/lib/strings";
export default async function Login({
  searchParams,
}: {
  searchParams: Promise<{ registered?: string; expired?: string }>;
}) {
  const params = await searchParams;
  return (
    <>
      <h1 className="text-2xl font-bold tracking-tight">{s.welcome}</h1>
      <p className="mb-8 mt-2 text-sm text-slate-600">{s.loginHint}</p>
      <AuthForm
        mode="login"
        registered={params.registered === "1"}
        expired={params.expired === "1"}
      />
    </>
  );
}
