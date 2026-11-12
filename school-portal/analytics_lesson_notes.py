"""Instructor drafts based on the supplied Foundation curriculum and proposed higher-stage lessons."""
FOUNDATION = [
    ('Build confidence in Excel and understand the analytics lifecycle.',
     'What data analytics is and why it matters; Excel workbooks, worksheets, rows, columns and ranges; data types, structured tables, sorting, filtering and freeze panes; ask, collect, prepare, analyze, visualize, share and act.',
     'Create and format a small sales dataset, convert it to an Excel Table, sort and filter records, and identify the questions the data can answer.',
     'Navigate Excel confidently and organize raw data in an analysis-ready structure.',
     'Prepare a clean workbook and document three business questions.'),
    ('Turn inconsistent raw data into a trustworthy dataset.',
     'Missing values, duplicates, errors and inconsistent formats; TRIM, CLEAN, PROPER, UPPER, LOWER and text-to-columns; Find and Replace, Remove Duplicates and Flash Fill; date, currency, number and percentage formatting; data validation and quality checks.',
     'Clean a deliberately messy customer and transaction dataset and create a data-quality checklist.',
     'Apply a repeatable cleaning process and explain each correction made.',
     'Submit the original data, cleaned data and a short cleaning log.'),
    ('Use formulas to calculate accurate business metrics.',
     'Relative, absolute and mixed references; SUM, AVERAGE, MIN, MAX, COUNT, COUNTA and ROUND; IF, IFS, AND, OR and IFERROR; SUMIF, SUMIFS, COUNTIF, COUNTIFS, AVERAGEIF and AVERAGEIFS; basic date and text calculations.',
     'Calculate revenue, cost, profit, margin, average transaction value and performance status for a sales dataset.',
     'Select the correct formula for common analytical questions and audit results for errors.',
     'Complete a formula challenge using at least eight functions.'),
    ('Connect tables and investigate patterns in business data.',
     'XLOOKUP and VLOOKUP; INDEX and MATCH foundations; UNIQUE, SORT and FILTER where supported; conditional formatting for trends, exceptions and risk flags; descriptive statistics and exploratory questions.',
     'Link a sales table to product and employee reference tables, then highlight unusual values and performance gaps.',
     'Combine related datasets and use exploration to identify meaningful patterns.',
     'Create an exception report with supporting calculations.'),
    ('Summarize large datasets quickly and convert totals into insights.',
     'Create and refresh PivotTables; group dates and categories; calculated fields and value display options; slicers, timelines and PivotCharts; writing insight statements.',
     'Analyze sales by month, region, product, salesperson and customer segment using PivotTables and charts.',
     'Build interactive summaries and explain what changed, why it matters and what action is recommended.',
     'Submit a one-page PivotTable analysis with three insights.'),
    ('Build a clear management dashboard that supports decision-making.',
     'Useful KPIs; dashboard layout, hierarchy, color and accessibility; charts for comparison, trend, composition and relationship; interactive filters and dynamic titles; executive summaries and recommendations.',
     'Create a dashboard displaying revenue, profit, transactions, trends, top products and regional performance.',
     'Design a professional dashboard and communicate findings to a non-technical audience.',
     'Present a five-minute dashboard walkthrough.'),
    ('Learn how analysts retrieve structured data from databases.',
     'Relational tables, rows, columns, keys and relationships; SELECT, FROM, WHERE, ORDER BY and LIMIT; DISTINCT, aliases and calculated columns; COUNT, SUM, AVG, MIN and MAX; GROUP BY and HAVING.',
     'Query a sample business database to find sales totals, customer activity and product performance.',
     'Write readable SQL queries that answer clearly stated business questions.',
     'Complete ten guided SQL query exercises.'),
    ('Combine multiple tables and produce richer analysis.',
     'INNER JOIN and LEFT JOIN; primary and foreign keys; CASE statements and conditional categories; subqueries and common table expression concepts; validation between SQL and Excel.',
     'Join customer, order and product tables to calculate revenue and identify inactive or high-value customers.',
     'Build multi-table queries and validate that totals remain accurate after joins.',
     'Create an SQL analysis report with queries and findings.'),
    ('Transform data and build a reliable reporting model.',
     'Power BI workflow and interface; Excel and CSV imports; Power Query cleaning and transformations; relationships, star schema and data types; calculated columns versus measures.',
     'Import a raw dataset, transform it in Power Query and create a basic star-schema model.',
     'Prepare data for reporting and explain the purpose of relationships in a model.',
     'Submit a Power BI file with documented transformation steps.'),
    ('Create interactive reports that track performance and reveal trends.',
     'DAX: SUM, COUNTROWS, DIVIDE and CALCULATE; KPI cards, tables, charts, slicers and drill-down; filter context and basic time analysis; report navigation and visual interactions; dashboard review and presentation.',
     'Build a two-page interactive report with KPI measures, trends, categories and geographic or regional analysis.',
     'Use measures and visuals to tell a coherent business story.',
     'Present the Power BI report and explain three recommended actions.'),
    ('Introduce a repeatable code-based workflow for larger or recurring analysis tasks.',
     'Python, Jupyter Notebook and the analyst workflow; variables, lists, dictionaries and basic control flow; pandas Series and DataFrames; read CSV, inspect, filter, sort, group and summarize; missing values, duplicates and data types; charts with pandas and matplotlib.',
     'Load a CSV file, clean the data, calculate grouped summaries and produce a simple visualization in Jupyter.',
     'Complete foundational analysis in Python and compare the workflow with Excel.',
     'Submit an annotated Jupyter Notebook with code, results and observations.'),
    ('Complete the full analytics lifecycle and present a professional solution.',
     'Define a problem and measurable questions; clean and validate data; analyze with appropriate tools and metrics; dashboard and findings; data privacy, ethics and secure handling; portfolio presentation, resume language and interview storytelling.',
     'Complete an end-to-end project using Excel, SQL, Power BI or Python and present it as a business case.',
     'Demonstrate practical analytical thinking, technical accuracy and clear communication.',
     'Submit the final project, dashboard, executive summary and five-to-seven-minute presentation.')
]
PROPOSED = {
    'intermediate':[
        ('Use readable multi-step SQL to investigate business questions.', 'CTEs; window functions; ranking and running totals; join cardinality; NULL handling and validation.', 'Produce a customer and sales analysis with reconciled totals and documented query checks.'),
        ('Build reliable reporting models and reusable measures.', 'Power Query transformations; star schemas; DAX filter context; date tables and time intelligence.', 'Deliver a Power BI model with tested measures and a month-over-month report.'),
        ('Create a repeatable exploratory analysis in Python.', 'pandas merges and grouped calculations; missing data; visualization; descriptive statistics and outliers.', 'Submit an annotated notebook explaining patterns, limitations and business implications.'),
        ('Deliver and maintain a business intelligence solution.', 'Requirements; KPI definitions; report design; refresh; row-level access; validation and handover.', 'Present a working BI project with a data dictionary, refresh plan and access-control checklist.')],
    'advanced':[
        ('Evaluate evidence using statistical inference.', 'Sampling; uncertainty; confidence intervals; hypothesis tests; experiment design and A/B test limitations.', 'Analyze an experiment and explain the result, assumptions and decision limits.'),
        ('Build and evaluate a forecasting or regression baseline.', 'Regression; time-based splits; baseline forecasts; error metrics; leakage and limitations.', 'Compare a model to a baseline using held-out data and explain business tradeoffs.'),
        ('Make analytical workflows reproducible and auditable.', 'Data ingestion and transformation; reusable pipelines; schema and quality checks; version control; documentation.', 'Deliver a versioned analytics pipeline with validation checks and reproducible outputs.'),
        ('Present a complete, evidence-based analytics solution.', 'Problem framing; pipeline quality; model evaluation; visualization; privacy; recommendations and handover.', 'Submit the reproducible capstone, evaluated analysis, dashboard and executive presentation.')]
}


def lesson_notes(stage,index):
    if stage=='foundation':
        focus,topics,lab,outcome,task=FOUNDATION[index]
        return f'FOCUS\n{focus}\n\nTOPICS\n{topics}\n\nLIVE LAB\n{lab}\n\nLEARNING OUTCOME\n{outcome}\n\nWEEKLY TASK\n{task}'
    objective,topics,task=PROPOSED[stage][index]
    prerequisite='Foundation or equivalent skills' if stage=='intermediate' else 'Intermediate or equivalent SQL, pandas and Power BI skills'
    return f'INSTRUCTOR DRAFT\nReview and tailor the lab and assessment before publishing.\n\nPREREQUISITES\n{prerequisite}\n\nOBJECTIVE\n{objective}\n\nTOPICS\n{topics}\n\nPRACTICAL DELIVERABLE\n{task}'
