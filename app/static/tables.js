// 表格：点表头排序，数字列按数值比较
function cellText(row, index) {
  const cell = row.cells[index];
  return cell ? cell.textContent.trim() : "";
}

function cellNumber(text) {
  const cleaned = text.replace(/,/g, "").replace(/[^\d.-]/g, "");
  const value = Number(cleaned);
  return Number.isNaN(value) ? null : value;
}

function sortBy(table, index, header) {
  const body = table.tBodies[0];
  if (!body) return;
  const rows = Array.from(body.rows);
  const numeric = header.dataset.sort === "num";
  const direction = header.dataset.dir === "asc" ? "desc" : "asc";
  header.dataset.dir = direction;
  const factor = direction === "asc" ? 1 : -1;
  rows.sort((left, right) => {
    const aText = cellText(left, index);
    const bText = cellText(right, index);
    if (numeric) {
      const a = cellNumber(aText);
      const b = cellNumber(bText);
      if (a === null && b === null) return 0;
      if (a === null) return 1;
      if (b === null) return -1;
      return (a - b) * factor;
    }
    return aText.localeCompare(bText, "zh-CN") * factor;
  });
  for (const row of rows) body.append(row);
  for (const node of table.querySelectorAll("thead th")) {
    node.removeAttribute("aria-sort");
  }
  header.setAttribute("aria-sort", direction === "asc" ? "ascending" : "descending");
}

export function initTables() {
  for (const table of document.querySelectorAll("table[data-sortable]")) {
    const headers = table.querySelectorAll("thead th");
    headers.forEach((header, index) => {
      if (header.dataset.nosort !== undefined) return;
      header.tabIndex = 0;
      header.addEventListener("click", () => sortBy(table, index, header));
      header.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          sortBy(table, index, header);
        }
      });
    });
  }
}
