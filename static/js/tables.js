/**
 * Tresorapide — Lightweight table enhancements
 * Click column headers to sort. Type in filter input to search rows.
 * Add class "enhanced-table" to any <table> to activate.
 * Tables with data-running-balance="true" recalculate Balance columns after sort.
 */
(function () {
    "use strict";

    function init() {
        document.querySelectorAll("table.enhanced-table").forEach(enhanceTable);
    }

    function enhanceTable(table) {
        var thead = table.querySelector("thead");
        var tbody = table.querySelector("tbody");
        if (!thead || !tbody) return;

        // --- Filter input ---
        var wrapper = table.closest(".table-responsive") || table.parentElement;
        var filterInput = document.createElement("input");
        filterInput.type = "text";
        filterInput.placeholder = "Filtrer le tableau…";
        filterInput.className = "table-filter-input";
        filterInput.style.cssText = "width:100%;padding:0.5rem 0.75rem;margin-bottom:0.5rem;border:1px solid #ccc;border-radius:4px;font-size:0.9rem;box-sizing:border-box;";
        wrapper.insertBefore(filterInput, table);

        filterInput.addEventListener("input", function () {
            var term = filterInput.value.toLowerCase();
            var rows = tbody.querySelectorAll("tr");
            rows.forEach(function (row) {
                var text = row.textContent.toLowerCase();
                row.style.display = text.indexOf(term) !== -1 ? "" : "none";
            });
            if (table.getAttribute("data-running-balance") === "true") {
                recalcRunningBalances(table, tbody);
            }
        });

        // --- Sortable headers ---
        var headers = thead.querySelectorAll("th");
        headers.forEach(function (th, colIdx) {
            th.style.cursor = "pointer";
            th.style.userSelect = "none";
            th.title = "Cliquer pour trier";
            var arrow = document.createElement("span");
            arrow.className = "sort-arrow";
            arrow.style.marginLeft = "0.3rem";
            arrow.style.fontSize = "0.7rem";
            th.appendChild(arrow);

            th.addEventListener("click", function () {
                sortTable(table, tbody, colIdx, th, headers);
            });
        });

        // --- Resizable columns ---
        headers.forEach(function (th) {
            var grip = document.createElement("div");
            grip.className = "col-resize-grip";
            grip.style.cssText = "position:absolute;right:0;top:0;bottom:0;width:5px;cursor:col-resize;";
            th.style.position = "relative";
            th.appendChild(grip);

            var startX, startWidth;
            grip.addEventListener("mousedown", function (e) {
                startX = e.clientX;
                startWidth = th.offsetWidth;
                e.preventDefault();

                function onMouseMove(ev) {
                    th.style.width = Math.max(40, startWidth + (ev.clientX - startX)) + "px";
                    th.style.minWidth = th.style.width;
                }
                function onMouseUp() {
                    document.removeEventListener("mousemove", onMouseMove);
                    document.removeEventListener("mouseup", onMouseUp);
                }
                document.addEventListener("mousemove", onMouseMove);
                document.addEventListener("mouseup", onMouseUp);
            });
        });
    }

    function sortTable(table, tbody, colIdx, activeTh, allHeaders) {
        var rows = Array.from(tbody.querySelectorAll("tr"));
        var currentDir = activeTh.getAttribute("data-sort-dir") || "none";
        var newDir = currentDir === "asc" ? "desc" : "asc";

        // Reset all arrows
        allHeaders.forEach(function (h) {
            h.setAttribute("data-sort-dir", "none");
            var a = h.querySelector(".sort-arrow");
            if (a) a.textContent = "";
        });

        activeTh.setAttribute("data-sort-dir", newDir);
        var arrow = activeTh.querySelector(".sort-arrow");
        if (arrow) arrow.textContent = newDir === "asc" ? " ▲" : " ▼";

        rows.sort(function (a, b) {
            var cellA = getCellText(a, colIdx);
            var cellB = getCellText(b, colIdx);

            // Try numeric comparison
            var numA = parseNumber(cellA);
            var numB = parseNumber(cellB);
            if (!isNaN(numA) && !isNaN(numB)) {
                return newDir === "asc" ? numA - numB : numB - numA;
            }

            // Try date comparison (YYYY-MM-DD)
            var dateA = Date.parse(cellA);
            var dateB = Date.parse(cellB);
            if (!isNaN(dateA) && !isNaN(dateB)) {
                return newDir === "asc" ? dateA - dateB : dateB - dateA;
            }

            // String comparison
            var cmp = cellA.localeCompare(cellB, "fr", { sensitivity: "base" });
            return newDir === "asc" ? cmp : -cmp;
        });

        rows.forEach(function (row) {
            tbody.appendChild(row);
        });

        // Recalculate running balances if applicable
        if (table.getAttribute("data-running-balance") === "true") {
            recalcRunningBalances(table, tbody);
        }
    }

    /**
     * Recompute Balance and Balance-15% columns based on current row order.
     * Reads per-row data-amount and data-trace attributes and writes into
     * .balance-cell and .balance-15-cell elements.
     */
    function recalcRunningBalances(table, tbody) {
        var budgetTotal = parseFloat(table.getAttribute("data-budget-total"));
        var budgetMinus = parseFloat(table.getAttribute("data-budget-minus-imprevues"));
        if (isNaN(budgetTotal) || isNaN(budgetMinus)) return;

        var cumulative = 0;
        var cumulativeNonImprevues = 0;
        var rows = tbody.querySelectorAll("tr");

        rows.forEach(function (row) {
            if (row.style.display === "none") return; // skip filtered rows
            var amount = parseFloat(row.getAttribute("data-amount"));
            var trace = row.getAttribute("data-trace");
            if (isNaN(amount)) return;

            cumulative += amount;
            if (trace !== "0") {
                cumulativeNonImprevues += amount;
            }

            var balance = budgetTotal - cumulative;
            var balance15 = budgetMinus - cumulativeNonImprevues;

            var balCell = row.querySelector(".balance-cell");
            var bal15Cell = row.querySelector(".balance-15-cell");
            if (balCell) {
                balCell.textContent = formatMoney(balance);
                balCell.classList.toggle("text-negative", balance < 0);
            }
            if (bal15Cell) {
                bal15Cell.textContent = formatMoney(balance15);
                bal15Cell.classList.toggle("text-negative", balance15 < 0);
            }
        });
    }

    function formatMoney(val) {
        return val.toFixed(2) + " $";
    }

    function getCellText(row, idx) {
        var cell = row.cells[idx];
        if (!cell) return "";
        return (cell.getAttribute("data-sort-value") || cell.textContent || "").trim();
    }

    function parseNumber(str) {
        // Handle "$", "," and spaces in numbers: "1 234.56 $" → 1234.56
        var cleaned = str.replace(/[$€\s]/g, "").replace(/,/g, ".").replace(/\u00a0/g, "");
        return parseFloat(cleaned);
    }

    // Initialize on DOM ready
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
