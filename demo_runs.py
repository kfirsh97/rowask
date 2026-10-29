"""Two real runs of the agent, recorded from the terminal on 29 Oct 2026.
Used by the web app's demo mode (DEMO=1), so the interface can be shown without a database or API key."""

DEMO_RUNS = {
    "כמה מכירות היו בכל שנה?": {
        "model": "gemini-3.5-flash",
        "seconds": 108.9,
        "steps": [
            {"tool": "get_schema", "args": {}, "result": "schema (3,185 chars)"},
            {"tool": "run_query", "args": {"sql": "SELECT YEAR(Sold_Date) AS Sale_Year, COUNT(*) AS Total_Sales FROM Sales GROUP BY YEAR(Sold_Date) ORDER BY Sale_Year;"}, "result": "4 rows"},
            {"tool": "run_query", "args": {"sql": "SELECT COUNT(*) AS Null_Date_Sales FROM Sales WHERE Sold_Date IS NULL;"}, "result": "1 rows"},
        ],
        "answer": "מספר המכירות לפי שנה:\n\n* **2020:** 191 מכירות\n* **2021:** 1,232 מכירות\n* **2022:** 1,628 מכירות\n* **2023:** 1,503 מכירות",
        "final_sql": "SELECT\n    YEAR(Sold_Date) AS Sale_Year,\n    COUNT(*) AS Total_Sales\nFROM Sales\nGROUP BY YEAR(Sold_Date)\nORDER BY Sale_Year;",
        "columns": ["Sale_Year", "Total_Sales"],
        "rows": [(2020, 191), (2021, 1232), (2022, 1628), (2023, 1503)],
    },
    "מי שלושת המוכרים עם סך המכירות הגבוה ביותר?": {
        "model": "gemini-3.5-flash",
        "seconds": 219.7,
        "steps": [
            {"tool": "get_schema", "args": {}, "result": "schema (3,185 chars)"},
            {"tool": "run_query", "args": {"sql": "SELECT TOP 3 s.Seller_ID, sel.Seller_Name, SUM(s.Sold_Price) AS Total_Sales_Amount, COUNT(s.SaleID) AS Total_Sales_Count FROM Sales s JOIN Sellers sel ON s.Seller_ID = sel.Seller_ID GROUP BY s.Seller_ID, sel.Seller_Name ORDER BY Total_Sales_Amount DESC"}, "result": "3 rows"},
            {"tool": "run_query", "args": {"sql": "SELECT TOP 5 s.Seller_ID, sel.Seller_Name, SUM(CAST(s.Sold_Price AS BIGINT)) AS Total_Sales_Amount, COUNT(s.SaleID) AS Total_Sales_Count FROM Sales s JOIN Sellers sel ON s.Seller_ID = sel.Seller_ID GROUP BY s.Seller_ID, sel.Seller_Name ORDER BY Total_Sales_Amount DESC"}, "result": "5 rows"},
        ],
        "answer": "שלושת המוכרים עם סך המכירות הגבוה ביותר:\n\n1. **911r** – 22,139,480 דולר (155 מכירות)\n2. **Wob** – 12,823,551 דולר (74 מכירות)\n3. **1600veloce** – 11,535,716 דולר (104 מכירות)",
        "final_sql": "SELECT TOP 3\n    s.Seller_ID,\n    sel.Seller_Name,\n    SUM(CAST(s.Sold_Price AS BIGINT)) AS Total_Sales_Amount,\n    COUNT(s.SaleID) AS Total_Sales_Count\nFROM Sales s\nJOIN Sellers sel ON s.Seller_ID = sel.Seller_ID\nGROUP BY s.Seller_ID, sel.Seller_Name\nORDER BY Total_Sales_Amount DESC",
        "columns": ["Seller_Name", "Total_Sales_Amount", "Total_Sales_Count"],
        "rows": [("911r", 22139480, 155), ("Wob", 12823551, 74), ("1600veloce", 11535716, 104)],
    },
}
