-- יצירת מסד הנתונים
CREATE DATABASE Porsche;
GO
--בחירת מסד הנתונים
USE Porsche;
GO

-- טבלת עובדים
CREATE TABLE Employees
(
    ID_Employee INT NOT NULL PRIMARY KEY,
    Employee_First_Name VARCHAR(20) NOT NULL,
    Employee_Last_Name VARCHAR(20),
    Employee_Email VARCHAR(100) NOT NULL UNIQUE,
    Employee_BirthDate DATE,
    Employee_Gender VARCHAR(6) CHECK (Employee_Gender IN ('Male', 'Female')),
    Employee_Phone VARCHAR(10) UNIQUE,
    Hiring_Date DATE DEFAULT GETDATE()
);

-- טבלת מנהלים
CREATE TABLE Managers
(
    ID_Employee INT NOT NULL PRIMARY KEY REFERENCES Employees(ID_Employee),
    Global_Salary DECIMAL(10,2),
);


-- טבלת עובדים זוטרים
CREATE TABLE Workers
(
    ID_Employee INT NOT NULL PRIMARY KEY REFERENCES Employees(ID_Employee),
    Hourly_Rate DECIMAL(6,2) CHECK (Hourly_Rate > 0),
    Monthly_Hours DECIMAL(5,2) CHECK (Monthly_Hours > 0),
    Direct_Manager_ID INT NULL, FOREIGN KEY(Direct_Manager_ID) REFERENCES Managers(ID_Employee)
    ON DELETE SET NULL
);

-- טבלת מחלקות
CREATE TABLE Departments
(
    DepartmentID INT PRIMARY KEY NOT NULL,
    Department_Name VARCHAR(40),
    ManagerID INT NULL,
    FOREIGN KEY (ManagerID) REFERENCES Managers(ID_Employee) ON DELETE SET NULL
);


-- טבלת בני זוג
CREATE TABLE Spouses
(
    Spouse_ID INT PRIMARY KEY,
    Employee_ID INT NOT NULL REFERENCES Employees(ID_Employee) ON DELETE CASCADE,
    Spouse_Name VARCHAR(50) NOT NULL,
    Spouse_BirthDate DATE
);


-- טבלת מיקודים
CREATE TABLE ZipCodes
(
    ZipCodeID INT PRIMARY KEY NOT NULL,
    City VARCHAR(40),
    State VARCHAR(40)
);

-- טבלת לקוחות
CREATE TABLE Customers
(
    ID_NUMBER VARCHAR(9) NOT NULL PRIMARY KEY,
    Customer_FULL_NAME VARCHAR(40) NOT NULL,
    FOLLOW INT CHECK (FOLLOW >= 0),
    COMMENTS INT CHECK (COMMENTS >= 0),
    ReferredByCustomerID VARCHAR(9) NULL,
    FOREIGN KEY (ReferredByCustomerID) REFERENCES Customers(ID_NUMBER),
    Customer_Gender VARCHAR(6) CHECK (Customer_Gender IN ('Male', 'Female')),
    ZipCodeID INT NOT NULL REFERENCES ZipCodes(ZipCodeID),
);

CREATE TABLE CARS
(
    Chassis VARCHAR(50) PRIMARY KEY NOT NULL,
    Title VARCHAR(200),
    YEAR INT,
    Engine VARCHAR(200),
    Transmission VARCHAR(200),
    Color VARCHAR(100),
    Upholstery VARCHAR(200),
    Audio_System VARCHAR(12),
    Slip_Limiter VARCHAR(3) CHECK (Slip_Limiter IN ('Yes', 'No')),
    Air_Conditioning VARCHAR(3) CHECK (Air_Conditioning IN ('Yes', 'No')),
    Cruise_Control VARCHAR(3) CHECK (Cruise_Control IN ('Yes', 'No')),
    Sunroof VARCHAR(3) CHECK (Sunroof IN ('Yes', 'No')),
    Rear_Spoiler VARCHAR(3) CHECK (Rear_Spoiler IN ('Yes', 'No')),
    OWNERS VARCHAR(100) NOT NULL,
    Accident VARCHAR(100),
    Mileage INT DEFAULT 0
);

-- טבלת מוכרים
CREATE TABLE Sellers
(
    Seller_ID INT PRIMARY KEY NOT NULL,
    Seller_Name VARCHAR(255) NOT NULL,
    Dealer_Or_PrivateParty VARCHAR(14)
);

