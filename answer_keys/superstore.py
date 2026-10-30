"""Answer key for the Superstore sample (profile: superstore, SQLite).

16 questions with known answers. About half are traps - the questions where a query runs, returns a
plausible number, and is wrong:
  - order lines vs orders: one order has several rows (COUNT(*) is not the number of orders)
  - customers by ID: 804 customer IDs carry 800 names (counting names undercounts)
  - a ratio of sums vs an average of ratios (profit margin, average order value)
  - "at least one" questions that need DISTINCT orders, not lines
  - the US only, when the table also holds Canada

Each question says what to return, so there is one right answer. Months and years are numbers.
"""
DIALECT = "sqlite"

QUESTIONS = [
    {"id": 1, "trap": "order lines vs orders",
     "question": "כמה הזמנות היו בשנת 2025?",
     "expected_sql": "SELECT COUNT(DISTINCT Order_ID) FROM superstore WHERE strftime('%Y', Order_Date) = '2025'"},
    {"id": 2, "trap": "customers by ID",
     "question": "How many different customers are there in the data?",
     "expected_sql": "SELECT COUNT(DISTINCT Customer_ID) FROM superstore"},
    {"id": 3,
     "question": "מה סך המכירות בכל אזור? החזר את האזור ואת סך המכירות.",
     "expected_sql": "SELECT Region, SUM(Sales) FROM superstore GROUP BY Region"},
    {"id": 4, "trap": "ratio of sums",
     "question": "What is the profit margin of each category, as a percentage (total profit / total sales x 100)? "
                 "Return the category and the margin.",
     "expected_sql": "SELECT Category, 100.0 * SUM(Profit) / SUM(Sales) FROM superstore GROUP BY Category"},
    {"id": 5,
     "question": "אילו 3 תת-קטגוריות הכי מפסידות, כלומר עם הרווח הכולל הנמוך ביותר? החזר את שמות תת-הקטגוריות.",
     "expected_sql": "SELECT Sub_Category FROM superstore GROUP BY Sub_Category ORDER BY SUM(Profit) LIMIT 3"},
    {"id": 6, "trap": "per order, not per line",
     "question": "מה מספר הימים הממוצע מההזמנה ועד המשלוח בכל שיטת משלוח? כל הזמנה נספרת פעם אחת. "
                 "החזר את שיטת המשלוח ואת הממוצע.",
     "expected_sql": """
        SELECT Ship_Mode, AVG(days) FROM (
            SELECT DISTINCT Order_ID, Ship_Mode, julianday(Ship_Date) - julianday(Order_Date) AS days FROM superstore)
        GROUP BY Ship_Mode"""},
    {"id": 7, "trap": "US only",
     "question": "Which US state has the highest total sales? Return the state.",
     "expected_sql": """SELECT State_Province FROM superstore WHERE Country_Region = 'United States'
                        GROUP BY State_Province ORDER BY SUM(Sales) DESC LIMIT 1"""},
    {"id": 8,
     "question": "מי עשרת הלקוחות עם הרווח הכולל הגבוה ביותר? החזר את שמות הלקוחות.",
     "expected_sql": "SELECT Customer_Name FROM superstore GROUP BY Customer_ID ORDER BY SUM(Profit) DESC LIMIT 10"},
    {"id": 9,
     "question": "What were the total sales in each year? Return the year (as a number) and the sales.",
     "expected_sql": "SELECT CAST(strftime('%Y', Order_Date) AS INTEGER), SUM(Sales) FROM superstore GROUP BY 1"},
    {"id": 10, "trap": "average of orders, not of lines",
     "question": "מה היה הערך הממוצע של הזמנה בשנת 2024 (סך המכירות חלקי מספר ההזמנות)?",
     "expected_sql": """SELECT SUM(Sales) / COUNT(DISTINCT Order_ID) FROM superstore
                        WHERE strftime('%Y', Order_Date) = '2024'"""},
    {"id": 11, "trap": "products by name",
     "question": "How many products (by product name) lost money overall, with total profit below zero?",
     "expected_sql": """SELECT COUNT(*) FROM (SELECT Product_Name FROM superstore GROUP BY Product_Name
                        HAVING SUM(Profit) < 0)"""},
    {"id": 12,
     "question": "באיזה חודש בשנת 2025 היו הכי הרבה מכירות? החזר את מספר החודש (1 עד 12).",
     "expected_sql": """SELECT CAST(strftime('%m', Order_Date) AS INTEGER) FROM superstore
                        WHERE strftime('%Y', Order_Date) = '2025' GROUP BY 1 ORDER BY SUM(Sales) DESC LIMIT 1"""},
    {"id": 13,
     "question": "מה סך המכירות בקנדה?",
     "expected_sql": "SELECT SUM(Sales) FROM superstore WHERE Country_Region = 'Canada'"},
    {"id": 14,
     "question": "By what percentage did total sales grow from 2025 to 2026?",
     "expected_sql": """SELECT 100.0 * (SUM(CASE WHEN strftime('%Y', Order_Date) = '2026' THEN Sales END) /
                                SUM(CASE WHEN strftime('%Y', Order_Date) = '2025' THEN Sales END) - 1)
                        FROM superstore"""},
    {"id": 15, "trap": "at least one line",
     "question": "איזה אחוז מההזמנות כלל לפחות מוצר אחד בהנחה?",
     "expected_sql": """SELECT 100.0 * COUNT(DISTINCT CASE WHEN Discount > 0 THEN Order_ID END) / COUNT(DISTINCT Order_ID)
                        FROM superstore"""},
    {"id": 16, "trap": "customers by ID",
     "question": "How many customers ordered both in 2025 and in 2026?",
     "expected_sql": """SELECT COUNT(*) FROM (
            SELECT Customer_ID FROM superstore WHERE strftime('%Y', Order_Date) = '2025'
            INTERSECT SELECT Customer_ID FROM superstore WHERE strftime('%Y', Order_Date) = '2026')"""},
]
