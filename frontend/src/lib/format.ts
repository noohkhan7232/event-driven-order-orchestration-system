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

// A calendar ETA has no instant: keep its day stable in every visitor timezone.
export const calendarDate = (date: string, locale?: string) => {
  const value = new Date(date + "T00:00:00Z");
  return {
    label: new Intl.DateTimeFormat(locale, {
      dateStyle: "medium",
      timeZone: "UTC",
    }).format(value),
    tooltip:
      new Intl.DateTimeFormat("en-IN", {
        dateStyle: "full",
        timeZone: "UTC",
      }).format(value) + " IST",
  };
};
