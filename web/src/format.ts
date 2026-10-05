const money = new Intl.NumberFormat("th-TH", { style: "currency", currency: "THB" });
const dateTime = new Intl.DateTimeFormat("th-TH", { timeZone: "Asia/Bangkok", dateStyle: "medium", timeStyle: "short" });
const dateOnly = new Intl.DateTimeFormat("th-TH", { timeZone: "Asia/Bangkok", dateStyle: "medium" });

export const fmtMoney = (n: number) => money.format(n);
export const fmtWhen = (iso: string, hasTime: boolean) => (hasTime ? dateTime : dateOnly).format(new Date(iso));

/** Current month in Bangkok as YYYY-MM. */
export function thisMonth(): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Bangkok", year: "numeric", month: "2-digit" })
    .formatToParts(new Date());
  const y = parts.find((p) => p.type === "year")!.value;
  const m = parts.find((p) => p.type === "month")!.value;
  return `${y}-${m}`;
}

export const CHANNEL_LABEL: Record<string, string> = {
  bill_payment: "จ่ายบิล/สแกน",
  promptpay: "พร้อมเพย์",
  transfer: "โอนเงิน",
  donation: "บริจาค",
};