-- טבלת סוכנויות
CREATE TABLE Dealer_Sellers
(
    Seller_ID INT NOT NULL PRIMARY KEY REFERENCES Sellers(Seller_ID),
    Manager_Name VARCHAR(100),
    Manager_Phone_Number VARCHAR(20) UNIQUE
);

-- טבלת מוכרים פרטיים
CREATE TABLE Private_Seller
(
    Seller_ID INT NOT NULL PRIMARY KEY REFERENCES Sellers(Seller_ID),
    Seller_Gender VARCHAR(6) CHECK (Seller_Gender IN ('Male', 'Female')),
    Seller_First_Name VARCHAR(100),
    Seller_Last_Name VARCHAR(100),
    Seller_Phone_Number VARCHAR(20) UNIQUE
);

-- טבלת חברות ביטוח
CREATE TABLE Insurance
(
    InsuranceID INT PRIMARY KEY NOT NULL,
    Company_Name VARCHAR(15) NOT NULL,
    Contact_Person VARCHAR(100),
    Contact_Person_Phone_Num VARCHAR(20) UNIQUE
);

--טבלת עובדים שאחראים על מוכרים
CREATE TABLE Seller_Managers_History
 (
    Seller_ID INT NOT NULL REFERENCES Sellers(Seller_ID),
    Responsible_Worker_ID INT NOT NULL REFERENCES Workers(ID_Employee),
    Start_Date DATE NOT NULL DEFAULT GETDATE(),
    End_Date DATE NULL,
    PRIMARY KEY (Seller_ID, Responsible_Worker_ID, Start_Date)
);

-- קשר בין סוכנויות לחברות ביטוח
CREATE TABLE DealersWithInsurances
(
    InsuranceID INT NOT NULL REFERENCES Insurance(InsuranceID),
    Seller_ID INT NOT NULL REFERENCES Dealer_Sellers(Seller_ID),
    PRIMARY KEY (InsuranceID, Seller_ID)
);


-- טבלת מכירות
CREATE TABLE Sales
(
    SaleID INT PRIMARY KEY NOT NULL,
    Chassis VARCHAR(50) NOT NULL REFERENCES CARS(Chassis),
    ID_NUMBER VARCHAR(9) NOT NULL REFERENCES Customers(ID_NUMBER),
    Seller_ID INT NOT NULL REFERENCES Sellers(Seller_ID),
    Views INT,
    Followers INT,
    Comments INT,
    Sold_Price INT,
    Sold_Date DATE DEFAULT GETDATE()
);

-- טבלת פוליסות ביטוח
CREATE TABLE Policy
(
    PolicyID BIGINT IDENTITY(792832, 827) PRIMARY KEY NOT NULL,
    Chassis VARCHAR(50) NOT NULL REFERENCES CARS(Chassis),
    Insurance_Type VARCHAR(40) NOT NULL,
    Special_Conditions VARCHAR(40) DEFAULT 'No Special Condition'
);

-- טבלת שירותים לרכב
CREATE TABLE Services
(
    ServiceID BIGINT IDENTITY(91384, 3183) PRIMARY KEY NOT NULL,
    Chassis VARCHAR(50) NOT NULL REFERENCES CARS(Chassis),
    Service_Type VARCHAR(30),
    Service_Date DATE DEFAULT GETDATE(),
    Service_Cost INT CHECK (Service_Cost BETWEEN 0 AND 3000),
    Assigned_Responsible INT REFERENCES Workers(ID_Employee)
);


-- טבלת תשלומים
CREATE TABLE Payments
(
    PaymentID INT IDENTITY (424591, 938) PRIMARY KEY NOT NULL,
    Chassis VARCHAR(50) NOT NULL REFERENCES CARS(Chassis),
    ID_NUMBER VARCHAR(9) NOT NULL REFERENCES Customers(ID_NUMBER),
    Amount decimal(10,2) CHECK (Amount >= 0),
    Payment_Method VARCHAR(20) CHECK (Payment_Method IN ('Financing', 'Credit Card', 'Bank Transfer', 'Cash')),
    Seller_Commission DECIMAL(10,2),
    Assigned_Responsible INT REFERENCES Workers(ID_Employee)
);

