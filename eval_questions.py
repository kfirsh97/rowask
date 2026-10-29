"""The answer key: 12 business questions, each with the query Kfir wrote for the original
HIT project (graded 100). The agent's answer counts as correct if ITS query returns the same
result as the expected query.

expected_sql returns only the columns that define the answer. The agent may return extra
columns (names, labels) - the checker looks for columns whose values match these.
Each question says explicitly whether it wants a count or a list, so there is one right answer.
"""

QUESTIONS = [
    {
        "id": 1,
        "question": "אילו מוכרים, לפי מזהה מוכר, היו בשנת 2022 עם מחיר מכירה ממוצע גבוה יותר "
                    "מהמחיר הממוצע של כל המכירות ב-2022? החזר את מזהה המוכר ואת הממוצע שלו.",
        "expected_sql": """
            WITH Sales2022 AS (SELECT Seller_ID, Sold_Price FROM Sales WHERE YEAR(Sold_Date) = 2022),
            AveragePerSeller AS (SELECT Seller_ID, AVG(Sold_Price) AS Average_Sale_Price FROM Sales2022 GROUP BY Seller_ID),
            OverallAverage AS (SELECT AVG(Sold_Price) AS Overall_Average_Price FROM Sales2022)
            SELECT aps.Seller_ID FROM AveragePerSeller aps, OverallAverage oa
            WHERE aps.Average_Sale_Price > oa.Overall_Average_Price
        """,
    },
    {
        "id": 2,
        "question": "אילו רכבי טורבו (המילה Turbo בכותרת) היו אצל מספר חד-ספרתי של בעלים קודמים "
                    "ונסעו בין 0 ל-20,000 מייל? החזר את מספר השלדה של כל רכב.",
        "expected_sql": """
            SELECT Chassis FROM CARS
            WHERE Title LIKE '%Turbo%' AND OWNERS LIKE '_Previous owners' AND Mileage BETWEEN 0 AND 20000
        """,
    },
    {
        "id": 3,
        "question": "מה סך הכנסות המכירות בכל אחת מהמדינות במערב ארצות הברית: קליפורניה, אורגון, וושינגטון, "
                    "נבדה, איידהו, מונטנה, ויומינג, יוטה, קולורדו, אלסקה והוואי? לפי מדינת המגורים של הלקוח.",
        "expected_sql": """
            SELECT z.State, SUM(Sold_Price) AS Total_Sales_Income
            FROM Customers c JOIN ZipCodes z ON c.ZipCodeID = z.ZipCodeID JOIN Sales sa ON c.ID_NUMBER = sa.ID_NUMBER
            WHERE z.State IN ('California','Oregon','Washington','Nevada','Idaho','Montana','Wyoming','Utah',
                              'Colorado','Alaska','Hawaii')
            GROUP BY z.State
        """,
    },
    {
        "id": 4,
        "question": "לפי חוק חדש, תדירות הטסט נקבעת לפי גיל הרכב (השנה הנוכחית פחות שנת הדגם): "
                    "20 ומעלה - כל חצי שנה, 10 עד 19 - כל שנה, 3 עד 9 - כל שנה וחצי, ומתחת ל-3 - כל שנתיים. "
                    "כמה רכבים יש בכל אחת מארבע הקבוצות?",
        "expected_sql": """
            SELECT COUNT(*) AS Cars FROM CARS
            GROUP BY CASE
                WHEN (YEAR(GETDATE()) - YEAR) >= 20 THEN 1
                WHEN (YEAR(GETDATE()) - YEAR) BETWEEN 10 AND 19 THEN 2
                WHEN (YEAR(GETDATE()) - YEAR) BETWEEN 3 AND 9 THEN 3
                ELSE 4 END
        """,
    },
    {
        "id": 5,
        "question": "אילו רכבים (מספר שלדה) עלו בסך כל השירותים שלהם יותר מהממוצע של סך עלות השירותים לרכב? "
                    "החזר את מספר השלדה ואת סך העלות.",
        "expected_sql": """
            SELECT Chassis, SUM(Service_Cost) AS Total_Service_Cost FROM Services GROUP BY Chassis
            HAVING SUM(Service_Cost) > (
                SELECT AVG(Total_Service_Cost) FROM (
                    SELECT Chassis, SUM(Service_Cost) AS Total_Service_Cost FROM Services GROUP BY Chassis
                ) AS Service_Avg)
        """,
    },
    {
        "id": 6,
        "question": "לכל רכב, מה השירות היקר ביותר שבוצע לו? החזר מספר שלדה, סוג השירות והעלות "
                    "(אם יש כמה שירותים באותה עלות מקסימלית, החזר את כולם).",
        "expected_sql": """
            SELECT s.Chassis, s.Service_Type, s.Service_Cost FROM Services s
            INNER JOIN (SELECT Chassis, MAX(Service_Cost) AS Max_Service_Cost FROM Services GROUP BY Chassis) m
              ON s.Chassis = m.Chassis AND s.Service_Cost = m.Max_Service_Cost
        """,
    },
    {
        "id": 7,
        "question": "בכל אחת מהמדינות במזרח ארצות הברית - מיין, ניו המפשייר, ורמונט, מסצ'וסטס, רוד איילנד, "
                    "קונטיקט, ניו יורק, ניו ג'רזי, פנסילבניה, דלאוור, מרילנד, וירג'יניה, מערב וירג'יניה, "
                    "צפון קרוליינה, דרום קרוליינה, ג'ורג'יה ופלורידה - כמה לקוחות שונים ביצעו לפחות רכישה אחת?",
        "expected_sql": """
            SELECT z.State, COUNT(DISTINCT c.ID_NUMBER) AS Customer_Count
            FROM Customers c JOIN ZipCodes z ON c.ZipCodeID = z.ZipCodeID JOIN Sales s ON c.ID_NUMBER = s.ID_NUMBER
            WHERE z.State IN ('Maine','New Hampshire','Vermont','Massachusetts','Rhode Island','Connecticut',
                'New York','New Jersey','Pennsylvania','Delaware','Maryland','Virginia','West Virginia',
                'North Carolina','South Carolina','Georgia','Florida')
            GROUP BY z.State
        """,
    },
    {
        "id": 8,
        "question": "כמה מוכרים לא ביצעו אף מכירה?",
        "expected_sql": """
            SELECT COUNT(*) AS Sellers_Without_Sales FROM (
                SELECT Seller_ID FROM Sellers EXCEPT SELECT Seller_ID FROM Sales) x
        """,
    },
    {
        "id": 9,
        "question": "אילו סוחרים עובדים עם חברת ביטוח מספר 1 או עם חברת ביטוח מספר 2? החזר את מזהה המוכר.",
        "expected_sql": """
            SELECT s.Seller_ID FROM Sellers s JOIN DealersWithInsurances d ON s.Seller_ID = d.Seller_ID WHERE d.InsuranceID = 1
            UNION
            SELECT s.Seller_ID FROM Sellers s JOIN DealersWithInsurances d ON s.Seller_ID = d.Seller_ID WHERE d.InsuranceID = 2
        """,
    },
    {
        "id": 10,
        "question": "אילו רכבים נמכרו, מבוטחים בביטוח מקיף, וגילם (השנה הנוכחית פחות שנת הדגם) קטן מ-4? "
                    "החזר את מספר הפוליסה ומספר השלדה.",
        "expected_sql": """
            SELECT p.PolicyID, c.Chassis FROM Policy p
            JOIN Sales s ON p.Chassis = s.Chassis JOIN Customers cu ON s.ID_NUMBER = cu.ID_NUMBER
            JOIN CARS c ON p.Chassis = c.Chassis
            WHERE p.Insurance_Type = 'Comprehensive'
            INTERSECT
            SELECT p.PolicyID, c.Chassis FROM Policy p
            JOIN Sales s ON p.Chassis = s.Chassis JOIN Customers cu ON s.ID_NUMBER = cu.ID_NUMBER
            JOIN CARS c ON p.Chassis = c.Chassis
            WHERE (YEAR(GETDATE()) - c.YEAR) < 4
        """,
    },
    {
        "id": 11,
        "question": "מה היו הכנסות המכירות בכל שנה?",
        "expected_sql": """
            SELECT YEAR(Sold_Date) AS Sale_Year, SUM(Sold_Price) AS Yearly_Revenue FROM Sales GROUP BY YEAR(Sold_Date)
        """,
    },
    {
        "id": 12,
        "question": "אילו מוכרים (מזהה מוכר) מכרו בסך הכול ביותר ממיליון דולר? החזר את המזהה ואת סך המכירות.",
        "expected_sql": """
            SELECT Seller_ID, SUM(Sold_Price) AS Total_Sales FROM Sales GROUP BY Seller_ID
            HAVING SUM(Sold_Price) > 1000000
        """,
    },
]
