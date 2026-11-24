"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { api, ApiError } from "@/lib/api/client";
import { strings as s } from "@/lib/strings";
import { Button } from "./ui/button";
import { ErrorNotice } from "./error-notice";
const schema = z.object({
  email: z.email(s.validation.email),
  password: z.string().min(8, s.validation.password),
  business_name: z.string().optional(),
  owner_name: z.string().optional(),
  slug: z.string().optional(),
  invite_code: z.string().optional(),
});
type Fields = z.infer<typeof schema>;
export function AuthForm({
  mode,
  registered = false,
  expired = false,
}: {
  mode: "login" | "register";
  registered?: boolean;
  expired?: boolean;
}) {
  const router = useRouter();
  const [error, setError] = useState<ApiError | null>(null);
  const [waiting, setWaiting] = useState(false);
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
    setError: fieldError,
  } = useForm<Fields>({ resolver: zodResolver(schema) });
  const isRegister = mode === "register";
  async function submit(values: Fields) {
    if (isRegister) {
      let invalid = false;
      for (const key of ["business_name", "owner_name"] as const) {
        if ((values[key]?.trim().length || 0) < 2) {
          fieldError(key, { message: s.validation.short });
          invalid = true;
        }
      }
      if (invalid) return;
    }
    setError(null);
    try {
      await api("/bff/auth/" + mode, {
        method: "POST",
        body: JSON.stringify(
          isRegister
            ? {
                business_name: values.business_name,
                owner_name: values.owner_name,
                owner_email: values.email,
                owner_password: values.password,
                ...(values.slug ? { slug: values.slug } : {}),
                ...(values.invite_code
                  ? { invite_code: values.invite_code }
                  : {}),
              }
            : { email: values.email, password: values.password },
        ),
      });
      router.replace(isRegister ? "/login?registered=1" : "/dashboard");
      router.refresh();
    } catch (error) {
      setError(error instanceof ApiError ? error : new ApiError(0, "unknown"));
    }
  }
  const fields: {
    key: keyof Fields;
    label: string;
    type?: string;
    autoComplete?: string;
  }[] = [
    ...(isRegister
      ? [
          {
            key: "business_name" as const,
            label: s.business,
            autoComplete: "organization",
          },
          { key: "owner_name" as const, label: s.owner, autoComplete: "name" },
        ]
      : []),
    { key: "email", label: s.email, type: "email", autoComplete: "email" },
    {
      key: "password",
      label: s.password,
      type: "password",
      autoComplete: isRegister ? "new-password" : "current-password",
    },
    ...(isRegister
      ? [
          { key: "slug" as const, label: s.slug },
          {
            key: "invite_code" as const,
            label: s.invite,
            type: "password",
            autoComplete: "off",
          },
        ]
      : []),
  ];
  return (
    <form
      method="post"
      action={isRegister ? "/bff/auth/register" : "/bff/auth/login"}
      onSubmit={handleSubmit(submit)}
      noValidate
      className="space-y-5"
    >
      {registered && (
        <p role="status" className="success">
          {s.registered}
        </p>
      )}
      {expired && <p role="status">{s.expired}</p>}
      {fields.map(({ key, label, type = "text", autoComplete }) => (
        <div key={key}>
          <label htmlFor={key}>{label}</label>
          <input
            id={key}
            type={type}
            autoComplete={autoComplete}
            maxLength={key === "password" ? 128 : 160}
            aria-invalid={!!errors[key]}
            aria-describedby={errors[key] ? key + "-error" : undefined}
            {...register(key)}
          />
          {errors[key] && (
            <p id={key + "-error"} className="field-error">
              {errors[key]?.message}
            </p>
          )}
        </div>
      ))}
      {error && (
        <ErrorNotice
          key={error.code + error.retryAfter}
          error={error}
          onWaiting={setWaiting}
        />
      )}
      <Button
        type="submit"
        className="w-full"
        disabled={isSubmitting || waiting}
      >
        {isSubmitting ? s.pending : isRegister ? s.submitRegister : s.login}
      </Button>
      <p className="text-center text-sm text-slate-600">
        {isRegister ? s.hasAccount : s.noAccount}{" "}
        <Link
          className="text-teal-800 underline underline-offset-4"
          href={isRegister ? "/login" : "/tenants/register"}
        >
          {isRegister ? s.login : s.register}
        </Link>
      </p>
    </form>
  );
}
