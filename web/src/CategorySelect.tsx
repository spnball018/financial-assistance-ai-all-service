import type { Account, Category } from "./types";

export type Choice = { category_id: number } | { transfer_account_id: number };

/** Leaf expense categories grouped under their parent, then "transfer to my account" choices.
 *  Values are "c:<id>" / "a:<id>" so one select can hold both. */
export function CategorySelect(props: {
  categories: Category[];
  accounts: Account[];
  fromAccountId: number;
  categoryId: number | null;
  transferAccountId: number | null;
  disabled?: boolean;
  onChange: (choice: Choice) => void;
}) {
  const { categories, accounts, fromAccountId, categoryId, transferAccountId, disabled, onChange } = props;
  const parents = categories.filter((c) => c.parent_id === null && c.kind === "expense");
  const childrenOf = (id: number) => categories.filter((c) => c.parent_id === id);
  const targets = accounts.filter((a) => a.id !== fromAccountId && (a.is_active || a.id === transferAccountId));
  const value = transferAccountId ? `a:${transferAccountId}` : categoryId ? `c:${categoryId}` : "";

  return (
    <select
      className={transferAccountId ? "category-select is-transfer" : "category-select"}
      value={value}
      disabled={disabled}
      onChange={(e) => {
        const [kind, id] = e.target.value.split(":");
        onChange(kind === "a" ? { transfer_account_id: Number(id) } : { category_id: Number(id) });
      }}
    >
      <option value="" disabled>
        — เลือกหมวด —
      </option>
      {parents.map((p) => {
        const kids = childrenOf(p.id);
        if (kids.length === 0) {
          return (
            <option key={p.id} value={`c:${p.id}`}>
              {p.name_th}
            </option>
          );
        }
        return (
          <optgroup key={p.id} label={p.name_th}>
            {kids.map((k) => (
              <option key={k.id} value={`c:${k.id}`}>
                {k.name_th}
              </option>
            ))}
          </optgroup>
        );
      })}
      <optgroup label="โอนเข้าบัญชีตัวเอง (ไม่นับเป็นรายจ่าย)">
        {targets.map((a) => (
          <option key={a.id} value={`a:${a.id}`}>
            → {a.name}
          </option>
        ))}
      </optgroup>
    </select>
  );
}
