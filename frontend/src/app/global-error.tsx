"use client";
import { strings as s } from "@/lib/strings";
export default function GlobalError({ reset }: { reset: () => void }) {
  return (
    <html lang="en">
      <body>
        <main role="alert">
          <h1>{s.errorTitle}</h1>
          <p>{s.errorHint}</p>
          <button onClick={reset}>{s.retry}</button>
        </main>
      </body>
    </html>
  );
}
