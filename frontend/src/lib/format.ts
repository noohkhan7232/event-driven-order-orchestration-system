export const money = (amount: number | string, currency = "INR") =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency }).format(
    Number(amount),
  );
export const localDate = (date: string, locale?: string) => ({
  label: new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(date)),
  tooltip: new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    dateStyle: "full",
    timeStyle: "long",
  }).format(new Date(date)),
});
