import type { Category } from "./types";

/** Leaf categories only, grouped under their parent (a parent with sub-categories is not selectable). */
export function CategorySelect(props: {
  categories: Category[];
  value: number | null;
  disabled?: boolean;
  onChange: (id: number) => void;
}) {
  const { categories, value, disabled, onChange } = props;
  const parents = categories.filter((c) => c.parent_id === null && c.kind === "expense");
  const childrenOf = (id: number) => categories.filter((c) => c.parent_id === id);
  return (
    <select
      className="category-select"
      value={value ?? ""}
      disabled={disabled}
      onChange={(e) => onChange(Number(e.target.value))}
    >
      <option value="" disabled>
        — เลือกหมวด —
      </option>
      {parents.map((p) => {
        const kids = childrenOf(p.id);
        if (kids.length === 0) {
          return (
            <option key={p.id} value={p.id}>
              {p.name_th}
            </option>
          );
        }
        return (
          <optgroup key={p.id} label={p.name_th}>
            {kids.map((k) => (
              <option key={k.id} value={k.id}>
                {k.name_th}
              </option>
            ))}
          </optgroup>
        );
      })}
    </select>
  );
}
